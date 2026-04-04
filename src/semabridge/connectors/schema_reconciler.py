"""Schema reconciliation helpers for Databricks source-to-semantic alignment.

This module maps semantic-model column names to physical Databricks source
column names using deterministic normalization rules, then generates a
projection view that aliases physical columns back to semantic names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def normalize_column_name(column_name: str) -> str:
    """Normalize a column identifier for matching.

    Rules:
    1. Lowercase all letters.
    2. Replace non-alphanumeric characters with underscores.
    3. Collapse duplicate underscores.
    4. Strip leading/trailing underscores.
    """
    lowered = str(column_name or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


def _quote_identifier(identifier: str) -> str:
    """Quote Databricks SQL identifiers using backticks."""
    escaped = str(identifier or "").replace("`", "``")
    return f"`{escaped}`"


@dataclass(frozen=True)
class MissingColumnDetail:
    """Structured detail for an expected semantic column that was not found."""

    expected_column: str
    normalized_expected: str
    recommendation: str


class MissingPrerequisiteException(Exception):
    """Raised when required source columns are missing and null injection is disabled."""

    def __init__(
        self,
        *,
        source_table: str,
        expected_columns: list[str],
        available_columns: list[str],
        missing_columns: list[MissingColumnDetail],
    ) -> None:
        self.source_table = source_table
        self.expected_columns = expected_columns
        self.available_columns = available_columns
        self.missing_columns = missing_columns

        missing_names = ", ".join(item.expected_column for item in missing_columns)
        message = (
            "Databricks source schema is missing required semantic columns "
            f"for {source_table}: {missing_names}"
        )
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        """Return a serializable payload for API/manifest propagation."""
        return {
            "error": "MISSING_PREREQUISITE",
            "source_table": self.source_table,
            "expected_columns": self.expected_columns,
            "available_columns": self.available_columns,
            "missing_columns": [
                {
                    "expected_column": item.expected_column,
                    "normalized_expected": item.normalized_expected,
                    "recommendation": item.recommendation,
                }
                for item in self.missing_columns
            ],
        }


class SchemaMapper:
    """Map semantic expected columns to physically available source columns."""

    def __init__(self, expected_columns: list[str], available_columns: list[str]) -> None:
        self.expected_columns = list(expected_columns or [])
        self.available_columns = list(available_columns or [])

    def build_mapping(self) -> tuple[dict[str, str], list[str]]:
        """Return (mapped, missing) where mapped keys are expected semantic columns."""
        mapped: dict[str, str] = {}
        missing: list[str] = []

        available_by_norm: dict[str, list[str]] = {}
        for available in self.available_columns:
            key = normalize_column_name(available)
            if not key:
                continue
            available_by_norm.setdefault(key, []).append(available)

        for expected in self.expected_columns:
            expected_norm = normalize_column_name(expected)
            if not expected_norm:
                missing.append(expected)
                continue

            candidates = available_by_norm.get(expected_norm, [])
            if not candidates:
                missing.append(expected)
                continue

            if len(candidates) == 1:
                mapped[expected] = candidates[0]
                continue

            expected_lower = expected.strip().lower()
            case_insensitive_exact = [c for c in candidates if c.strip().lower() == expected_lower]
            chosen = case_insensitive_exact[0] if case_insensitive_exact else sorted(candidates)[0]
            mapped[expected] = chosen

            logger.warning(
                "SchemaMapper found ambiguous normalized source columns for expected='%s' "
                "(normalized='%s'). Candidates=%s. Chosen='%s'.",
                expected,
                expected_norm,
                candidates,
                chosen,
            )

        return mapped, missing

    def generate_view_sql(
        self,
        *,
        source_table: str,
        target_view: str,
        missing_strategy: Literal["null", "raise"] = "null",
    ) -> str:
        """Generate CREATE OR REPLACE VIEW SQL with semantic aliases.

        Args:
            source_table: Fully qualified Databricks source table reference.
            target_view: Fully qualified Databricks view name to create.
            missing_strategy: How to handle missing expected columns.
                - "null": project ``NULL AS `<Expected Name>` ``.
                - "raise": raise MissingPrerequisiteException.
        """
        mapped, missing = self.build_mapping()

        if missing and missing_strategy == "raise":
            missing_details = [
                MissingColumnDetail(
                    expected_column=column,
                    normalized_expected=normalize_column_name(column),
                    recommendation=(
                        f"Add source column '{normalize_column_name(column)}' to {source_table} "
                        "or define an upstream transformation to populate it."
                    ),
                )
                for column in missing
            ]

            logger.warning(
                "Schema reconciliation failed for %s with %d missing prerequisite columns: %s",
                source_table,
                len(missing),
                ", ".join(missing),
            )

            raise MissingPrerequisiteException(
                source_table=source_table,
                expected_columns=self.expected_columns,
                available_columns=self.available_columns,
                missing_columns=missing_details,
            )

        select_fragments: list[str] = []
        for expected in self.expected_columns:
            expected_quoted = _quote_identifier(expected)
            available = mapped.get(expected)

            if available:
                select_fragments.append(f"{_quote_identifier(available)} AS {expected_quoted}")
            else:
                logger.warning(
                    "Schema reconciliation fallback for %s: injecting NULL for missing expected column '%s'.",
                    source_table,
                    expected,
                )
                select_fragments.append(f"NULL AS {expected_quoted}")

        select_sql = ",\n    ".join(select_fragments) if select_fragments else "*"

        return (
            f"CREATE OR REPLACE VIEW {target_view} AS\n"
            f"SELECT\n    {select_sql}\n"
            f"FROM {source_table}"
        )
