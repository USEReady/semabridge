"""Synonym merge and override helpers.

The merge logic is deliberately additive: missing or malformed synonym sources
become empty lists so a synonym issue cannot break model conversion or sync.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def _clean_items(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def merge_synonyms_with_sources(
    ui_overrides: list[str] | None = None,
    user_defined: list[str] | None = None,
    auto_generated: list[str] | None = None,
    max_auto: int = 3,
) -> tuple[list[str], dict[str, str]]:
    """Merge synonyms as UI overrides > TMSL/user-defined > auto-generated,
    also returning a {synonym: source} provenance map.

    source is one of "manual_override", "tmsl_authored", "auto_generated".
    Report-layer aliases are not a source this function knows about — callers
    that also resolve report aliases (see tmsl_to_osi.py) tag those entries
    themselves after merging, since only they have that information.
    """
    result: list[str] = []
    sources: dict[str, str] = {}
    seen: set[str] = set()

    def add_all(values: Iterable[str], source_tag: str, cap: int | None = None) -> None:
        added = 0
        for raw in values:
            text = str(raw or "").strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            if cap is not None and added >= cap:
                break
            seen.add(key)
            result.append(text)
            sources[text] = source_tag
            added += 1

    add_all(_clean_items(ui_overrides), "manual_override")
    add_all(_clean_items(user_defined), "tmsl_authored")
    add_all(_clean_items(auto_generated), "auto_generated", cap=max_auto)
    return result, sources


def merge_synonyms(
    ui_overrides: list[str] | None = None,
    user_defined: list[str] | None = None,
    auto_generated: list[str] | None = None,
    max_auto: int = 3,
) -> list[str]:
    """Merge synonyms as UI overrides > TMSL/user-defined > auto-generated.

    For backward compatibility with the earlier two-list design,
    ``merge_synonyms(user_defined, auto_generated)`` is also accepted.

    Thin wrapper over merge_synonyms_with_sources() for callers that don't
    need provenance — signature and behavior unchanged.
    """
    if auto_generated is None and user_defined is not None:
        auto_generated = user_defined
        user_defined = ui_overrides
        ui_overrides = []

    merged, _sources = merge_synonyms_with_sources(ui_overrides, user_defined, auto_generated, max_auto)
    return merged


def synonym_override_key(model_name: str, table_name: str, object_name: str) -> tuple[str, str, str]:
    return (
        str(model_name or "").strip().casefold(),
        str(table_name or "").strip().casefold(),
        str(object_name or "").strip().casefold(),
    )


def load_synonym_overrides(project_id: str | None) -> dict[tuple[str, str, str], list[str]]:
    """Load UI synonym overrides for a project.

    The override table is optional for compatibility with older deployments.
    Missing table/model/schema errors are logged and treated as no overrides.
    """
    project_id = str(project_id or "").strip()
    if not project_id:
        return {}

    try:
        from sqlalchemy import select

        from semabridge.repository.orm.models import SynonymOverride
        from semabridge.repository.orm.session_factory import db_manager

        overrides: dict[tuple[str, str, str], list[str]] = {}
        with db_manager.get_session() as session:
            rows = session.execute(
                select(SynonymOverride).where(SynonymOverride.project_id == project_id)
            ).scalars().all()
            for row in rows:
                overrides[
                    synonym_override_key(row.model_name, row.table_name, row.column_name)
                ] = _clean_items(row.synonyms)
        return overrides
    except Exception as exc:
        logger.warning(
            "Synonym override table unavailable for project '%s'; continuing without UI overrides: %s",
            project_id,
            exc,
        )
        return {}


def lookup_synonym_override(
    overrides: dict[tuple[str, str, str], list[str]] | None,
    model_names: Iterable[str],
    table_name: str,
    object_name: str,
) -> list[str]:
    if not overrides:
        return []
    for model_name in model_names:
        found = overrides.get(synonym_override_key(model_name, table_name, object_name))
        if found:
            return list(found)
    return []


def load_ontology_overrides(project_id: str | None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Load full UI ontology overrides (synonyms, description, business_rules, data_steward) for a project."""
    project_id = str(project_id or "").strip()
    if not project_id:
        return {}

    try:
        from sqlalchemy import select

        from semabridge.repository.orm.models import SynonymOverride
        from semabridge.repository.orm.session_factory import db_manager

        overrides: dict[tuple[str, str, str], dict[str, Any]] = {}
        with db_manager.get_session() as session:
            rows = session.execute(
                select(SynonymOverride).where(SynonymOverride.project_id == project_id)
            ).scalars().all()
            for row in rows:
                key = synonym_override_key(row.model_name, row.table_name, row.column_name)
                overrides[key] = {
                    "synonyms": _clean_items(row.synonyms),
                    "description": getattr(row, "description", None),
                    "business_rules": list(getattr(row, "business_rules", []) or []),
                    "data_steward": getattr(row, "data_steward", None),
                }
        return overrides
    except Exception as exc:
        logger.warning(
            "Ontology override table unavailable for project '%s'; continuing without UI overrides: %s",
            project_id,
            exc,
        )
        return {}


