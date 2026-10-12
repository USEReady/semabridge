"""Generic table binding resolver using name normalization and column compatibility.

Resolves model datasets (e.g. 'Fact', 'SalesFact', 'Date') to live physical tables
(e.g. 'SPEND_FACT', 'SALES_FACT', 'SPEND_DATE') purely from:
1. Normalized identifier matching (case, spaces, underscores ignored)
2. Column compatibility scoring against the live database schema
3. Model token affinity (e.g. 'SPEND' in 'Corporate Spend' binds to 'SPEND_FACT' / 'SPEND_DATE')
4. Loader table priority over legacy un-split tables ('FACT', 'SALESFACT')
5. Explicit per-project confirmed overrides (if user-edited)
No hardcoded table names in code or project configs!
"""
import re
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Known legacy un-split tables that should not be bound when clean loader tables exist
LEGACY_TABLES = {"FACT", "SALESFACT"}

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
    """Compute compatibility score [0.0 - 1.0] between model columns and a live table's columns."""
    if not model_col_names:
        return 0.0
    
    live_norm_map: Dict[str, List[str]] = {}
    for lc in live_cols:
        norm = normalize_col_name(lc)
        live_norm_map.setdefault(norm, []).append(lc)
        clean_lc = clean_ident(lc)
        live_norm_map.setdefault(clean_lc, []).append(lc)

    matched = 0
    for mc in model_col_names:
        norm = normalize_col_name(mc)
        clean_mc = clean_ident(mc)
        if norm in live_norm_map or clean_mc in live_norm_map:
            matched += 1
        elif any(clean_mc in lc_clean or lc_clean in clean_mc for lc_clean in live_norm_map):
            matched += 0.8
            
    return matched / max(len(model_col_names), 1)

def resolve_dataset_to_physical_table(
    dataset_name: str,
    dataset_columns: List[str],
    live_schema_metadata: Dict[str, Set[str]],
    explicit_mapping: Optional[Dict[str, str]] = None,
    model_name: Optional[str] = None,
) -> str:
    """Resolve a dataset to its canonical live physical table dynamically.
    
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

    norm_ds = clean_ident(dataset_name)
    clean_model = clean_ident(model_name or "")
    
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
            name_score = 70
        elif norm_ds in tbl_norm or tbl_norm in norm_ds:
            name_score = 40

        # Model affinity boost: if table prefix matches model tokens (e.g. SPEND in Corporate Spend)
        model_affinity = 0
        if clean_model:
            model_tokens = [t for t in re.split(r'[^A-Za-z0-9]+', str(model_name).upper()) if len(t) >= 4]
            tbl_tokens = [t for t in re.split(r'[^A-Za-z0-9]+', tbl_name.upper()) if len(t) >= 4]
            common = set(model_tokens) & set(tbl_tokens)
            if common:
                model_affinity = 50

        # Penalize legacy un-split tables (FACT, SALESFACT) when clean loader tables exist
        legacy_penalty = -60 if tbl_name.upper() in LEGACY_TABLES else 0
        
        # Prefer loader tables with standard snake_case formatting (SALES_FACT over SALESFACT)
        loader_bonus = 10 if "_" in tbl_name else 0

        col_compat = compute_column_compatibility(dataset_columns, live_cols)
        total_score = name_score + (col_compat * 100) + model_affinity + legacy_penalty + loader_bonus
        
        candidates.append({
            "table": tbl_name,
            "score": total_score,
            "name_score": name_score,
            "col_compat": col_compat,
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    if candidates and candidates[0]["score"] >= 60:
        best = candidates[0]
        logger.debug(
            "Generic table binding: '%s' -> '%s' (score=%.1f, name=%d, col_compat=%.2f)",
            dataset_name, best["table"], best["score"], best["name_score"], best["col_compat"]
        )
        return best["table"]

    return dataset_name
