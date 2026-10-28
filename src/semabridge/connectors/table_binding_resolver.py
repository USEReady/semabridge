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
        # Uppercase table names identified as SemaBridge placeholders
        self.placeholder_tables: Set[str] = set()
        # Normalized identifier -> list of duplicate placeholder table names in schema
        self.duplicate_placeholder_tables: Dict[str, List[str]] = {}

    def clear(self) -> None:
        super().clear()
        self.pinned_bindings.clear()
        self.row_counts.clear()
        self.binding_log.clear()
        self.placeholder_tables.clear()
        self.duplicate_placeholder_tables.clear()


def clean_ident(name: str) -> str:
    """Normalize identifier ignoring spaces, punctuation, underscores, and case."""
    return re.sub(r'[^a-zA-Z0-9]', '', str(name or '')).upper()


def normalize_col_name(name: str) -> str:
    """Normalize column name by stripping a COL_ prefix used for reserved words."""
    raw = str(name or '').strip()
    if raw.upper().startswith("COL_"):
        raw = raw[4:]
    return clean_ident(raw)


def is_column_contained(
    model_col: str,
    live_upper: Set[str],
    live_clean: Set[str],
    live_norm: Set[str],
    allow_id_suffix: bool = True,
) -> bool:
    """Check if model column exists in the live table's columns."""
    if model_col.upper() in live_upper:
        return True
    c = clean_ident(model_col)
    if c in live_clean:
        return True
    norm = normalize_col_name(model_col)
    if norm and norm in live_norm:
        return True
    # Table ID match: e.g. model col 'DEPARTMENT' matches live 'DEPARTMENT_ID' / 'DEPARTMENTID', or 'DEPARTMENT_ID' matches 'DEPARTMENT'
    if allow_id_suffix and ((c + "ID") in live_clean or (c.endswith("ID") and c[:-2] in live_clean)):
        return True
    return False


def compute_column_compatibility(
    model_col_names: List[str],
    live_cols: Set[str],
    allow_id_suffix: bool = True,
) -> float:
    """Compute fraction [0.0 - 1.0] of model columns contained in the live table."""
    if not model_col_names or not live_cols:
        return 0.0

    live_upper = {c.upper() for c in live_cols}
    live_clean = {clean_ident(c) for c in live_cols}
    live_norm = {normalize_col_name(c) for c in live_cols if c}

    matched = sum(1 for mc in model_col_names if is_column_contained(mc, live_upper, live_clean, live_norm, allow_id_suffix=allow_id_suffix))
    return matched / len(model_col_names)


