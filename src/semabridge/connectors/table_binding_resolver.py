"""Generic table binding resolver using column compatibility and normalized name matching.

Resolves model datasets to live physical tables purely from:
1. Column compatibility scoring against the live database schema
2. Normalized identifier matching (case, spaces, punctuation, underscores ignored)
3. Explicit per-project confirmed overrides (if user-edited)
4. Bindings saved by an earlier successful sync of the same project/model
   (LiveSchemaMetadata.pinned_bindings), so every later sync binds the same way

The binding never depends on table data (row counts or contents). Order:
explicit mapping, saved binding, the table named exactly like the PBIX table,
then the best column-set match. If two tables still tie, the sync stops with
TableBindingAmbiguityError listing them.

No hardcoded table names or model names in code or configs!
"""
import re
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


AMBIGUOUS_TABLE_BINDING = "Ambiguous table binding"


class TableBindingAmbiguityError(ValueError):
    """Two or more tables match a dataset equally well by name and columns.

    The sync stops: the user saves a binding (or maps the table explicitly)
    rather than SemaBridge guessing from table contents.
    """

    def __init__(self, dataset_name: str, candidates: List[str]):
        self.dataset_name = dataset_name
        self.candidates = list(candidates)
        super().__init__(
            f"{AMBIGUOUS_TABLE_BINDING} for '{dataset_name}': {', '.join(self.candidates)} "
            f"match equally by name and columns. Map the table explicitly or save a binding."
        )


class LiveSchemaMetadata(dict):
    """TABLE -> {COLUMN} map of the live schema, plus this deploy's binding state.

    A plain dict for every existing reader. The extra attributes ride along
    with the one metadata object an emitter shares with its schema manager
    and DDL builders, so every resolve_dataset_to_physical_table() call site
    sees the same pins and row counts and records into the same log.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # clean_ident(dataset) -> physical table saved by an earlier sync.
        self.pinned_bindings: Dict[str, str] = {}
        # Kept for callers that still read it; never used to bind.
        self.row_counts: Dict[str, int] = {}
        # clean_ident(dataset) -> {dataset, table, matched_by, tie}
        self.binding_log: Dict[str, Dict[str, Any]] = {}

    def clear(self) -> None:
        super().clear()
        self.pinned_bindings.clear()
        self.row_counts.clear()
        self.binding_log.clear()


def clean_ident(name: str) -> str:
    """Normalize identifier ignoring spaces, punctuation, underscores, and case."""
    return re.sub(r'[^a-zA-Z0-9]', '', str(name or '')).upper()


def normalize_col_name(name: str) -> str:
    """Normalize column name with standard reserved word aliases."""
    c = clean_ident(name)
    if c in ("COLDATE", "DATE", "DATEDATE"):
        return "DATE"
    if c in ("COLVALUE", "VALUE"):
        return "VALUE"
    return c


def is_column_contained(
    model_col: str,
    live_upper: Set[str],
    live_clean: Set[str],
    live_norm: Set[str],
) -> bool:
    """Check if model column exists in the live table's columns."""
    if model_col.upper() in live_upper:
        return True
    c = clean_ident(model_col)
    if c in live_clean:
        return True
    if normalize_col_name(model_col) in live_norm:
        return True
    # Table ID match: e.g. model col 'DEPARTMENT' matches live 'DEPARTMENT_ID' / 'DEPARTMENTID', or 'DEPARTMENT_ID' matches 'DEPARTMENT'
    if (c + "ID") in live_clean or (c.endswith("ID") and c[:-2] in live_clean):
        return True
    return False


def compute_column_compatibility(
    model_col_names: List[str],
    live_cols: Set[str]
) -> float:
    """Compute fraction [0.0 - 1.0] of model columns contained in the live table."""
    if not model_col_names or not live_cols:
        return 0.0

    live_upper = {c.upper() for c in live_cols}
    live_clean = {clean_ident(c) for c in live_cols}
    live_norm = {normalize_col_name(c) for c in live_cols}

    matched = sum(1 for mc in model_col_names if is_column_contained(mc, live_upper, live_clean, live_norm))
    return matched / len(model_col_names)


def rank_table_candidates(
    dataset_name: str,
    dataset_columns: List[str],
    live_schema_metadata: Dict[str, Set[str]],
) -> List[Dict[str, Any]]:
    """Rank candidate physical tables primarily by column compatibility, tie-breaking by name match.
    
    A table is a candidate when column compatibility >= 0.8 or its normalized
    name equals the dataset's; column compatibility ranks first, so a
    name-only match never beats a column match.
    """
    norm_ds = clean_ident(dataset_name)

    # Filter out auto-generated temp tables and enriched views
    viable_tables = {
        tbl: cols for tbl, cols in live_schema_metadata.items()
        if not tbl.startswith(("__FIXED", "DATETABLETEMPLATE_", "LOCALDATETABLE_"))
        and not tbl.endswith("_ENRICHED")
    }

    candidates = []
    for tbl_name, live_cols in viable_tables.items():
        col_compat = compute_column_compatibility(dataset_columns, live_cols)
        tbl_norm = clean_ident(tbl_name)

        # A candidate needs matching columns (>= 0.8) or the same normalized
        # name; columns rank first, so a name-only match never beats them.
        if col_compat < 0.8 and tbl_norm != norm_ds:
            continue

        # Name matching score [0 - 100]
        name_score = 0
        if norm_ds == tbl_norm:
            name_score = 100
        elif tbl_norm.endswith(norm_ds) or norm_ds.endswith(tbl_norm):
            name_score = 75
        elif tbl_norm.startswith(norm_ds) or norm_ds.startswith(tbl_norm):
            name_score = 65
        elif norm_ds in tbl_norm or tbl_norm in norm_ds:
            name_score = 45

        candidates.append({
            "table_name": tbl_name,
            "table": tbl_name,
            "score": (col_compat * 100) + (name_score * 0.5),
            "name_score": name_score,
            "col_compat": col_compat,
        })

    # Rank by column compatibility, then name match; table name only orders
    # the list (an equal top rank is an ambiguity, see resolve below).
    candidates.sort(key=lambda x: (-x["col_compat"], -x["name_score"], x["table"]))
    return candidates


