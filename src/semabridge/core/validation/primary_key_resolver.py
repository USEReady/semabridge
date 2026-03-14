"""
Primary Key Resolution Engine.

Provides reliable PK detection and validation for datasets before
Snowflake Semantic View generation. Supports three resolution strategies:

    1. Explicit     — ``is_key=True`` columns declared in the model.
    2. Relationship — Inbound FK references (``to_columns``) define PKs.
    3. Heuristic    — Auto-detect PK candidates from column metadata.

The resolver validates that PK columns are *physical* (i.e. exist in
the Snowflake table as real columns, not DAX calculated columns).

Configuration:
    Controlled by ``SnowflakeBehavior.pk_resolution_mode``:
    - ``strict``     → Abort if no explicit or relationship PK found.
    - ``permissive`` → Fall back to first physical column with a warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# =========================================================================
# Result Types
# =========================================================================


@dataclass
class PKResolution:
    """Result of PK resolution for a single dataset."""

    dataset_name: str
    pk_columns: List[str]          # Sanitized physical column names
    source: str                     # "explicit", "relationship", "heuristic", "fallback"
    is_valid: bool = True
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# =========================================================================
# Resolver
# =========================================================================


class PrimaryKeyResolver:
    """Resolves primary keys for datasets using a multi-strategy approach.

    Resolution order:
        1. Explicit ``is_key=True`` columns.
        2. Inbound relationship ``to_columns``.
        3. Heuristic detection (naming patterns, unique columns).
        4. Fallback to first physical column (permissive mode only).

    Args:
        id_sanitizer: Configured ``IdentifierSanitizer`` instance.
        pk_mode: ``"strict"`` or ``"permissive"``.
    """

    # Common PK naming patterns (case-insensitive)
    _PK_PATTERNS = (
        "ID", "KEY", "PK", "CODE",
        "_ID", "_KEY", "_PK", "_CODE",
    )

    def __init__(
        self,
        id_sanitizer: IdentifierSanitizer,
        pk_mode: str = "permissive",
    ) -> None:
        self._id = id_sanitizer
        self._mode = pk_mode

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_all(
        self,
        datasets: list,
        relationships: list,
    ) -> Dict[str, PKResolution]:
        """Resolve PKs for every dataset in the model.

        Args:
            datasets: List of SML/OSI dataset objects.
            relationships: List of SML/OSI relationship objects.

        Returns:
            ``{dataset.unique_name: PKResolution}`` mapping.
        """
        # Build inbound relationship map
        inbound_map = self._build_inbound_map(relationships)

        results: Dict[str, PKResolution] = {}
        for ds in datasets:
            results[ds.unique_name] = self._resolve_single(ds, inbound_map)
        return results

    def resolve_single(
        self,
        dataset: Any,
        relationships: list,
    ) -> PKResolution:
        """Resolve PK for a single dataset.

        Args:
            dataset: SML/OSI dataset object.
            relationships: List of SML/OSI relationship objects.

        Returns:
            ``PKResolution`` for the dataset.
        """
        inbound_map = self._build_inbound_map(relationships)
        return self._resolve_single(dataset, inbound_map)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_inbound_map(
        self, relationships: list
    ) -> Dict[str, List[str]]:
        """Build map of dataset_name → inbound relationship to_columns."""
        inbound: Dict[str, List[str]] = {}
        for rel in relationships:
            if not getattr(rel, "is_active", True):
                continue
            to_ds = getattr(rel, "to_dataset", None)
            to_cols = getattr(rel, "to_columns", None)
            if to_ds and to_cols:
                if to_ds not in inbound:
                    inbound[to_ds] = []
                for col in to_cols:
                    if col not in inbound[to_ds]:
                        inbound[to_ds].append(col)
        return inbound

    def _get_physical_columns(self, dataset: Any) -> Set[str]:
        """Return the set of sanitized physical column names for a dataset."""
        cols: Set[str] = set()
        for c in getattr(dataset, "columns", []):
            name = getattr(c, "unique_name", "")
            if name.startswith("RowNumber") or name.startswith("_"):
                continue
            source_expr = getattr(c, "source_expression", None)
            if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                continue
            cols.add(self._id.sanitize_column(name))
        return cols

    def _resolve_single(
        self,
        dataset: Any,
        inbound_map: Dict[str, List[str]],
    ) -> PKResolution:
        ds_name = getattr(dataset, "unique_name", "<unknown>")
        physical_cols = self._get_physical_columns(dataset)

        result = PKResolution(dataset_name=ds_name, pk_columns=[], source="")

        # ── Strategy 1: Explicit is_key columns ────────────────────────
        explicit_keys: List[str] = []
        for c in getattr(dataset, "columns", []):
            if not getattr(c, "is_key", False):
                continue
            source_expr = getattr(c, "source_expression", None)
            if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                result.warnings.append(
                    f"Key column '{c.unique_name}' is calculated (not physical)"
                )
                continue
            sanitized = self._id.sanitize_column(c.unique_name)
            if sanitized in physical_cols:
                explicit_keys.append(sanitized)
            else:
                result.warnings.append(
                    f"Key column '{c.unique_name}' (sanitized: '{sanitized}') "
                    f"not found in physical columns"
                )

        if explicit_keys:
            result.pk_columns = explicit_keys
            result.source = "explicit"
            result.is_valid = True
            return result

        # ── Strategy 2: Inbound relationship PKs ──────────────────────
        rel_pk_cols = inbound_map.get(ds_name, [])
        if rel_pk_cols:
            valid_rel_pks: List[str] = []
            for pk_col in rel_pk_cols:
                sanitized = self._id.sanitize_column(pk_col)
                if sanitized in physical_cols:
                    valid_rel_pks.append(sanitized)
                else:
                    result.warnings.append(
                        f"Relationship PK column '{pk_col}' (sanitized: "
                        f"'{sanitized}') not found in physical columns"
                    )
            if valid_rel_pks:
                result.pk_columns = valid_rel_pks
                result.source = "relationship"
                result.is_valid = True
                return result
            else:
                result.warnings.append(
                    f"All relationship PK columns are non-physical "
                    f"({rel_pk_cols})"
                )

        # ── Strategy 3: Heuristic detection ───────────────────────────
        candidates = self._heuristic_detect(dataset, physical_cols)
        if candidates:
            result.pk_columns = candidates
            result.source = "heuristic"
            result.is_valid = True
            result.warnings.append(
                f"PK auto-detected by naming convention: {candidates}"
            )
            return result

        # ── Strategy 4: Fallback ──────────────────────────────────────
        if self._mode == "strict":
            result.is_valid = False
            result.source = "none"
            result.errors.append(
                f"No primary key found for dataset '{ds_name}' "
                f"(pk_resolution_mode=strict). Mark a column as is_key "
                f"or define a relationship pointing to this table."
            )
            return result

        # Permissive fallback — use first physical column
        if physical_cols:
            fallback = sorted(physical_cols)[0]
            result.pk_columns = [fallback]
            result.source = "fallback"
            result.is_valid = True
            result.warnings.append(
                f"No explicit PK; using first physical column "
                f"'{fallback}' as fallback"
            )
        else:
            # Absolute last resort — first column of any kind
            columns = getattr(dataset, "columns", [])
            if columns:
                col_name = self._id.sanitize_column(columns[0].unique_name)
                result.pk_columns = [col_name]
                result.source = "fallback"
                result.is_valid = True
                result.warnings.append(
                    f"No physical columns found; using '{col_name}' as "
                    f"last-resort fallback"
                )
            else:
                result.pk_columns = ["ID"]
                result.source = "fallback"
                result.is_valid = True
                result.warnings.append(
                    "Dataset has no columns; using synthetic 'ID'"
                )

        return result

    def _heuristic_detect(
        self, dataset: Any, physical_cols: Set[str]
    ) -> List[str]:
        """Detect PK candidates using naming convention heuristics.

        Looks for columns whose sanitized name ends with common PK
        suffixes (ID, KEY, PK, CODE) and is the *only* candidate.
        For tables like DIM_CUSTOMER, looks for ``CUSTOMER_ID`` or just ``ID``.
        """
        ds_name = getattr(dataset, "unique_name", "").upper()
        candidates: List[str] = []

        for col_name in physical_cols:
            upper = col_name.upper()
            # Exact match: ID, KEY, etc.
            if upper in ("ID", "KEY", "PK"):
                candidates.append(col_name)
                continue
            # Suffix match: CUSTOMER_ID, ORDER_KEY, etc.
            for suffix in self._PK_PATTERNS:
                if upper.endswith(suffix):
                    candidates.append(col_name)
                    break

        # If exactly one candidate, auto-select
        if len(candidates) == 1:
            return candidates

        # If multiple, try heuristic: prefer <TABLE_NAME>_ID
        if candidates:
            for c in candidates:
                # Match pattern: FACT_SALES → SALES_ID or FACT_SALES_ID
                base = ds_name.replace("FACT_", "").replace("DIM_", "")
                if c.upper() == f"{base}_ID" or c.upper() == "ID":
                    return [c]
            # Return first candidate if no better match
            return [candidates[0]]

        return []