def _evaluate_candidates(
    dataset_name: str,
    dataset_columns: List[str],
    viable_tables: Dict[str, Set[str]],
    allow_id_suffix: bool,
    placeholder_tables: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    norm_ds = clean_ident(dataset_name)
    candidates = []
    placeholders = placeholder_tables or set()
    for tbl_name, live_cols in viable_tables.items():
        col_compat = compute_column_compatibility(dataset_columns, live_cols, allow_id_suffix=allow_id_suffix)
        tbl_norm = clean_ident(tbl_name)

        if col_compat < 0.8 and tbl_norm != norm_ds:
            continue

        name_score = 0
        if norm_ds == tbl_norm:
            name_score = 100
        elif tbl_norm.endswith(norm_ds) or norm_ds.endswith(tbl_norm):
            name_score = 75
        elif tbl_norm.startswith(norm_ds) or norm_ds.startswith(tbl_norm):
            name_score = 65
        elif norm_ds in tbl_norm or tbl_norm in norm_ds:
            name_score = 45

        is_placeholder = 1 if (tbl_name.upper() in placeholders or clean_ident(tbl_name) in placeholders) else 0

        candidates.append({
            "table_name": tbl_name,
            "table": tbl_name,
            "score": (col_compat * 100) + (name_score * 0.5),
            "name_score": name_score,
            "col_compat": col_compat,
            "is_placeholder": is_placeholder,
        })

    # A real table beats a placeholder (is_placeholder=0 before 1)
    candidates.sort(key=lambda x: (x.get("is_placeholder", 0), -x["col_compat"], -x["name_score"], x["table"]))
    return candidates


def rank_table_candidates(
    dataset_name: str,
    dataset_columns: List[str],
    live_schema_metadata: Dict[str, Set[str]],
    allow_id_suffix: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Rank candidate physical tables primarily by column compatibility, tie-breaking by name match.
    
    A table is a candidate when column compatibility >= 0.8 or its normalized
    name equals the dataset's; column compatibility ranks first, so a
    name-only match never beats a column match. Real tables beat placeholders.
    
    The ID-suffix match is used only when it results in a single candidate.
    """
    # Filter out auto-generated temp tables and enriched views
    viable_tables = {
        tbl: cols for tbl, cols in live_schema_metadata.items()
        if not tbl.startswith(("__FIXED", "DATETABLETEMPLATE_", "LOCALDATETABLE_"))
        and not tbl.endswith("_ENRICHED")
    }

    placeholder_tables = getattr(live_schema_metadata, "placeholder_tables", set()) or set()

    if allow_id_suffix is not None:
        return _evaluate_candidates(dataset_name, dataset_columns, viable_tables, allow_id_suffix, placeholder_tables)

    # First, evaluate without ID-suffix matching
    cands_no_id = _evaluate_candidates(dataset_name, dataset_columns, viable_tables, allow_id_suffix=False, placeholder_tables=placeholder_tables)
    has_viable_no_id = any(c["col_compat"] >= 0.8 for c in cands_no_id)

    if has_viable_no_id:
        return cands_no_id

    # If no candidate reached 0.8 compatibility without ID suffix, try with ID suffix
    cands_with_id = _evaluate_candidates(dataset_name, dataset_columns, viable_tables, allow_id_suffix=True, placeholder_tables=placeholder_tables)
    viable_with_id = [c for c in cands_with_id if c["col_compat"] >= 0.8]

    # Use the ID-suffix match ONLY when it is the single candidate
    if len(viable_with_id) == 1:
        return cands_with_id

    return cands_no_id


def _is_viable(candidate: Dict[str, Any]) -> bool:
    return candidate["col_compat"] >= 0.8


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
    table by columns (>= 0.8) or normalized name. Real tables beat placeholders.
    A tie between duplicate placeholders resolves to saved binding, else canonical name.
    Raises TableBindingAmbiguityError when remaining candidates are tied.
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
            c for c in candidates
            if c.get("is_placeholder", 0) == best.get("is_placeholder", 0)
            and c["col_compat"] == best["col_compat"]
            and c["name_score"] == best["name_score"]
        ]
        if len(tied) > 1:
            # A tie between duplicate placeholders -> saved binding, else canonical name
            if all(c.get("is_placeholder", 0) == 1 for c in tied):
                pinned = (getattr(live_schema_metadata, "pinned_bindings", None) or {}).get(clean_ident(dataset_name))
                if pinned:
                    for c in tied:
                        if clean_ident(c["table"]) == clean_ident(pinned) or c["table"].upper() == str(pinned).upper():
                            _log_binding(live_schema_metadata, dataset_name, c["table"], "saved")
                            return c["table"]
                from semabridge.utils.identifiers import IdentifierSanitizer
                canon = IdentifierSanitizer().sanitize_table_name(dataset_name).upper()
                for c in tied:
                    if c["table"].upper() == canon:
                        _log_binding(live_schema_metadata, dataset_name, c["table"], "canonical name")
                        return c["table"]
            tied_names = [c["table"] for c in tied]
            raise TableBindingAmbiguityError(dataset_name, tied_names)
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
            live_compat = compute_column_compatibility(list(live_cols), set(dataset_columns))
            if compat < 0.8 and not (live_compat >= 0.8 and compat >= 0.5):
                compatible_candidates = [
                    c["table"]
                    for c in rank_table_candidates(dataset_name, dataset_columns, live_schema_metadata)
                    if c["col_compat"] >= 0.8 and clean_ident(c["table"]) != clean_tbl
                ]
                resolutions = [
                    {
                        "type": "map_table",
                        "description": (
                            "Map to another existing table (choose from compatible candidates); "
                            "the choice is saved as the project's binding."
                        ),
                        "candidates": compatible_candidates,
                    },
                    {
                        "type": "use_schema",
                        "description": (
                            "Use a different schema for this model's data (the project's source-data schema setting). "
                            "The schema must already exist; SemaBridge never creates schemas."
                        ),
                    },
                ]
                return {
                    "type": "table_column_mismatch",
                    "dataset_name": dataset_name,
                    "table_name": tbl_name,
                    "col_compat": compat,
                    "message": (
                        f"{tbl_name} already exists with different columns. "
                        f"Target table '{tbl_name}' already exists in Snowflake but its columns do not match "
                        f"PBIX table '{dataset_name}' (compatibility {compat:.1%} < 80%). "
                        f"Cannot bind or create without overwriting."
                    ),
                    "headline": f"{tbl_name} already exists with different columns.",
                    "candidates": compatible_candidates,
                    "resolutions": resolutions,
                }
    return None

