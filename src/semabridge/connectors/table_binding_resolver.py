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

        # TABLE -> {COLUMN: DATA_TYPE} from information_schema (e.g. TEXT, DATE,
        # NUMBER). Lets DDL generation adapt to an existing table whose column
        # type differs from the model (a date stored as TEXT, ...).
        self.column_types: Dict[str, Dict[str, str]] = {}

        # Upper-case names of tables other SemaBridge models created as their
        # own namespaced tables (table_bindings matched_by "created").
        self.other_model_tables: Set[str] = set()



    def clear(self) -> None:

        super().clear()

        self.pinned_bindings.clear()

        self.other_model_tables.clear()

        self.row_counts.clear()

        self.binding_log.clear()





def clean_ident(name: str) -> str:
    """Normalize identifier using the canonical sanitizer."""
    if not name:
        return ""
    from semabridge.utils.identifiers import IdentifierSanitizer
    return IdentifierSanitizer().to_alias(name)





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

    if not model_col_names or len(model_col_names) == 0 or not live_cols:

        return 0.0



    live_upper = {c.upper() for c in live_cols}

    live_clean = {clean_ident(c) for c in live_cols}

    live_norm = {normalize_col_name(c) for c in live_cols}



    matched = sum(1 for mc in model_col_names if is_column_contained(mc, live_upper, live_clean, live_norm))

    return matched / len(model_col_names)





def _extract_model_namespaces(model_name: Optional[str]) -> Set[str]:
    """Extract possible physical namespace prefixes and model tokens from a model identifier.
    
    Handles:
    - Raw friendly names (e.g. 'Competitive Marketing Analysis' -> 'COMPETITIVE_MARKETING_ANALYSIS')
    - Hex/UUID hashed names (e.g. 'e124ae47cc9646bc9403600f2c8449b2_Competitive Marketing Analysis')
    - Model keys with '::' (e.g. 'proj-comp::Competitive Marketing Analysis')
    - File stems (e.g. 'Competitive Marketing Analysis.pbix')
    """
    if not model_name:
        return set()
    from semabridge.utils.name_translator import build_physical_namespace
    from semabridge.utils.identifiers import clean_pbix_model_name, IdentifierSanitizer

    raw = str(model_name).strip()
    if not raw:
        return set()

    variants = {raw}
    if "::" in raw:
        variants.add(raw.split("::", 1)[1])

    # Strip hex/UUID upload prefix if present: e.g. e124ae47cc9646bc9403600f2c8449b2_Competitive Marketing Analysis
    m = re.match(r"^[0-9a-fA-F\-]{24,36}_(.+)$", raw)
    if m:
        variants.add(m.group(1))

    namespaces: Set[str] = set()
    for v in list(variants):
        if not v:
            continue
        stem = clean_pbix_model_name(v) if v.lower().endswith(".pbix") else v
        stem = stem.strip()
        if not stem:
            continue

        try:
            ns = build_physical_namespace(stem)
            if ns and ns != "MODEL":
                namespaces.add(ns.upper())
        except Exception:
            pass

        try:
            c = IdentifierSanitizer().to_alias(stem)
            if c:
                namespaces.add(c.upper())
        except Exception:
            pass

        alnum = re.sub(r"[^A-Z0-9]", "", stem.upper())
        if alnum:
            namespaces.add(alnum)

    return namespaces