def lookup_ontology_override(
    overrides: dict[tuple[str, str, str], dict[str, Any]] | None,
    model_names: Iterable[str],
    table_name: str,
    object_name: str,
) -> dict[str, Any] | None:
    if not overrides:
        return None
    for model_name in model_names:
        found = overrides.get(synonym_override_key(model_name, table_name, object_name))
        if found:
            return found
    return None


def lookup_attribute_synonyms(attr: Any, dataset_obj: Any, phys_col: str | None = None) -> list[str]:
    """Retrieve synonyms for a dimension attribute by looking up the backing column."""
    if not dataset_obj:
        return []

    candidates = [
        getattr(attr, "source_column", None),
        getattr(attr, "dataset_column", None),
        getattr(attr, "unique_name", None),
        phys_col,
    ]
    columns = list(getattr(dataset_obj, "columns", []) or [])
    for candidate in candidates:
        if not candidate:
            continue
        col = dataset_obj.get_column(candidate) if hasattr(dataset_obj, "get_column") else None
        if not col:
            col = next(
                (
                    item for item in columns
                    if str(getattr(item, "unique_name", "")).casefold() == str(candidate).casefold()
                ),
                None,
            )
        if col:
            return list(getattr(col, "synonyms", []) or [])
    return []


def _row_timestamp(row: Any):
    """updated_at (or created_at) of a SynonymOverride row as a naive UTC
    datetime for comparison, or None when unknown."""
    from datetime import timezone

    value = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def copy_synonym_overrides(
    source_project_ids: Iterable[str],
    target_project_id: str,
    *,
    model_name_by_source: dict[str, str] | None = None,
) -> int:
    """Copy UI synonym overrides saved during a dry run (under transient
    ``preview-*`` project ids, one per model) into the real project, so the
    synonyms the user edited in the dry run are the ones that get deployed.

    Rules:
      - Each preview project holds exactly one model's rows. When
        ``model_name_by_source`` maps a preview id to the model name the real
        project deploys that model under, its rows are stored under that
        model name only -- one model's synonyms never land on another model.
      - A row already on the target is replaced only when the preview row is
        strictly NEWER than it. Synonyms the user edits on the real project
        after the dry run therefore are never overwritten by an older dry-run
        value, however often the project is saved.
      - When several sources carry the same key, the first source wins.

    The synonym values themselves are copied verbatim; how synonyms are
    generated, resolved or looked up is unchanged. Returns the number of
    target rows created/updated. Errors are logged and return 0 -- synonym
    migration must never block saving a project.
    """
    target = str(target_project_id or "").strip()
    name_map = {str(k).strip(): str(v).strip() for k, v in (model_name_by_source or {}).items() if str(v or "").strip()}
    sources = []
    for pid in source_project_ids or []:
        text = str(pid or "").strip()
        if text and text != target and text not in sources:
            sources.append(text)
    if not target or not sources:
        return 0

    try:
        from sqlalchemy import select

        from semabridge.repository.orm.models import SynonymOverride
        from semabridge.repository.orm.session_factory import db_manager

        copied = 0
        with db_manager.get_session() as session:
            merged: dict[tuple[str, str, str], tuple[tuple[str, str, str], list[str], str | None, list[str], str | None, Any]] = {}
            for source in sources:
                rows = session.execute(
                    select(SynonymOverride).where(SynonymOverride.project_id == source)
                ).scalars().all()
                for row in rows:
                    model_name = name_map.get(source) or row.model_name
                    key = synonym_override_key(model_name, row.table_name, row.column_name)
                    if key in merged:
                        continue
                    merged[key] = (
                        (model_name, row.table_name, row.column_name),
                        _clean_items(row.synonyms),
                        getattr(row, "description", None),
                        list(getattr(row, "business_rules", []) or []),
                        getattr(row, "data_steward", None),
                        _row_timestamp(row),
                    )
            if not merged:
                return 0
            existing_rows = session.execute(
                select(SynonymOverride).where(SynonymOverride.project_id == target)
            ).scalars().all()
            existing = {
                synonym_override_key(r.model_name, r.table_name, r.column_name): r
                for r in existing_rows
            }
            for key, ((model_name, table_name, column_name), values, desc, rules, steward, source_ts) in merged.items():
                row = existing.get(key)
                if row is not None:
                    target_ts = _row_timestamp(row)
                    if source_ts is None or target_ts is None or source_ts <= target_ts:
                        continue
                    row.synonyms = values
                    if desc is not None:
                        row.description = desc
                    if rules:
                        row.business_rules = rules
                    if steward is not None:
                        row.data_steward = steward
                    # Carry the dry-run edit's own timestamp, so "newer"
                    # always compares edit times, not copy times.
                    row.updated_at = source_ts
                else:
                    row = SynonymOverride(
                        project_id=target,
                        model_name=model_name,
                        table_name=table_name,
                        column_name=column_name,
                    )
                    row.synonyms = values
                    if desc is not None:
                        row.description = desc
                    if rules:
                        row.business_rules = rules
                    if steward is not None:
                        row.data_steward = steward
                    if source_ts is not None:
                        row.created_at = source_ts
                        row.updated_at = source_ts
                    session.add(row)
                copied += 1
        logger.info(
            "Migrated %d synonym override(s) from %s to project '%s'",
            copied,
            ", ".join(sources),
            target,
        )
        return copied
    except Exception as exc:
        logger.warning(
            "Synonym override migration to project '%s' failed (continuing): %s",
            target,
            exc,
        )
        return 0


