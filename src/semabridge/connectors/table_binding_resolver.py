"""Generic table binding resolver using column compatibility and normalized name matching.

Resolves model datasets to live physical tables purely from:
1. Column compatibility scoring against the live database schema
2. Normalized identifier matching (case, spaces, punctuation, underscores ignored)
3. Explicit per-project confirmed overrides (if user-edited)

No hardcoded table names or model names in code or configs!
"""
import re
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


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


def compute_column_compatibility(
    model_col_names: List[str],
    live_cols: Set[str]
) -> float:
    """Compute compatibility score [0.0 - 1.0] between model columns and a live table's columns.
    
    Awards:
    - 1.0 for exact case-insensitive column match
    - 0.95 for clean match (ignoring spaces/underscores/punctuation, e.g. COST ELEMENT ID == COST_ELEMENT_ID)
    - 0.70 for reserved-word alias match (e.g. DATE == COL_DATE, VALUE == COL_VALUE)
    - 0.70 for meaningful substring match (e.g. DEPARTMENT in DEPARTMENT_ID)
    """
    if not model_col_names or not live_cols:
        return 0.0

    live_upper = {c.upper(): c for c in live_cols}
    live_clean = {clean_ident(c): c for c in live_cols}
    live_norm = {normalize_col_name(c): c for c in live_cols}

    score = 0.0
    for mc in model_col_names:
        mc_upper = mc.upper()
        mc_clean = clean_ident(mc)
        mc_norm = normalize_col_name(mc)

        if mc_upper in live_upper:
            score += 1.0
        elif mc_clean in live_clean:
            score += 0.95
        elif mc_norm in live_norm:
            score += 0.70
        elif any(mc_clean in lc or lc in mc_clean for lc in live_clean if len(lc) >= 3):
            score += 0.70

    return score / len(model_col_names)


def rank_table_candidates(
    dataset_name: str,
    dataset_columns: List[str],
    live_schema_metadata: Dict[str, Set[str]],
) -> List[Dict[str, Any]]:
    """Rank candidate physical tables primarily by column compatibility, tie-breaking by name match."""
    norm_ds = clean_ident(dataset_name)

    # Filter out auto-generated temp tables and enriched views
    viable_tables = {
        tbl: cols for tbl, cols in live_schema_metadata.items()
        if not tbl.startswith(("__FIXED", "DATETABLETEMPLATE_", "LOCALDATETABLE_"))
        and not tbl.endswith("_ENRICHED")
    }

    candidates = []
    for tbl_name, live_cols in viable_tables.items():
        tbl_norm = clean_ident(tbl_name)

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

        col_compat = compute_column_compatibility(dataset_columns, live_cols)

        # Require either some name similarity or high column compatibility (>= 0.6)
        if name_score == 0 and col_compat < 0.6:
            continue

        candidates.append({
            "table_name": tbl_name,
            "table": tbl_name,
            "score": (col_compat * 100) + (name_score * 0.5),
            "name_score": name_score,
            "col_compat": col_compat,
        })

    # Rank primarily by column compatibility, tie-breaking by name match score
    candidates.sort(key=lambda x: (x["col_compat"], x["name_score"]), reverse=True)
    return candidates


def resolve_dataset_to_physical_table(
    dataset_name: str,
    dataset_columns: List[str],
    live_schema_metadata: Dict[str, Set[str]],
    explicit_mapping: Optional[Dict[str, str]] = None,
    model_name: Optional[str] = None,
) -> str:
    """Resolve a dataset to its canonical live physical table dynamically.

    Candidates are ranked primarily by column compatibility (how accurately the table's
    columns match the model's columns) and normalized name match.
    Returns the resolved physical table name.
    """
    if explicit_mapping and dataset_name in explicit_mapping:
        return explicit_mapping[dataset_name]
    if explicit_mapping:
        for k, v in explicit_mapping.items():
            if clean_ident(k) == clean_ident(dataset_name):
                return v

    if not live_schema_metadata:
        return dataset_name

    candidates = rank_table_candidates(dataset_name, dataset_columns, live_schema_metadata)

    if candidates and (candidates[0]["col_compat"] >= 0.4 or candidates[0]["name_score"] >= 45):
        best = candidates[0]
        logger.debug(
            "Generic table binding: '%s' -> '%s' (col_compat=%.2f, name_score=%d)",
            dataset_name, best["table"], best["col_compat"], best["name_score"]
        )
        return best["table"]

    return dataset_name

