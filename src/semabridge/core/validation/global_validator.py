"""
Global Pre-Deployment Validator.

Centralized validation gate that must pass BEFORE any SQL generation
or deployment. Orchestrates:

    Tier 1 — YAML Schema:          Structural integrity
    Tier 2 — Identifier Grounding: Column refs resolve to physical columns
    Tier 3 — PK Integrity:         Every dataset has a valid primary key
    Tier 4 — Relationship Validation: FK/PK columns are physical
    Tier 5 — Metric Validation:    Metrics reference valid columns/datasets
    Tier 6 — Snowflake Metadata:   Source tables/columns match Snowflake

Deployment MUST abort if any Tier produces blocking errors.

This module unifies and replaces scattered validation across the codebase
with a single entry point: ``GlobalValidator.validate()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.logger import get_logger
from semabridge.core.validation.primary_key_resolver import (
    PrimaryKeyResolver,
    PKResolution,
)

logger = get_logger(__name__)


# =========================================================================
# Validation Issue
# =========================================================================


@dataclass
class ValidationIssue:
    """A single validation finding."""
    tier: int
    severity: str  # "ERROR" or "WARNING"
    category: str
    object_name: str
    message: str
    suggested_fix: str = ""

    def __str__(self) -> str:
        return (
            f"[Tier {self.tier}/{self.severity}] {self.category} — "
            f"{self.object_name}: {self.message}"
        )


@dataclass
class GlobalValidationReport:
    """Aggregated validation report across all tiers."""
    issues: List[ValidationIssue] = field(default_factory=list)
    pk_resolutions: Dict[str, PKResolution] = field(default_factory=dict)

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
        self,
        tier: int,
        category: str,
        name: str,
        msg: str,
        fix: str = "",
    ) -> None:
        self.issues.append(
            ValidationIssue(tier, "ERROR", category, name, msg, fix)
        )

    def add_warning(
        self,
        tier: int,
        category: str,
        name: str,
        msg: str,
        fix: str = "",
    ) -> None:
        self.issues.append(
            ValidationIssue(tier, "WARNING", category, name, msg, fix)
        )

    def errors_summary(self) -> str:
        """One-line summary of errors for exception messages."""
        if not self.has_errors:
            return "All validations passed"
        error_lines = [str(i) for i in self.issues if i.severity == "ERROR"]
        return (
            f"{self.error_count} error(s), {self.warning_count} warning(s). "
            f"First errors: " + "; ".join(error_lines[:5])
        )


# =========================================================================
# Global Validator
# =========================================================================


class GlobalValidator:
    """Centralized pre-deployment validator.

    Runs all validation tiers and returns a comprehensive report.
    If ``halt_on_error=True`` (default), raises
    ``SemaBridgeValidationError`` on any blocking error.

    Args:
        id_sanitizer: Configured ``IdentifierSanitizer`` instance.
        sf_behavior: ``SnowflakeBehavior`` configuration.
        snowflake_metadata: Optional dict of ``{TABLE_NAME: {COL_SET}}``
            from Snowflake INFORMATION_SCHEMA for Tier 6 validation.
    """

    def __init__(
        self,
        id_sanitizer: IdentifierSanitizer,
        sf_behavior: Any,
        snowflake_metadata: Optional[Dict[str, Set[str]]] = None,
    ) -> None:
        self._id = id_sanitizer
        self._sf_behavior = sf_behavior
        self._sf_meta = snowflake_metadata or {}

        pk_mode_raw = getattr(sf_behavior, "pk_resolution_mode", None)
        pk_mode = (
            pk_mode_raw.value if hasattr(pk_mode_raw, "value") else "permissive"
        )
        self._pk_resolver = PrimaryKeyResolver(id_sanitizer, pk_mode)
        self._pk_mode = pk_mode

    # ------------------------------------------------------------------
    # Public Entry Point
    # ------------------------------------------------------------------

    def validate(
        self,
        model: Any,
        *,
        halt_on_error: bool = True,
    ) -> GlobalValidationReport:
        """Run all validation tiers against a model.

        Args:
            model: SML or OSI model to validate.
            halt_on_error: If True, raise on blocking errors.

        Returns:
            ``GlobalValidationReport`` with all issues.

        Raises:
            SemaBridgeValidationError: If ``halt_on_error`` and errors found.
        """
        report = GlobalValidationReport()

        self._tier1_schema(model, report)
        self._tier2_identifiers(model, report)
        self._tier3_primary_keys(model, report)
        self._tier4_relationships(model, report)
        self._tier5_metrics(model, report)

        if self._sf_meta:
            self._tier6_snowflake_metadata(model, report)

        if halt_on_error and report.has_errors:
            from semabridge.core.exceptions import (
                ValidationError as SemaBridgeValidationError,
            )
            logger.error(
                f"Global validation FAILED: {report.errors_summary()}"
            )
            raise SemaBridgeValidationError(
                f"Pre-deployment validation failed with "
                f"{report.error_count} error(s). "
                f"{report.errors_summary()}",
                errors=[
                    str(i) for i in report.issues if i.severity == "ERROR"
                ],
            )

        if report.warning_count > 0:
            warnings_text = "\n  - ".join(
                str(i) for i in report.issues if i.severity == "WARNING"
            )
            logger.warning(
                f"Global validation passed with "
                f"{report.warning_count} warning(s):\n  - {warnings_text}"
            )
        else:
            logger.info("Global validation passed (all tiers)")

        return report

    # ------------------------------------------------------------------
    # Tier 1 — YAML Schema Structural Integrity
    # ------------------------------------------------------------------

    def _tier1_schema(self, model: Any, report: GlobalValidationReport) -> None:
        model_name = getattr(model, "unique_name", "<unnamed>")
        datasets = getattr(model, "datasets", [])
        metrics = getattr(model, "metrics", [])

        if not datasets:
            report.add_error(
                1, "Schema", model_name,
                "Model has no datasets",
                "Add at least one dataset to the model.",
            )
            return

        dataset_metric_counts: dict[str, int] = {}
        for metric in metrics:
            ds_ref = getattr(metric, "dataset", None)
            if ds_ref:
                dataset_metric_counts[ds_ref] = dataset_metric_counts.get(ds_ref, 0) + 1

        for ds in datasets:
            if not getattr(ds, "columns", []):
                metric_count = dataset_metric_counts.get(ds.unique_name, 0)
                if metric_count > 0:
                    # Measure-only datasets are valid by design.
                    # Keep Tier-1 logs clean by suppressing informational warnings.
                    continue
                else:
                    report.add_error(
                        1, "Schema", ds.unique_name,
                        "Dataset has no columns",
                        "Add column definitions to this dataset or attach metrics to it.",
                    )

        ds_names = {ds.unique_name for ds in datasets}
        for metric in metrics:
            ds_ref = getattr(metric, "dataset", None)
            if ds_ref and ds_ref not in ds_names:
                report.add_error(
                    1, "Schema", metric.unique_name,
                    f"Metric references non-existent dataset '{ds_ref}'",
                    f"Ensure dataset '{ds_ref}' exists in the model.",
                )

            has_source = bool(
                getattr(metric, "source_column", None)
                and getattr(metric, "aggregation", None)
            )
            has_expr = bool(
                getattr(metric, "sql_expression", None)
                or getattr(metric, "expression", None)
            )
            if not has_source and not has_expr:
                report.add_warning(
                    1, "Schema", metric.unique_name,
                    "Metric has neither source_column+aggregation nor expression",
                    "Provide either source_column+aggregation or an expression.",
                )

    # ------------------------------------------------------------------
    # Tier 2 — Identifier Grounding
    # ------------------------------------------------------------------

    def _tier2_identifiers(
        self, model: Any, report: GlobalValidationReport
    ) -> None:
        # Build physical column lookup per dataset
        phys_lookup = self._build_physical_lookup(model)

        # Check metric source_column references
        for metric in getattr(model, "metrics", []):
            src_col = getattr(metric, "source_column", None)
            ds_name = getattr(metric, "dataset", None)
            if src_col and ds_name:
                sanitized = self._id.sanitize_column(src_col)
                known = phys_lookup.get(ds_name, set())
                if known and sanitized not in known:
                    report.add_warning(
                        2, "Identifier", metric.unique_name,
                        f"Metric source_column '{src_col}' (sanitized: "
                        f"'{sanitized}') not found in physical columns of "
                        f"dataset '{ds_name}'",
                        "Verify column exists in the source table or mark as calculated.",
                    )

        # Check dimension attributes
        for dim in getattr(model, "dimensions", []):
            for attr in getattr(dim, "attributes", []):
                col_name = (
                    getattr(attr, "dataset_column", None)
                    or getattr(attr, "source_column", None)
                )
                attr_ds = getattr(attr, "dataset", None)
                if attr_ds and col_name:
                    sanitized = self._id.sanitize_column(col_name)
                    known = phys_lookup.get(attr_ds, set())
                    if known and sanitized not in known:
                        report.add_warning(
                            2, "Identifier", attr.unique_name,
                            f"Dimension attribute column '{col_name}' "
                            f"not found in physical columns of '{attr_ds}'",
                            "Verify column exists in the source table or update the attribute mapping.",
                        )

    # ------------------------------------------------------------------
    # Tier 3 — Primary Key Integrity
    # ------------------------------------------------------------------

    def _tier3_primary_keys(
        self, model: Any, report: GlobalValidationReport
    ) -> None:
        datasets = getattr(model, "datasets", [])
        relationships = getattr(model, "relationships", [])
        metrics = getattr(model, "metrics", [])

        metric_counts_by_dataset: dict[str, int] = {}
        for metric in metrics:
            ds_ref = getattr(metric, "dataset", None)
            if ds_ref:
                metric_counts_by_dataset[ds_ref] = metric_counts_by_dataset.get(ds_ref, 0) + 1

        pk_datasets = []
        for ds in datasets:
            if not getattr(ds, "columns", []) and metric_counts_by_dataset.get(ds.unique_name, 0) > 0:
                logger.info(
                    "Tier-3 PK: skipping PK resolution for measure-only dataset '%s'",
                    ds.unique_name,
                )
                continue
            pk_datasets.append(ds)

        pk_results = self._pk_resolver.resolve_all(pk_datasets, relationships)
        report.pk_resolutions = pk_results

        for ds_name, pk_res in pk_results.items():
            for warn in pk_res.warnings:
                report.add_warning(
                    3, "PK", ds_name, warn,
                    "Mark a column as is_key or set pk_resolution_mode=permissive.",
                )
            for err in pk_res.errors:
                report.add_error(
                    3, "PK", ds_name, err,
                    "Mark a column as is_key=True or define a relationship "
                    "pointing to this table.",
                )

    # ------------------------------------------------------------------
    # Tier 4 — Relationship Validation
    # ------------------------------------------------------------------

    def _tier4_relationships(
        self, model: Any, report: GlobalValidationReport
    ) -> None:
        datasets = getattr(model, "datasets", [])
        relationships = getattr(model, "relationships", [])
        ds_names = {ds.unique_name for ds in datasets}
        phys_lookup = self._build_physical_lookup(model)

        for rel in relationships:
            if not getattr(rel, "is_active", True):
                continue

            rel_name = getattr(rel, "unique_name", "<unnamed_rel>")
            from_ds = getattr(rel, "from_dataset", "")
            to_ds = getattr(rel, "to_dataset", "")

            # Check dataset existence
            if from_ds not in ds_names:
                report.add_error(
                    4, "Relationship", rel_name,
                    f"from_dataset '{from_ds}' does not exist",
                    "Add the missing dataset or fix the relationship reference.",
                )
                continue

            if to_ds not in ds_names:
                report.add_error(
                    4, "Relationship", rel_name,
                    f"to_dataset '{to_ds}' does not exist",
                    "Add the missing dataset or fix the relationship reference.",
                )
                continue

            # Validate FK columns exist as physical columns
            from_phys = phys_lookup.get(from_ds, set())
            for fk_col in getattr(rel, "from_columns", []):
                sanitized = self._id.sanitize_column(fk_col)
                if from_phys and sanitized not in from_phys:
                    report.add_warning(
                        4, "Relationship", rel_name,
                        f"FK column '{fk_col}' (sanitized: '{sanitized}') "
                        f"not in physical columns of '{from_ds}'. "
                        f"Relationship will use non-physical key.",
                        "Ensure the FK column exists in the source table or "
                        "materialize the column.",
                    )

            # Validate PK columns exist as physical columns
            to_phys = phys_lookup.get(to_ds, set())
            for pk_col in getattr(rel, "to_columns", []):
                sanitized = self._id.sanitize_column(pk_col)
                if to_phys and sanitized not in to_phys:
                    report.add_warning(
                        4, "Relationship", rel_name,
                        f"PK column '{pk_col}' (sanitized: '{sanitized}') "
                        f"not in physical columns of '{to_ds}'. "
                        f"Relationship will use non-physical key.",
                        "Ensure the PK column exists in the source table or "
                        "materialize the column.",
                    )

    # ------------------------------------------------------------------
    # Tier 5 — Metric Validation  
    # ------------------------------------------------------------------

    def _tier5_metrics(
        self, model: Any, report: GlobalValidationReport
    ) -> None:
        phys_lookup = self._build_physical_lookup(model)
        ds_names = {ds.unique_name for ds in getattr(model, "datasets", [])}

        for metric in getattr(model, "metrics", []):
            ds_name = getattr(metric, "dataset", None)
            if not ds_name:
                report.add_warning(
                    5, "Metric", metric.unique_name,
                    "Metric has no dataset reference",
                    "Set the dataset field on this metric.",
                )
                continue

            if ds_name not in ds_names:
                # Already caught in Tier 1, skip duplicate
                continue

            src_col = getattr(metric, "source_column", None)
            if src_col:
                sanitized = self._id.sanitize_column(src_col)
                known = phys_lookup.get(ds_name, set())
                if known and sanitized not in known:
                    report.add_warning(
                        5, "Metric", metric.unique_name,
                        f"source_column '{src_col}' (sanitized: "
                        f"'{sanitized}') not found in physical columns "
                        f"of '{ds_name}'",
                        "Verify the column exists or update the metric definition.",
                    )

    # ------------------------------------------------------------------
    # Tier 6 — Snowflake Metadata Validation
    # ------------------------------------------------------------------

    def _tier6_snowflake_metadata(
        self, model: Any, report: GlobalValidationReport
    ) -> None:
        """Validate model references against live Snowflake metadata.

        Requires ``self._sf_meta`` to be populated with
        ``{TABLE_NAME: {COLUMN_NAME, ...}}`` from INFORMATION_SCHEMA.
        """
        for ds in getattr(model, "datasets", []):
            source_table = (
                getattr(ds, "source_table", None) or ds.unique_name
            )
            safe_table = self._id.sanitize_table_name(source_table)

            if safe_table not in self._sf_meta:
                report.add_error(
                    6, "Snowflake", ds.unique_name,
                    f"Source table '{safe_table}' not found in Snowflake",
                    "Enable create_missing_tables=true or create the table manually.",
                )
                continue

            sf_columns = self._sf_meta[safe_table]
            for col in getattr(ds, "columns", []):
                name = getattr(col, "unique_name", "")
                if name.startswith("RowNumber") or name.startswith("_"):
                    continue
                src_expr = getattr(col, "source_expression", None)
                if src_expr and not IdentifierSanitizer.is_physical_source_column(src_expr):
                    continue
                sanitized = self._id.sanitize_column(name)
                if sanitized not in sf_columns:
                    report.add_warning(
                        6, "Snowflake", f"{ds.unique_name}.{name}",
                        f"Column '{sanitized}' not found in Snowflake table "
                        f"'{safe_table}'",
                        "Check for casing mismatches or add the column to the table.",
                    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_physical_lookup(
        self, model: Any
    ) -> Dict[str, Set[str]]:
        """Build sanitized physical-column lookup per dataset."""
        result: Dict[str, Set[str]] = {}
        for ds in getattr(model, "datasets", []):
            cols: Set[str] = set()
            for c in getattr(ds, "columns", []):
                name = getattr(c, "unique_name", "")
                if name.startswith("RowNumber") or name.startswith("_"):
                    continue
                src_expr = getattr(c, "source_expression", None)
                if src_expr and not IdentifierSanitizer.is_physical_source_column(src_expr):
                    continue
                cols.add(self._id.sanitize_column(name))
            result[ds.unique_name] = cols
        return result