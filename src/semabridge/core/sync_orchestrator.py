"""
Sync Orchestrator.

Provides a robust, dependency-aware multi-model sync pipeline
that validates ALL models BEFORE deployment, orders by dependency,
and produces a comprehensive report.

The orchestrator wraps the existing ``SnowflakeEmitter`` and
``_sync_single_fabric_model`` plumbing with three additional phases:

    Phase 0 — Pre-Sync Validation:
        Validates every discovered model against the 4-tier validator and
        classifies each as VALID, WARN, or BLOCKED.

    Phase 1 — Dependency Ordering:
        Sorts models so dimensions are deployed before facts, and facts
        before derived views / semantic views.

    Phase 2 — Resilient Deployment:
        Deploys each model in order; retries on transient errors;
        skips permanently invalid models and continues.

    Phase 3 — Sync Report:
        Produces a structured report with per-model status, errors,
        and suggested fixes.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# =========================================================================
# Report Models
# =========================================================================


class ModelStatus(str, Enum):
    """Status of a single model in the sync batch."""
    PENDING = "pending"
    VALIDATING = "validating"
    VALID = "valid"
    BLOCKED = "blocked"      # Failed pre-validation → skip deployment
    DEPLOYING = "deploying"
    SUCCESS = "success"
    FAILED = "failed"        # Deployment error
    SKIPPED = "skipped"      # Upstream dependency failed


@dataclass
class ModelIssue:
    """A single issue found during validation or deployment."""
    severity: str            # "ERROR", "WARNING", "INFO"
    category: str            # "PK", "Identifier", "Relationship", "Schema", "Runtime"
    message: str
    suggested_fix: str = ""


@dataclass
class ModelResult:
    """Per-model outcome in a sync batch."""
    model_name: str
    model_id: str
    status: ModelStatus = ModelStatus.PENDING
    issues: List[ModelIssue] = field(default_factory=list)
    deploy_duration_ms: int = 0
    error_message: str = ""
    classification: str = ""   # "dimension", "fact", "derived", "unknown"

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)

    def add_issue(
        self,
        severity: str,
        category: str,
        message: str,
        suggested_fix: str = "",
    ) -> None:
        self.issues.append(ModelIssue(severity, category, message, suggested_fix))


@dataclass
class SyncReport:
    """Complete report for a multi-model sync run."""

    batch_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    start_time: float = 0.0
    end_time: float = 0.0
    model_results: List[ModelResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.model_results)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.model_results if r.status == ModelStatus.SUCCESS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.model_results if r.status == ModelStatus.FAILED)

    @property
    def blocked(self) -> int:
        return sum(1 for r in self.model_results if r.status == ModelStatus.BLOCKED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.model_results if r.status == ModelStatus.SKIPPED)

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time if self.end_time else 0.0

    @property
    def all_succeeded(self) -> bool:
        return self.failed == 0 and self.blocked == 0

    def get_result(self, model_name: str) -> Optional[ModelResult]:
        """Lookup result by model name."""
        for r in self.model_results:
            if r.model_name == model_name:
                return r
        return None

    def format_summary(self) -> str:
        """Human-readable summary string."""
        lines = [
            f"Sync Report (batch: {self.batch_id})",
            f"  Duration: {self.duration_seconds:.1f}s",
            f"  Total: {self.total} | Success: {self.succeeded} | "
            f"Failed: {self.failed} | Blocked: {self.blocked} | "
            f"Skipped: {self.skipped}",
        ]
        if self.failed > 0 or self.blocked > 0:
            lines.append("")
            lines.append("  Failed/Blocked models:")
            for r in self.model_results:
                if r.status in (ModelStatus.FAILED, ModelStatus.BLOCKED):
                    reason = r.error_message or "; ".join(
                        i.message for i in r.issues if i.severity == "ERROR"
                    )
                    lines.append(f"    ✗ {r.model_name}: {reason[:120]}")
                    for issue in r.issues:
                        if issue.suggested_fix:
                            lines.append(f"      → Fix: {issue.suggested_fix}")
        return "\n".join(lines)


# =========================================================================
# Dependency Ordering
# =========================================================================


def _classify_dataset(ds: Any) -> str:
    """Classify a dataset as dimension, fact, or derived.

    Args:
        ds: An SML or OSI dataset object.

    Returns:
        Classification string: "dimension", "fact", or "derived".
    """
    name_upper = (ds.unique_name or "").upper()
    is_fact = getattr(ds, "is_fact", False)

    if is_fact:
        return "fact"
    if name_upper.startswith("DIM_") or name_upper.startswith("D_"):
        return "dimension"
    # Check if it's a derived/bridge table
    if name_upper.startswith("BRIDGE_") or name_upper.startswith("AGG_"):
        return "derived"
    return "dimension"  # Default to dimension for non-fact tables


def _classify_model(model: Any) -> str:
    """Classify the entire model based on its constituent datasets.

    A model containing only dimension tables → "dimension"
    A model containing at least one fact table → "fact"
    Otherwise, default to "fact" (safest ordering).

    Args:
        model: An SML or OSI model.

    Returns:
        Classification string.
    """
    datasets = getattr(model, "datasets", [])
    if not datasets:
        return "unknown"
    has_fact = any(getattr(ds, "is_fact", False) for ds in datasets)
    if has_fact:
        return "fact"
    return "dimension"


# Deploy order: dimensions first, then facts, then derived views
_DEPLOY_ORDER = {"dimension": 0, "fact": 1, "derived": 2, "unknown": 3}


def order_models_by_dependency(
    models: List[Dict[str, Any]],
    osi_models: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Sort models by deployment dependency order.

    Dimensions are deployed before facts, and facts before derived views.
    Within each category, models are kept in discovery order.

    Args:
        models: List of model dicts with at least "id" and "name".
        osi_models: Optional mapping of model_id → OSI/SML model for
            classification. If not provided, classification uses name heuristics.

    Returns:
        Sorted list of model dicts.
    """
    def _sort_key(model_dict: Dict[str, Any]) -> Tuple[int, str]:
        m_id = model_dict.get("id", "")
        m_name = model_dict.get("name", "")

        if osi_models and m_id in osi_models:
            classification = _classify_model(osi_models[m_id])
        else:
            # Name-based heuristic fallback
            name_upper = m_name.upper()
            if name_upper.startswith("DIM") or "DIMENSION" in name_upper:
                classification = "dimension"
            elif name_upper.startswith("FACT") or "FACT" in name_upper:
                classification = "fact"
            else:
                classification = "unknown"

        return (_DEPLOY_ORDER.get(classification, 3), m_name)

    return sorted(models, key=_sort_key)


