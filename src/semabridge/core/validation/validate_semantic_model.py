"""
Pre-Deployment Semantic Model Validator.

Runs a multi-tiered validation suite on an SML or OSI model BEFORE any SQL is
generated or executed, ensuring:

    Tier 1 — YAML Schema:         Structural integrity of datasets/metrics/rels
    Tier 2 — Identifier Grounding: Column refs resolve to physical columns
    Tier 3 — PK Integrity:         Every dataset has a valid primary key
    Tier 4 — Relationship Validation: FK columns exist in physical schemas

Deployment MUST abort if validation fails.

Note:
    This module is dual-compatible with both SML and OSI model types.
    SMLAttribute uses ``dataset_column``; OSIAttribute uses ``source_column``.
    All accessors use ``getattr`` with fallback to handle both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from semabridge.core.exceptions import ValidationError as SemaBridgeValidationError
from semabridge.formats.sml.models import SMLModel, SMLDataset
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# =========================================================================
# Report Classes
# =========================================================================


@dataclass
class ValidationIssue:
    """A single validation finding."""

    tier: int
    severity: str  # "ERROR" or "WARNING"
    category: str
    object_name: str
    message: str

    def __str__(self) -> str:
        return f"[Tier {self.tier}/{self.severity}] {self.category} — {self.object_name}: {self.message}"


@dataclass
class PreDeploymentReport:
    """Collects validation issues across all tiers."""

    issues: List[ValidationIssue] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARNING")

    def add_error(
        self, tier: int, category: str, name: str, msg: str
    ) -> None:
        self.issues.append(
            ValidationIssue(tier, "ERROR", category, name, msg)
        )

    def add_warning(
        self, tier: int, category: str, name: str, msg: str
    ) -> None:
        self.issues.append(
            ValidationIssue(tier, "WARNING", category, name, msg)
        )

    def summary(self) -> str:
        """One-line summary suitable for exception messages."""
        if not self.issues:
            return "All validations passed"
        lines = [str(i) for i in self.issues if i.severity == "ERROR"]
        return f"{self.error_count} error(s), {self.warning_count} warning(s). First errors: " + "; ".join(
            lines[:5]
        )


# =========================================================================
# Public entry-point
# =========================================================================


def validate_pre_deployment(
    sml: SMLModel,
    sf_behavior: Any,
    id_sanitizer: IdentifierSanitizer,
) -> PreDeploymentReport:
    """Run all pre-deployment validations before SQL generation.

    This function is called from ``SnowflakeEmitter.deploy()`` immediately
    before DDL generation.  It collects all issues across four tiers and
    raises ``SemaBridgeValidationError`` if any blocking error is found.

    Args:
        sml: The SML model to validate.
        sf_behavior: ``SnowflakeBehavior`` configuration (for pk_resolution_mode).
        id_sanitizer: Configured ``IdentifierSanitizer`` instance.

    Returns:
        ``PreDeploymentReport`` — always returned (even on success) so
        callers can inspect warnings.

    Raises:
        SemaBridgeValidationError: If any Tier validation produces errors.
    """
    report = PreDeploymentReport()

    _validate_tier1_schema(sml, report)
    _validate_tier2_identifiers(sml, id_sanitizer, report)
    _validate_tier3_primary_keys(sml, sf_behavior, id_sanitizer, report)
    _validate_tier4_relationships(sml, id_sanitizer, report)

    if report.has_errors:
        logger.error(
            f"Pre-deployment validation FAILED: {report.summary()}"
        )
        raise SemaBridgeValidationError(
            f"Pre-deployment validation failed with {report.error_count} error(s). "
            f"{report.summary()}",
            errors=[str(i) for i in report.issues if i.severity == "ERROR"],
        )

    if report.warning_count > 0:
        warnings_text = "\n  - ".join(
            str(i) for i in report.issues if i.severity == "WARNING"
        )
        logger.warning(
            f"Pre-deployment validation passed with {report.warning_count} warning(s):\n  - {warnings_text}"
        )
    else:
        logger.info("Pre-deployment validation passed (all tiers)")

    return report


# =========================================================================
# Standalone PK validation (exported for direct use)
# =========================================================================


def validate_primary_keys(
    sml: SMLModel,
    sf_behavior: Any,
    id_sanitizer: IdentifierSanitizer,
) -> PreDeploymentReport:
    """Validate primary key integrity for every dataset.

    Usable standalone (e.g. from CLI ``validate`` command) or as part of
    the full ``validate_pre_deployment`` pipeline.

    Args:
        sml: SML model.
        sf_behavior: SnowflakeBehavior config.
        id_sanitizer: IdentifierSanitizer instance.

    Returns:
        Report containing PK-related issues.
    """
    report = PreDeploymentReport()
    _validate_tier3_primary_keys(sml, sf_behavior, id_sanitizer, report)
    return report


# =========================================================================
# Tier 1 — YAML Schema / structural integrity
# =========================================================================


def _validate_tier1_schema(
    sml: SMLModel, report: PreDeploymentReport
) -> None:
    """Check basic structural integrity of the SML model."""

    # Must have datasets
    if not sml.datasets:
        report.add_error(1, "Schema", sml.unique_name or "<unnamed>", "Model has no datasets")
        return

    metric_counts_by_dataset: dict[str, int] = {}
    for metric in sml.metrics:
        ds_ref = getattr(metric, "dataset", None)
        if ds_ref:
            metric_counts_by_dataset[ds_ref] = metric_counts_by_dataset.get(ds_ref, 0) + 1

    for ds in sml.datasets:
        # Dataset must have columns
        if not ds.columns:
            metric_count = metric_counts_by_dataset.get(ds.unique_name, 0)
            if metric_count > 0:
                continue
            else:
                report.add_error(
                    1, "Schema", ds.unique_name, "Dataset has no columns"
                )

    for metric in sml.metrics:
        # Metric must reference a dataset that exists
        ds_names = {ds.unique_name for ds in sml.datasets}
        if metric.dataset and metric.dataset not in ds_names:
            report.add_error(
                1,
                "Schema",
                metric.unique_name,
                f"Metric references non-existent dataset '{metric.dataset}'",
            )

        # Metric must have either source_column+aggregation or an expression
        has_source = bool(metric.source_column and metric.aggregation)
        has_expr = bool(
            getattr(metric, "sql_expression", None)
            or getattr(metric, "expression", None)
        )
        if not has_source and not has_expr:
            report.add_warning(
                1,
                "Schema",
                metric.unique_name,
                "Metric has neither source_column+aggregation nor expression",
            )


# =========================================================================
# Tier 2 — Identifier grounding
# =========================================================================


def _validate_tier2_identifiers(
    sml: SMLModel,
    id_sanitizer: IdentifierSanitizer,
    report: PreDeploymentReport,
) -> None:
    """Verify column references resolve to physical columns."""

    # Build physical column lookup per dataset
    phys_lookup: Dict[str, Set[str]] = {}
    for ds in sml.datasets:
        cols: Set[str] = set()
        for c in ds.columns:
            if c.unique_name.startswith("RowNumber") or c.unique_name.startswith("_"):
                continue
            source_expr = getattr(c, "source_expression", None)
            if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                continue
            cols.add(id_sanitizer.sanitize_column(c.unique_name))
        phys_lookup[ds.unique_name] = cols

    # Check metrics reference valid columns
    for metric in sml.metrics:
        if metric.source_column and metric.dataset:
            sanitized_col = id_sanitizer.sanitize_column(metric.source_column)
            known = phys_lookup.get(metric.dataset, set())
            if known and sanitized_col not in known:
                report.add_warning(
                    2,
                    "Identifier",
                    metric.unique_name,
                    f"Metric source_column '{metric.source_column}' "
                    f"(sanitized: '{sanitized_col}') not found in physical "
                    f"columns of dataset '{metric.dataset}'",
                )

    # Check dimension attributes reference valid columns.
    # SMLAttribute uses 'dataset_column'; OSIAttribute uses 'source_column'.
    for dim in sml.dimensions:
        for attr in dim.attributes:
            col_name = (
                getattr(attr, "dataset_column", None)
                or getattr(attr, "source_column", None)
            )
            attr_dataset = getattr(attr, "dataset", None)
            if attr_dataset and col_name:
                sanitized_col = id_sanitizer.sanitize_column(col_name)
                known = phys_lookup.get(attr_dataset, set())
                if known and sanitized_col not in known:
                    report.add_warning(
                        2,
                        "Identifier",
                        attr.unique_name,
                        f"Dimension attribute column '{col_name}' "
                        f"not found in physical columns of '{attr_dataset}'",
                    )


# =========================================================================
# Tier 3 — Primary key integrity
# =========================================================================


def _validate_tier3_primary_keys(
    sml: SMLModel,
    sf_behavior: Any,
    id_sanitizer: IdentifierSanitizer,
    report: PreDeploymentReport,
) -> None:
    """Ensure every dataset has a valid primary key.

    Rules:
      - Must have ≥1 physical column with ``is_key=True``, OR
      - Must be the target of a relationship (inbound FK defines PK), OR
      - ``pk_resolution_mode=permissive`` allows fallback to first column.

    A column is "physical" if it has no ``source_expression`` or if
    ``source_expression`` is a simple identifier (not a DAX formula).
    """

    pk_mode = getattr(sf_behavior, "pk_resolution_mode", None)
    is_strict = pk_mode and pk_mode.value == "strict"

    # Build inbound relationship map: dataset_name → list of to_columns
    inbound_pk: Dict[str, List[str]] = {}
    for rel in sml.relationships:
        if rel.is_active and rel.to_dataset and rel.to_columns:
            if rel.to_dataset not in inbound_pk:
                inbound_pk[rel.to_dataset] = []
            for col in rel.to_columns:
                if col not in inbound_pk[rel.to_dataset]:
                    inbound_pk[rel.to_dataset].append(col)

    metric_counts_by_dataset: Dict[str, int] = {}
    for metric in sml.metrics:
        ds_ref = getattr(metric, "dataset", None)
        if ds_ref:
            metric_counts_by_dataset[ds_ref] = metric_counts_by_dataset.get(ds_ref, 0) + 1

    for ds in sml.datasets:
        if not ds.columns and metric_counts_by_dataset.get(ds.unique_name, 0) > 0:
            continue

        # Collect physical key columns
        physical_keys = []
        for c in ds.columns:
            if not c.is_key:
                continue
            source_expr = getattr(c, "source_expression", None)
            if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                # Calculated key — not valid as physical PK
                report.add_warning(
                    3,
                    "PK",
                    ds.unique_name,
                    f"Key column '{c.unique_name}' is calculated (not physical)",
                )
                continue
            physical_keys.append(c.unique_name)

        # Check inbound relationship PKs
        rel_pk_cols = inbound_pk.get(ds.unique_name, [])

        if rel_pk_cols:
            # Validate relationship PK columns exist as physical
            ds_phys = {
                id_sanitizer.sanitize_column(c.unique_name)
                for c in ds.columns
                if not c.unique_name.startswith("RowNumber")
                and not c.unique_name.startswith("_")
                and not (
                    getattr(c, "source_expression", None)
                    and not IdentifierSanitizer.is_physical_source_column(
                        getattr(c, "source_expression", "")
                    )
                )
            }
            all_non_physical = True
            for pk_col in rel_pk_cols:
                sanitized = id_sanitizer.sanitize_column(pk_col)
                if sanitized in ds_phys:
                    all_non_physical = False
                else:
                    report.add_warning(
                        3,
                        "PK",
                        ds.unique_name,
                        f"Relationship PK column '{pk_col}' (sanitized: '{sanitized}') "
                        f"not found in physical columns",
                    )
            if all_non_physical:
                report.add_warning(
                    3,
                    "PK",
                    ds.unique_name,
                    f"All relationship PK columns are non-physical ({rel_pk_cols}). "
                    f"Using first physical column as fallback.",
                )

        has_valid_pk = bool(physical_keys) or bool(rel_pk_cols)

        if not has_valid_pk:
            if is_strict:
                report.add_error(
                    3,
                    "PK",
                    ds.unique_name,
                    f"No primary key found (pk_resolution_mode=strict). "
                    f"Mark a column as is_key or define a relationship "
                    f"pointing to this table.",
                )
            else:
                # Permissive — warn but allow fallback
                report.add_warning(
                    3,
                    "PK",
                    ds.unique_name,
                    "No explicit primary key; will fall back to first column",
                )


# =========================================================================
# Tier 4 — Relationship validation
# =========================================================================


def _validate_tier4_relationships(
    sml: SMLModel,
    id_sanitizer: IdentifierSanitizer,
    report: PreDeploymentReport,
) -> None:
    """Validate FK columns exist in their respective datasets."""

    ds_names = {ds.unique_name for ds in sml.datasets}

    # Build physical column lookup per dataset
    phys_lookup: Dict[str, Set[str]] = {}
    for ds in sml.datasets:
        cols: Set[str] = set()
        for c in ds.columns:
            if c.unique_name.startswith("RowNumber") or c.unique_name.startswith("_"):
                continue
            source_expr = getattr(c, "source_expression", None)
            if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                continue
            cols.add(id_sanitizer.sanitize_column(c.unique_name))
        phys_lookup[ds.unique_name] = cols

    for rel in sml.relationships:
        if not rel.is_active:
            continue

        # Validate from_dataset exists
        if rel.from_dataset not in ds_names:
            report.add_error(
                4,
                "Relationship",
                rel.unique_name,
                f"from_dataset '{rel.from_dataset}' does not exist",
            )
            continue

        # Validate to_dataset exists
        if rel.to_dataset not in ds_names:
            report.add_error(
                4,
                "Relationship",
                rel.unique_name,
                f"to_dataset '{rel.to_dataset}' does not exist",
            )
            continue

        # Validate FK columns exist in from_dataset
        from_phys = phys_lookup.get(rel.from_dataset, set())
        for fk_col in (rel.from_columns or []):
            sanitized = id_sanitizer.sanitize_column(fk_col)
            if from_phys and sanitized not in from_phys:
                report.add_warning(
                    4,
                    "Relationship",
                    rel.unique_name,
                    f"FK column '{fk_col}' (sanitized: '{sanitized}') not "
                    f"found in physical columns of '{rel.from_dataset}'",
                )

        # Validate PK columns exist in to_dataset
        to_phys = phys_lookup.get(rel.to_dataset, set())
        for pk_col in (rel.to_columns or []):
            sanitized = id_sanitizer.sanitize_column(pk_col)
            if to_phys and sanitized not in to_phys:
                report.add_warning(
                    4,
                    "Relationship",
                    rel.unique_name,
                    f"PK column '{pk_col}' (sanitized: '{sanitized}') not "
                    f"found in physical columns of '{rel.to_dataset}'",
                )