def _matches_model_namespace(tbl_name: str, model_name: Optional[str]) -> bool:
    """Check if a physical table name begins with or belongs to this model's physical namespace."""
    if not model_name or not tbl_name:
        return False
    ns_set = _extract_model_namespaces(model_name)
    if not ns_set:
        return False

    raw_tbl = str(tbl_name).strip().upper()
    clean_tbl = clean_ident(tbl_name).upper()
    alnum_tbl = re.sub(r"[^A-Z0-9]", "", raw_tbl)

    for ns in ns_set:
        if not ns:
            continue
        # Table starts with namespace followed by underscore: e.g. COMPETITIVE_MARKETING_ANALYSIS_DATE
        if raw_tbl.startswith(f"{ns}_") or clean_tbl.startswith(f"{ns}_"):
            return True
        if raw_tbl.startswith(ns) or clean_tbl.startswith(ns) or alnum_tbl.startswith(ns):
            return True
        if f"_{ns}_" in f"_{raw_tbl}_" or f"_{ns}_" in f"_{clean_tbl}_":
            return True

    return False


def rank_table_candidates(
    dataset_name: str,
    dataset_columns: List[str],
    live_schema_metadata: Dict[str, Set[str]],
    model_name: Optional[str] = None,
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
        and not tbl.endswith(("_ENRICHED", "_LATEST"))
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
        from semabridge.utils.identifiers import IdentifierSanitizer

        norm_ds_raw = IdentifierSanitizer(suppress_reserved=False).to_alias(dataset_name)
        tbl_norm_raw = IdentifierSanitizer(suppress_reserved=False).to_alias(tbl_name)
        alnum_ds = re.sub(r"[^A-Z0-9]", "", norm_ds_raw.upper())
        alnum_tbl = re.sub(r"[^A-Z0-9]", "", tbl_norm_raw.upper())

        is_model_match = bool(model_name and _matches_model_namespace(tbl_name, model_name))

        name_score = 0
        if norm_ds == tbl_norm:
            name_score = 100
        elif alnum_ds and alnum_ds == alnum_tbl:
            name_score = 90
        elif is_model_match and (
            tbl_norm.endswith(norm_ds)
            or (alnum_ds and alnum_tbl and alnum_tbl.endswith(alnum_ds))
            or (norm_ds_raw and tbl_norm_raw and tbl_norm_raw.endswith(norm_ds_raw))
            or norm_ds in tbl_norm
        ):
            # Canonical namespaced table for this model: e.g. COMPETITIVE_MARKETING_ANALYSIS_DATE
            name_score = 85
        elif tbl_norm.endswith(norm_ds) or norm_ds.endswith(tbl_norm):
            name_score = 75
        elif norm_ds_raw and tbl_norm_raw and (tbl_norm_raw.endswith(norm_ds_raw) or norm_ds_raw.endswith(tbl_norm_raw)):
            name_score = 70
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
            "model_match": is_model_match,
        })

    # Rank by column compatibility, then name match, prioritizing model match;
    # table name only orders the list.
    candidates.sort(key=lambda x: (-x["col_compat"], -x["name_score"], -int(x.get("model_match", False)), x["table"]))
    return candidates





def _is_other_models_table(table: str, dataset_name: str, live_schema_metadata: Any) -> bool:
    """True when ``table`` is another SemaBridge model's namespaced copy of
    ``dataset_name``: a table another model created (table_bindings, carried
    on the metadata as ``other_model_tables``), else -- when that is unknown
    -- a table named ``<PREFIX>_<DATASET>`` whose prefix is a multi-word
    identifier (CORE_FINANCE_V1_DATE for 'Date'). A single-word prefix
    (DIM_DATE, SPEND_DATE) is taken as a user table and still binds."""
    upper = str(table or "").upper()
    known = getattr(live_schema_metadata, "other_model_tables", None) or set()
    if upper in known:
        return True
    from semabridge.utils.identifiers import IdentifierSanitizer

    # Raw aliases: 'Date' must stay DATE here, not the reserved-word COL_DATE.
    sanitizer = IdentifierSanitizer(suppress_reserved=False)
    suffix = sanitizer.to_alias(dataset_name).upper()
    clean_tbl = sanitizer.to_alias(table).upper()
    if not suffix or not clean_tbl.endswith(f"_{suffix}"):
        return False
    prefix = clean_tbl[: -(len(suffix) + 1)].strip("_")
    return len([part for part in prefix.split("_") if part]) >= 2