# =========================================================================
# Pre-Sync Validation (Phase 0)
# =========================================================================


def validate_model_for_sync(
    model: Any,
    sf_behavior: Any,
    id_sanitizer: Any,
) -> ModelResult:
    """Validate a single model before deployment.

    Uses the GlobalValidator (6-tier) when available, falling back to
    the legacy 4-tier validator. Catches ALL exceptions so a single model
    validation failure never crashes the batch.

    Args:
        model: SML or OSI model.
        sf_behavior: ``SnowflakeBehavior`` configuration.
        id_sanitizer: ``IdentifierSanitizer`` instance.

    Returns:
        ``ModelResult`` with issues populated.
    """
    model_name = getattr(model, "unique_name", None) or getattr(model, "label", "unknown")
    result = ModelResult(
        model_name=model_name,
        model_id=getattr(model, "unique_name", ""),
        classification=_classify_model(model),
    )

    try:
        # Prefer GlobalValidator (6-tier) over legacy 4-tier
        try:
            from semabridge.core.validation.global_validator import GlobalValidator

            validator = GlobalValidator(
                id_sanitizer=id_sanitizer,
                sf_behavior=sf_behavior,
            )
            report = validator.validate(model, halt_on_error=False)

            for issue in report.issues:
                result.add_issue(
                    severity=issue.severity,
                    category=issue.category,
                    message=f"{issue.object_name}: {issue.message}",
                    suggested_fix=issue.suggested_fix,
                )

            if report.has_errors:
                result.status = ModelStatus.BLOCKED
                result.error_message = f"{report.error_count} blocking errors found"
            else:
                result.status = ModelStatus.VALID

        except ImportError:
            # Fall back to legacy 4-tier validator
            from semabridge.core.validation.validate_semantic_model import (
                PreDeploymentReport,
                _validate_tier1_schema,
                _validate_tier2_identifiers,
                _validate_tier3_primary_keys,
                _validate_tier4_relationships,
            )

            report = PreDeploymentReport()
            _validate_tier1_schema(model, report)
            _validate_tier2_identifiers(model, id_sanitizer, report)
            _validate_tier3_primary_keys(model, sf_behavior, id_sanitizer, report)
            _validate_tier4_relationships(model, id_sanitizer, report)

            for issue in report.issues:
                fix = ""
                if "primary key" in issue.message.lower():
                    fix = "Add is_key=True to a column, or set pk_resolution_mode=permissive"
                elif "not found in physical" in issue.message.lower():
                    fix = "Verify column exists in Snowflake table or mark as calculated"
                elif "does not exist" in issue.message.lower():
                    fix = "Check that the referenced dataset/table is defined"

                result.add_issue(
                    severity=issue.severity,
                    category=issue.category,
                    message=f"{issue.object_name}: {issue.message}",
                    suggested_fix=fix,
                )

            if report.has_errors:
                result.status = ModelStatus.BLOCKED
                result.error_message = report.summary()
            else:
                result.status = ModelStatus.VALID

    except Exception as e:
        # Validation itself crashed — log but don't block deployment
        # (the model might still deploy successfully).
        logger.warning(
            f"Pre-sync validation crashed for '{model_name}': {e}. "
            f"Model will be attempted anyway."
        )
        result.status = ModelStatus.VALID
        result.add_issue(
            severity="WARNING",
            category="Validation",
            message=f"Validation error: {e}",
            suggested_fix="Check model structure manually",
        )

    return result


