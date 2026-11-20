"""Unity Catalog Loose Table & Column Matcher for Databricks (Step 7).

Matches Power BI / SML datasets and columns to real Unity Catalog tables even
when PBIX metadata carries foreign database/schema names (such as Snowflake's
``semabridge.public``) or casing/spacing/underscore differences.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from semabridge.sml.models import SMLDataset, SMLModel


def _strip_quotes(value: str) -> str:
    return str(value or "").strip().strip("`\"[]'")


def _norm_ident(value: str) -> str:
    """Normalize an identifier for loose matching (case-, space-, underscore-, hyphen-insensitive)."""
    cleaned = _strip_quotes(value)
    return re.sub(r"[\s_\-`\"\[\]]+", "", cleaned).lower()


def _parse_fqn(raw: str) -> Tuple[str, str, str]:
    """Parse a 3-part ``catalog.schema.table`` string into ``(catalog, schema, table)``."""
    parts = [_strip_quotes(p) for p in str(raw or "").split(".") if p.strip()]
    if len(parts) >= 3:
        return parts[-3], parts[-2], parts[-1]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    if len(parts) == 1:
        return "", "", parts[0]
    return "", "", ""


def _quote_fqn(catalog: str, schema: str, table: str) -> str:
    parts = [p for p in (catalog, schema, table) if p]
    return ".".join(f"`{p.replace('`', '``')}`" for p in parts)


@dataclass
class _UCTableEntry:
    catalog: str
    schema: str
    name: str
    full_name: str
    columns: List[str] = field(default_factory=list)

    @property
    def quoted_fqn(self) -> str:
        return _quote_fqn(self.catalog, self.schema, self.name)


@dataclass
class MatchedTable:
    """Result of matching one SML dataset to a Unity Catalog table."""

    dataset_name: str
    full_name: str
    quoted_fqn: str
    catalog: str
    schema: str
    table: str
    match_reason: str
    confidence: float
    column_matches: Dict[str, str] = field(default_factory=dict)
    unmatched_columns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "full_name": self.full_name,
            "quoted_fqn": self.quoted_fqn,
            "catalog": self.catalog,
            "schema": self.schema,
            "table": self.table,
            "match_reason": self.match_reason,
            "confidence": self.confidence,
            "column_matches": dict(self.column_matches),
            "unmatched_columns": list(self.unmatched_columns),
        }


@dataclass
class TableMatchReport:
    """Aggregate report for matching an SMLModel against Unity Catalog tables."""

    matched: Dict[str, MatchedTable] = field(default_factory=dict)
    missing_facts: List[str] = field(default_factory=list)
    missing_lookups: List[str] = field(default_factory=list)
    ambiguous: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched": {k: v.to_dict() for k, v in self.matched.items()},
            "missing_facts": list(self.missing_facts),
            "missing_lookups": list(self.missing_lookups),
            "ambiguous": {k: list(v) for k, v in self.ambiguous.items()},
        }


def _normalize_uc_tables(uc_tables: Iterable[Dict[str, Any]]) -> List[_UCTableEntry]:
    entries: List[_UCTableEntry] = []
    for raw in uc_tables or []:
        if not isinstance(raw, dict):
            continue
        catalog = _strip_quotes(raw.get("catalog") or raw.get("catalog_name") or "")
        schema = _strip_quotes(raw.get("schema") or raw.get("schema_name") or "")
        name = _strip_quotes(raw.get("name") or raw.get("table_name") or "")
        full_name = _strip_quotes(raw.get("full_name") or "")

        if full_name and (not catalog or not schema or not name):
            p_cat, p_sch, p_tbl = _parse_fqn(full_name)
            catalog = catalog or p_cat
            schema = schema or p_sch
            name = name or p_tbl

        if not name:
            continue
        if not full_name:
            full_name = ".".join(p for p in (catalog, schema, name) if p)

        raw_cols = raw.get("columns") or []
        cols: List[str] = []
        for col in raw_cols:
            if isinstance(col, dict):
                col_name = str(col.get("name") or col.get("column_name") or "").strip()
            else:
                col_name = str(col or "").strip()
            if col_name:
                cols.append(col_name)

        entries.append(
            _UCTableEntry(
                catalog=catalog,
                schema=schema,
                name=name,
                full_name=full_name,
                columns=cols,
            )
        )
    return entries


def match_columns(
    dataset_columns: Iterable[str],
    uc_columns: Iterable[str],
) -> Tuple[Dict[str, str], List[str]]:
    """Match source dataset column names to physical Unity Catalog columns.

    Priority per column:
        1. Exact match (case-sensitive)
        2. Case-insensitive match
        3. Loose normalized match (case-, space-, underscore-, hyphen-insensitive)
    """
    physical_cols = [str(c).strip() for c in uc_columns if str(c or "").strip()]
    exact_set = set(physical_cols)
    lower_map: Dict[str, str] = {}
    norm_map: Dict[str, str] = {}
    for col in physical_cols:
        lower_map.setdefault(col.lower(), col)
        norm_map.setdefault(_norm_ident(col), col)

    matches: Dict[str, str] = {}
    unmatched: List[str] = []
    for src_col in dataset_columns:
        src = str(src_col or "").strip()
        if not src:
            continue
        if src in exact_set:
            matches[src] = src
        elif src.lower() in lower_map:
            matches[src] = lower_map[src.lower()]
        elif _norm_ident(src) in norm_map:
            matches[src] = norm_map[_norm_ident(src)]
        else:
            unmatched.append(src)
    return matches, unmatched


def _dataset_source_columns(dataset: SMLDataset) -> List[str]:
    """Return physical-candidate column names for an SMLDataset."""
    cols: List[str] = []
    for col in getattr(dataset, "columns", []) or []:
        if getattr(col, "is_calculated", False):
            continue
        name = str(getattr(col, "source_column", None) or getattr(col, "unique_name", "") or "").strip()
        if name:
            cols.append(name)
    return cols


def _score_column_overlap(
    ds_cols: List[str],
    uc_entry: _UCTableEntry,
) -> Tuple[float, Dict[str, str], List[str]]:
    if not ds_cols or not uc_entry.columns:
        return 0.0, {}, list(ds_cols)
    col_matches, unmatched = match_columns(ds_cols, uc_entry.columns)
    matched_count = len(col_matches)
    if matched_count == 0:
        return 0.0, col_matches, unmatched
    recall = matched_count / len(ds_cols)
    union_size = len(ds_cols) + len(uc_entry.columns) - matched_count
    jaccard = matched_count / max(union_size, 1)
    score = 0.8 * recall + 0.2 * jaccard
    return score, col_matches, unmatched


def _build_matched_table(
    dataset: SMLDataset,
    entry: _UCTableEntry,
    *,
    match_reason: str,
    confidence: float,
    ds_cols: List[str],
) -> MatchedTable:
    col_matches, unmatched = match_columns(ds_cols, entry.columns)
    return MatchedTable(
        dataset_name=dataset.unique_name,
        full_name=entry.full_name,
        quoted_fqn=entry.quoted_fqn,
        catalog=entry.catalog,
        schema=entry.schema,
        table=entry.name,
        match_reason=match_reason,
        confidence=round(confidence, 4),
        column_matches=col_matches,
        unmatched_columns=unmatched,
    )


def _lookup_override(
    dataset: SMLDataset,
    saved_overrides: Optional[Dict[str, str]],
) -> Optional[str]:
    if not saved_overrides:
        return None
    candidates = [dataset.unique_name]
    if dataset.source_table:
        candidates.append(dataset.source_table)

    # Exact match
    for cand in candidates:
        if cand in saved_overrides and str(saved_overrides[cand] or "").strip():
            return str(saved_overrides[cand]).strip()

    # Case-insensitive & loose-normalized match
    lower_overrides = {str(k).strip().lower(): str(v).strip() for k, v in saved_overrides.items() if str(v or "").strip()}
    norm_overrides = {_norm_ident(k): str(v).strip() for k, v in saved_overrides.items() if str(v or "").strip()}
    for cand in candidates:
        if cand.lower() in lower_overrides:
            return lower_overrides[cand.lower()]
        if _norm_ident(cand) in norm_overrides:
            return norm_overrides[_norm_ident(cand)]
    return None


def _is_fact_dataset(dataset: SMLDataset, sml_model: SMLModel) -> bool:
    if bool(getattr(dataset, "is_fact", False)):
        return True
    ds_upper = str(dataset.unique_name or "").upper()
    for rel in getattr(sml_model, "relationships", []) or []:
        if str(getattr(rel, "from_dataset", "") or "").upper() == ds_upper:
            return True
    return False


def match_databricks_tables(
    sml_model: SMLModel,
    uc_tables: Iterable[Dict[str, Any]],
    *,
    target_catalog: str = "",
    target_schema: str = "",
    saved_overrides: Optional[Dict[str, str]] = None,
    tie_margin: float = 0.05,
    min_overlap_score: float = 0.4,
) -> TableMatchReport:
    """Match SML datasets and columns to real Unity Catalog tables.

    Matching priority per dataset:
        1. ``saved_overrides`` (always wins when present)
        2. Exact FQN match (dataset's declared ``source_database.source_schema.table``
           or target ``target_catalog.target_schema.table``)
        3. Target ``catalog.schema`` + loose normalized name match
           (case-, space-, underscore-insensitive), then cross-schema loose name match
        4. Column-overlap scoring across candidate tables; close ties are reported
           in ``report.ambiguous`` instead of guessing silently.
    """
    entries = _normalize_uc_tables(uc_tables)
    t_cat = _strip_quotes(target_catalog).lower()
    t_sch = _strip_quotes(target_schema).lower()

    # Index UC tables by lowercased FQN for quick lookup
    by_fqn: Dict[Tuple[str, str, str], _UCTableEntry] = {
        (e.catalog.lower(), e.schema.lower(), e.name.lower()): e for e in entries
    }
    target_entries = [
        e for e in entries
        if (not t_cat or e.catalog.lower() == t_cat)
        and (not t_sch or e.schema.lower() == t_sch)
    ]

    report = TableMatchReport()

    for dataset in getattr(sml_model, "datasets", []) or []:
        ds_name = str(dataset.unique_name or "").strip()
        if not ds_name:
            continue
        ds_cols = _dataset_source_columns(dataset)
        src_table = _strip_quotes(getattr(dataset, "source_table", "") or ds_name)
        src_cat = _strip_quotes(getattr(dataset, "source_database", "") or "")
        src_sch = _strip_quotes(getattr(dataset, "source_schema", "") or "")

        # ── Tier 1: saved_overrides (always wins) ────────────────────────
        override_val = _lookup_override(dataset, saved_overrides)
        if override_val:
            o_cat, o_sch, o_tbl = _parse_fqn(override_val)
            o_cat = o_cat or _strip_quotes(target_catalog)
            o_sch = o_sch or _strip_quotes(target_schema)
            existing = by_fqn.get((o_cat.lower(), o_sch.lower(), o_tbl.lower()))
            entry = existing or _UCTableEntry(
                catalog=o_cat,
                schema=o_sch,
                name=o_tbl,
                full_name=".".join(p for p in (o_cat, o_sch, o_tbl) if p),
                columns=[],
            )
            report.matched[ds_name] = _build_matched_table(
                dataset,
                entry,
                match_reason="saved_override",
                confidence=1.0,
                ds_cols=ds_cols,
            )
            continue

        # ── Tier 2: Exact FQN match ──────────────────────────────────────
        exact_match: Optional[_UCTableEntry] = None
        if src_cat and src_sch:
            exact_match = by_fqn.get((src_cat.lower(), src_sch.lower(), src_table.lower()))
            if exact_match is None and ds_name.lower() != src_table.lower():
                exact_match = by_fqn.get((src_cat.lower(), src_sch.lower(), ds_name.lower()))
        if exact_match is None and t_cat and t_sch:
            exact_match = by_fqn.get((t_cat, t_sch, src_table.lower()))
            if exact_match is None and ds_name.lower() != src_table.lower():
                exact_match = by_fqn.get((t_cat, t_sch, ds_name.lower()))
        if exact_match is not None:
            report.matched[ds_name] = _build_matched_table(
                dataset,
                exact_match,
                match_reason="exact_fqn",
                confidence=1.0,
                ds_cols=ds_cols,
            )
            continue

        # ── Tier 3: Loose normalized name match (prefer target catalog.schema) ─
        name_norms = {_norm_ident(ds_name), _norm_ident(src_table)}
        name_norms.discard("")

        def _resolve_candidates(
            candidates: List[_UCTableEntry],
            reason: str,
            base_conf: float,
        ) -> Optional[str]:
            """Resolve candidate list. Returns 'matched', 'ambiguous', or None."""
            if not candidates:
                return None
            if len(candidates) == 1:
                report.matched[ds_name] = _build_matched_table(
                    dataset,
                    candidates[0],
                    match_reason=reason,
                    confidence=base_conf,
                    ds_cols=ds_cols,
                )
                return "matched"
            # Multiple name-matched candidates: try breaking tie with column overlap
            scored = [
                (_score_column_overlap(ds_cols, c)[0], c)
                for c in candidates
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            top_score = scored[0][0]
            tied = [c for s, c in scored if (top_score - s) <= tie_margin]
            if len(tied) == 1 and top_score > 0:
                report.matched[ds_name] = _build_matched_table(
                    dataset,
                    tied[0],
                    match_reason=reason,
                    confidence=base_conf,
                    ds_cols=ds_cols,
                )
                return "matched"
            report.ambiguous[ds_name] = [c.full_name for c in tied]
            return "ambiguous"

        if t_cat or t_sch:
            target_name_matches = [
                e for e in target_entries if _norm_ident(e.name) in name_norms
            ]
            outcome = _resolve_candidates(
                target_name_matches, "target_schema_loose_name", 0.95
            )
            if outcome in ("matched", "ambiguous"):
                continue

        all_name_matches = [e for e in entries if _norm_ident(e.name) in name_norms]
        outcome = _resolve_candidates(all_name_matches, "loose_name", 0.85)
        if outcome in ("matched", "ambiguous"):
            continue

        # ── Tier 4: Column-overlap scoring across candidate tables ───────
        pool = target_entries if target_entries else entries
        scored_overlap: List[Tuple[float, int, _UCTableEntry]] = []
        for entry in pool:
            score, col_matches, _ = _score_column_overlap(ds_cols, entry)
            if score >= min_overlap_score and (len(col_matches) >= 2 or (len(ds_cols) == 1 and len(col_matches) == 1)):
                scored_overlap.append((score, len(col_matches), entry))

        # If nothing matched in target schema, broaden to all UC tables
        if not scored_overlap and pool is not entries:
            for entry in entries:
                score, col_matches, _ = _score_column_overlap(ds_cols, entry)
                if score >= min_overlap_score and (len(col_matches) >= 2 or (len(ds_cols) == 1 and len(col_matches) == 1)):
                    scored_overlap.append((score, len(col_matches), entry))

        if scored_overlap:
            scored_overlap.sort(key=lambda item: (item[0], item[1]), reverse=True)
            top_score = scored_overlap[0][0]
            tied = [
                entry
                for score, _, entry in scored_overlap
                if (top_score - score) <= tie_margin
            ]
            if len(tied) > 1:
                report.ambiguous[ds_name] = [e.full_name for e in tied]
                continue
            best_entry = scored_overlap[0][2]
            report.matched[ds_name] = _build_matched_table(
                dataset,
                best_entry,
                match_reason="column_overlap",
                confidence=top_score,
                ds_cols=ds_cols,
            )
            continue

        # ── Unmatched: classify as missing fact or missing lookup ────────
        if _is_fact_dataset(dataset, sml_model):
            report.missing_facts.append(ds_name)
        else:
            report.missing_lookups.append(ds_name)

    return report