def _is_viable(candidate: Dict[str, Any]) -> bool:
    return candidate["col_compat"] >= 0.8 or candidate["name_score"] == 100


def _log_binding(live_schema_metadata: Any, dataset_name: str, table: str, matched_by: str,
                 tie: Optional[List[Dict[str, Any]]] = None) -> None:
    log = getattr(live_schema_metadata, "binding_log", None)
    if log is None:
        return
    log[clean_ident(dataset_name)] = {
        "dataset": dataset_name,
        "table": table,
        "matched_by": matched_by,
        "tie": tie or [],
    }


def resolve_dataset_to_physical_table(
    dataset_name: str,
    dataset_columns: List[str],
    live_schema_metadata: Dict[str, Set[str]],
    explicit_mapping: Optional[Dict[str, str]] = None,
    model_name: Optional[str] = None,
) -> str:
    """Resolve a dataset to its canonical live physical table dynamically.

    Order: explicit mapping, then the binding saved by an earlier sync of
    this project/model (if that table still exists), then the table named
    exactly like the dataset (if its columns match), then the best existing
    table by columns (>= 0.8) or normalized name. Otherwise the table counts
    as missing. Raises TableBindingAmbiguityError when the best match is tied.
    """
    if explicit_mapping and dataset_name in explicit_mapping:
        return explicit_mapping[dataset_name]
    if explicit_mapping:
        for k, v in explicit_mapping.items():
            if clean_ident(k) == clean_ident(dataset_name):
                return v

    if not live_schema_metadata:
        return dataset_name

    pinned = (getattr(live_schema_metadata, "pinned_bindings", None) or {}).get(clean_ident(dataset_name))
    if pinned and pinned.upper() in live_schema_metadata:
        _log_binding(live_schema_metadata, dataset_name, pinned, "saved")
        return pinned

    candidates = rank_table_candidates(dataset_name, dataset_columns, live_schema_metadata)

    exact = [
        c for c in candidates
        if c["table"].upper() == str(dataset_name).upper() and c["col_compat"] >= 0.8
    ]
    if exact:
        _log_binding(live_schema_metadata, dataset_name, exact[0]["table"], "exact name")
        return exact[0]["table"]

    if candidates and _is_viable(candidates[0]):
        best = candidates[0]
        tied = [
            c["table"] for c in candidates
            if c["col_compat"] == best["col_compat"] and c["name_score"] == best["name_score"]
        ]
        if len(tied) > 1:
            raise TableBindingAmbiguityError(dataset_name, tied)
        logger.debug(
            "Generic table binding: '%s' -> '%s' (col_compat=%.2f, name_score=%d)",
            dataset_name, best["table"], best["col_compat"], best["name_score"],
        )
        _log_binding(
            live_schema_metadata,
            dataset_name,
            best["table"],
            "columns" if best["col_compat"] >= 0.8 else "name",
        )
        return best["table"]

    return dataset_name


def check_table_name_collision_blocker(
    dataset_name: str,
    dataset_columns: List[str],
    live_schema_metadata: Dict[str, Set[str]],
) -> Optional[Dict[str, Any]]:
    """Check if the needed table name already exists in Snowflake but its columns don't match (compat < 0.8).

    Returns blocker dict if collision detected, otherwise None.
    """
    if not live_schema_metadata:
        return None

    clean_ds = clean_ident(dataset_name)
    for tbl_name, live_cols in live_schema_metadata.items():
        if tbl_name.startswith(("__FIXED", "DATETABLETEMPLATE_", "LOCALDATETABLE_")) or tbl_name.endswith("_ENRICHED"):
            continue
        clean_tbl = clean_ident(tbl_name)
        if clean_ds == clean_tbl:
            compat = compute_column_compatibility(dataset_columns, live_cols)
            if compat < 0.8:
                return {
                    "dataset_name": dataset_name,
                    "table_name": tbl_name,
                    "col_compat": compat,
                    "message": (
                        f"Target table '{tbl_name}' already exists in Snowflake but its columns do not match "
                        f"PBIX table '{dataset_name}' (compatibility {compat:.1%} < 80%). "
                        f"Cannot bind or create without overwriting."
                    ),
                }
    return None