def is_filtered_metric(metric: Any) -> bool:
    """Determine generically whether a metric contains row-level filter predicates.

    A filtered metric restricts which rows are aggregated (e.g. FILTER(...),
    CASE WHEN ... THEN, WHERE, or IFF), whereas an unfiltered base metric aggregates
    all rows of its underlying table/concept.
    """
    import re

    sql_expr = str(
        getattr(metric, "sql_expression", None)
        or (metric.get("sql_expression") if isinstance(metric, dict) else None)
        or ""
    ).strip()
    if sql_expr:
        if re.search(r"\bCASE\s+WHEN\b|\bWHERE\b|\bIFF\s*\(|\bFILTER\s*\(", sql_expr, re.I):
            return True
        return False

    dax_expr = str(
        getattr(metric, "expression", None)
        or getattr(metric, "dax_expression", None)
        or (metric.get("expression") if isinstance(metric, dict) else None)
        or ""
    ).strip()
    if not dax_expr:
        return False
    if re.search(r"\bFILTER\s*\(", dax_expr, re.I):
        return True
    if re.search(r"\bCALCULATE\s*\(.+,\s*[^)]*(?:=|<|>|ISBLANK|NOT|IN)\b", dax_expr, re.I):
        return True
    if re.search(r"\bCASE\s+WHEN\b|\bWHERE\b|\bIFF\s*\(", dax_expr, re.I):
        return True
    return False