def _is_viable(candidate: Dict[str, Any]) -> bool:

    return candidate["col_compat"] >= 0.8





def _log_binding(live_schema_metadata: Any, dataset_name: str, table: str, matched_by: str,

                 tie: Optional[List[Dict[str, Any]]] = None) -> None:

    log = getattr(live_schema_metadata, "binding_log", None)

    if log is None:

        return

    key = clean_ident(dataset_name)
    prev = log.get(key) or {}
    entry = {
        "dataset": dataset_name,
        "table": table,
        "matched_by": matched_by,
        "tie": tie or [],
    }
    if str(prev.get("table") or "").upper() == str(table or "").upper():
        # Same table re-resolved later in the deploy: keep the namespace and
        # the fact that this sync created it (ownership for other projects).
        for k in ("database", "schema"):
            if prev.get(k):
                entry[k] = prev[k]
        if prev.get("matched_by") == "created":
            entry["matched_by"] = "created"
    log[key] = entry





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

    table by columns (>= 0.8). Otherwise the table counts

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



    candidates = rank_table_candidates(dataset_name, dataset_columns, live_schema_metadata, model_name=model_name)

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
            if model_name:
                model_matches = [t for t in tied if _matches_model_namespace(t, model_name)]
                if len(model_matches) == 1:
                    chosen = model_matches[0]
                    logger.info(
                        "Disambiguated table binding for '%s' using model '%s': %s (over %s)",
                        dataset_name, model_name, chosen,
                        ", ".join(t for t in tied if t != chosen),
                    )
                    _log_binding(live_schema_metadata, dataset_name, chosen, "model namespace")
                    return chosen

            if best["name_score"] == 0:
                logger.info(
                    "Ignoring %d column-only ties for '%s' with zero name similarity (%s); treating table as missing.",
                    len(tied), dataset_name, ", ".join(tied),
                )
                return dataset_name
            if model_name and best["name_score"] < 85:
                # Only partial-name matches (e.g. 'Date' vs CORE_FINANCE_V1_DATE,
                # SPEND_DATE): other models' namespaced tables in the shared
                # schema, none of them this model's. Picking one would tie
                # this model to another model's table, and failing stops a
                # brand-new model's dry run whenever two models share a table
                # name. Treat it as missing: the model gets its own
                # <NAMESPACE>_<TABLE> (plan_source_tables "create").
                logger.info(
                    "No table of model '%s' for '%s'; %d other models' tables match only partially (%s). "
                    "Treating it as missing so this model gets its own table.",
                    model_name, dataset_name, len(tied), ", ".join(tied),
                )
                return dataset_name
            raise TableBindingAmbiguityError(dataset_name, tied)

        if (
            model_name
            and 0 < best["name_score"] < 85
            and not best.get("model_match")
            and _is_other_models_table(best["table"], dataset_name, live_schema_metadata)
        ):
            # The only candidate is another SemaBridge model's namespaced
            # table matched by its name ending (e.g. 'Date' vs
            # CORE_FINANCE_V1_DATE): binding would tie this model to that
            # model's table. Treat it as missing, like the tie case above:
            # this model gets its own <NAMESPACE>_<TABLE>.
            logger.info(
                "No table of model '%s' for '%s'; the only partial match %s belongs to another model. "
                "Treating it as missing so this model gets its own table.",
                model_name, dataset_name, best["table"],
            )
            return dataset_name

        matched_by ="model namespace" if best.get("name_score") == 85 else "columns"
        logger.debug(
            "Generic table binding: '%s' -> '%s' (col_compat=%.2f, name_score=%d, matched_by=%s)",
            dataset_name, best["table"], best["col_compat"], best["name_score"], matched_by,
        )
        _log_binding(
            live_schema_metadata,
            dataset_name,
            best["table"],
            matched_by,
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



