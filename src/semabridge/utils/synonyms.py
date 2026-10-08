"""Synonym merge and override helpers.

The merge logic is deliberately additive: missing or malformed synonym sources
become empty lists so a synonym issue cannot break model conversion or sync.
"""

from __future__ import annotations

from typing import Any, Iterable

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
            merged: dict[tuple[str, str, str], tuple[tuple[str, str, str], list[str], Any]] = {}
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
            for key, ((model_name, table_name, column_name), values, source_ts) in merged.items():
                row = existing.get(key)
                if row is not None:
                    target_ts = _row_timestamp(row)
                    if source_ts is None or target_ts is None or source_ts <= target_ts:
                        continue
                    if list(row.synonyms) == values:
                        continue
                    row.synonyms = values
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