def get_metric_qualifier_word(metric: Any) -> Optional[str]:
    """Derive qualifier prefix from metric label or unique_name."""
    import re

    name = str(
        getattr(metric, "label", None)
        or getattr(metric, "name", None)
        or getattr(metric, "unique_name", None)
        or (metric.get("label") if isinstance(metric, dict) else None)
        or (metric.get("name") if isinstance(metric, dict) else None)
        or ""
    ).strip()
    words = [w for w in re.split(r"[\s_]+", name) if w]
    if len(words) > 1:
        return words[0]
    return name or None


def disambiguate_metric_synonyms(
    metrics: List[Any],
    view_name: Optional[str] = None,
) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """Ensure no two metrics in the same view share the same synonym.

    When two metrics compete for a generic term (e.g. an unfiltered base count vs
    a filtered count), the unfiltered/base metric gets the generic term; filtered
    metrics receive qualified terms (the filter word + the noun).

    Returns:
        (metrics, list_of_collision_records)
    """
    collisions_recorded: List[Dict[str, Any]] = []
    if not metrics:
        return metrics, collisions_recorded

    def _get_name(m: Any) -> str:
        if isinstance(m, dict):
            return str(m.get("unique_name") or m.get("name") or "metric")
        return str(getattr(m, "unique_name", None) or getattr(m, "name", None) or getattr(m, "label", None) or "metric")

    def _get_syns(m: Any) -> List[str]:
        if isinstance(m, dict):
            return list(m.get("synonyms") or [])
        return list(getattr(m, "synonyms", None) or [])

    def _set_syns(m: Any, syns: List[str]) -> None:
        if isinstance(m, dict):
            m["synonyms"] = syns
        else:
            setattr(m, "synonyms", syns)

    # 1. Map each synonym to list of metrics that claim it
    syn_claimants: Dict[str, List[Any]] = {}
    for m in metrics:
        for s in _get_syns(m):
            cleaned = str(s or "").strip()
            if cleaned:
                syn_claimants.setdefault(cleaned.casefold(), []).append((cleaned, m))

    # 2. Identify collisions where >= 2 metrics claim the same casefolded term
    for cf_term, items in syn_claimants.items():
        distinct_metrics = []
        seen_names = set()
        for raw_s, m in items:
            m_name = _get_name(m)
            if m_name not in seen_names:
                seen_names.add(m_name)
                distinct_metrics.append((raw_s, m))

        if len(distinct_metrics) < 2:
            continue

        raw_term = distinct_metrics[0][0]
        unfiltered_claimants = [m for _, m in distinct_metrics if not is_filtered_metric(m)]
        filtered_claimants = [m for _, m in distinct_metrics if is_filtered_metric(m)]

        winner = None
        if len(unfiltered_claimants) == 1:
            winner = unfiltered_claimants[0]
        else:
            winner = distinct_metrics[0][1]
            for _, m in distinct_metrics:
                m_label = str(getattr(m, "label", None) or getattr(m, "name", None) or "").strip()
                if m_label.casefold() == cf_term:
                    winner = m
                    break

        winner_name = _get_name(winner)
        losers = [m for _, m in distinct_metrics if m is not winner]

        collisions_recorded.append({
            "view": view_name or "default",
            "term": raw_term,
            "metrics": [_get_name(m) for _, m in distinct_metrics],
            "resolved_to": winner_name,
        })

        for loser in losers:
            l_syns = _get_syns(loser)
            new_l_syns = []
            qualifier = get_metric_qualifier_word(loser)
            for s in l_syns:
                if str(s).strip().casefold() == cf_term:
                    if qualifier and qualifier.casefold() not in cf_term:
                        candidate = f"{qualifier} {s}".strip()
                        if candidate.casefold() != cf_term and candidate not in new_l_syns:
                            new_l_syns.append(candidate)
                else:
                    new_l_syns.append(s)
            _set_syns(loser, new_l_syns)

    return metrics, collisions_recorded