# =========================================================================
# Suggested Fix Generator
# =========================================================================


def suggest_fix_for_error(error_message: str) -> str:
    """Generate a human-readable fix suggestion from an error message.

    Uses the DeploymentReport's suggest_fix engine when available,
    falls back to a local pattern matcher.

    Args:
        error_message: The raw error string from deployment.

    Returns:
        A suggested fix string.
    """
    try:
        from semabridge.utils.error_reporter import suggest_fix
        fix = suggest_fix(error_message)
        if fix:
            return fix
    except ImportError:
        pass

    # Local fallback
    msg = error_message.lower()
    if "invalid identifier" in msg:
        return (
            "A column referenced in the semantic view does not exist in the "
            "source table. Check for casing mismatches or calculated columns "
            "that need to be excluded."
        )
    if "primary key" in msg or "pk" in msg:
        return (
            "Set pk_resolution_mode=permissive in behavior.yaml, or mark "
            "a column as is_key=True in the model definition."
        )
    if "does not exist" in msg and "table" in msg:
        return (
            "Source table is missing in Snowflake. Enable "
            "create_missing_tables=true in behavior.yaml."
        )
    if "timeout" in msg or "semaphore" in msg or "load shedding" in msg:
        return (
            "Snowflake warehouse is overloaded. Increase warehouse size "
            "or reduce concurrent workers."
        )
    if "not authorized" in msg or "permission" in msg:
        return (
            "Check Snowflake RBAC grants: USAGE on database/schema, "
            "CREATE TABLE, CREATE SEMANTIC VIEW."
        )
    if "attributeerror" in msg:
        return (
            "Internal emitter bug — a required attribute was not initialized. "
            "Check model structure for empty or None values."
        )
    return "Check Snowflake logs and model definition for details."
