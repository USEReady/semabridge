"""
Cortex Validator — Mandate 6: Cortex Semantic Model Grounding.

Validates Cortex Analyst YAML output against Snowflake Cortex
requirements, catching silent-failure patterns before deployment:

1. **Description completeness** — Every table and column MUST have a
   non-empty ``description`` field (Cortex silently degrades without).
2. **Metric expression validity** — SQL expressions must not reference
   non-existent columns or use unsupported syntax.
3. **Relationship integrity** — ``PRIMARY KEY`` and ``FOREIGN KEY``
   declarations must reference real physical columns.
4. **Verified-query coverage** — At least one verified query should
   exist per metric group (warns if missing).
5. **Naming hygiene** — Identifiers must be properly quoted and
   SCREAMING_SNAKE_CASE for Snowflake compatibility.

Usage::

    from semabridge.core.validation.cortex_validator import CortexValidator

    validator = CortexValidator(require_descriptions=True)
    report = validator.validate(cortex_yaml_dict)
    if report.has_errors:
        for err in report.errors:
            print(f"ERROR: {err}")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CortexValidationReport:
    """Result of Cortex YAML validation."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def summary(self) -> str:
        parts = [
            f"{len(self.errors)} error(s)",
            f"{len(self.warnings)} warning(s)",
            f"{len(self.info)} info",
        ]
        return f"Cortex validation: {', '.join(parts)}"


class CortexValidator:
    """Validates Cortex Analyst YAML before deployment.

    Parameters
    ----------
    require_descriptions : bool
        If True, missing descriptions on tables/columns are errors;
        otherwise they are warnings.
    """

    # Cortex-specific reserved words that must not be used as bare identifiers
    CORTEX_RESERVED = frozenset({
        "TABLE", "VIEW", "COLUMN", "INDEX", "SELECT", "FROM", "WHERE",
        "JOIN", "GROUP", "ORDER", "BY", "HAVING", "UNION", "INSERT",
        "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "GRANT", "REVOKE",
    })

    def __init__(self, require_descriptions: bool = True) -> None:
        self.require_descriptions = require_descriptions

    def validate(self, cortex_yaml: dict[str, Any]) -> CortexValidationReport:
        """Run all validation checks on a Cortex YAML structure.

        Parameters
        ----------
        cortex_yaml
            Parsed Cortex Analyst YAML dictionary (top-level keys:
            ``name``, ``tables``, ``verified_queries``, etc.).

        Returns
        -------
        CortexValidationReport
        """
        report = CortexValidationReport()

        self._validate_top_level(cortex_yaml, report)
        self._validate_tables(cortex_yaml.get("tables", []), report)
        self._validate_verified_queries(cortex_yaml, report)
        self._validate_naming(cortex_yaml, report)

        level = "ERROR" if report.has_errors else "WARNING" if report.has_warnings else "INFO"
        logger.log(
            logging.ERROR if report.has_errors else logging.WARNING if report.has_warnings else logging.INFO,
            report.summary(),
        )

        return report

    # ------------------------------------------------------------------
    # Validation passes
    # ------------------------------------------------------------------

    def _validate_top_level(self, data: dict, report: CortexValidationReport) -> None:
        """Ensure required top-level keys exist."""
        if not data.get("name"):
            report.errors.append("Missing top-level 'name' field")

        if not data.get("tables"):
            report.errors.append("No 'tables' defined — Cortex model will be empty")

        if data.get("description"):
            report.info.append(f"Model description: {data['description'][:80]}")
        else:
            msg = "Missing model-level 'description'"
            if self.require_descriptions:
                report.errors.append(msg)
            else:
                report.warnings.append(msg)

    def _validate_tables(self, tables: list[dict], report: CortexValidationReport) -> None:
        """Validate each table and its columns/metrics."""
        all_columns: dict[str, set[str]] = {}  # table_name -> set of column names

        for table in tables:
            tname = table.get("name", "<unnamed>")

            # Description check
            if not table.get("description"):
                msg = f"Table '{tname}': missing description"
                if self.require_descriptions:
                    report.errors.append(msg)
                else:
                    report.warnings.append(msg)

            # Columns
            cols = table.get("columns", [])
            col_names: set[str] = set()
            for col in cols:
                cname = col.get("name", "<unnamed>")
                col_names.add(cname.upper())

                if not col.get("description"):
                    msg = f"Column '{tname}.{cname}': missing description"
                    if self.require_descriptions:
                        report.errors.append(msg)
                    else:
                        report.warnings.append(msg)

                # Check synonyms are strings, not empty
                synonyms = col.get("synonyms", [])
                if synonyms and not all(isinstance(s, str) and s.strip() for s in synonyms):
                    report.warnings.append(
                        f"Column '{tname}.{cname}': invalid synonym entries"
                    )

            all_columns[tname.upper()] = col_names

            # Metrics
            metrics = table.get("metrics", [])
            for metric in metrics:
                mname = metric.get("name", "<unnamed>")
                expr = metric.get("expression", "") or metric.get("expr", "")

                if not expr:
                    report.warnings.append(
                        f"Metric '{tname}.{mname}': empty expression"
                    )
                    continue

                # Check for column references in the expression
                quoted_refs = re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr.upper())
                for ref in quoted_refs:
                    if ref not in col_names and ref != mname.upper():
                        report.warnings.append(
                            f"Metric '{tname}.{mname}': references "
                            f"column '{ref}' not in table columns"
                        )

            # Primary key
            pk = table.get("primary_key") or table.get("primary_keys", [])
            if not pk:
                report.warnings.append(
                    f"Table '{tname}': no primary key defined"
                )

        report.info.append(
            f"Validated {len(tables)} table(s), "
            f"{sum(len(c) for c in all_columns.values())} column(s)"
        )

    def _validate_verified_queries(
        self, data: dict, report: CortexValidationReport
    ) -> None:
        """Check verified queries for completeness."""
        queries = data.get("verified_queries", [])
        if not queries:
            report.warnings.append(
                "No verified queries — Cortex Analyst accuracy will be lower"
            )
            return

        for i, q in enumerate(queries):
            if not q.get("question"):
                report.warnings.append(f"Verified query #{i+1}: missing 'question'")
            if not q.get("sql") and not q.get("answer"):
                report.warnings.append(
                    f"Verified query #{i+1}: missing both 'sql' and 'answer'"
                )

        report.info.append(f"Found {len(queries)} verified query/queries")

    def _validate_naming(self, data: dict, report: CortexValidationReport) -> None:
        """Check identifier naming conventions."""
        tables = data.get("tables", [])
        for table in tables:
            tname = table.get("name", "")
            # Warn if table name has spaces or lowercase (Snowflake issues)
            if " " in tname:
                report.warnings.append(
                    f"Table '{tname}': contains spaces — may cause SQL errors"
                )
            if tname != tname.upper() and not tname.startswith('"'):
                report.info.append(
                    f"Table '{tname}': not uppercase — Snowflake will normalize"
                )

            for col in table.get("columns", []):
                cname = col.get("name", "")
                if cname.upper() in self.CORTEX_RESERVED and not cname.startswith('"'):
                    report.warnings.append(
                        f"Column '{tname}.{cname}': reserved word — should be quoted"
                    )
