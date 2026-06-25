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


def merge_synonyms(
    ui_overrides: list[str] | None = None,
    user_defined: list[str] | None = None,
    auto_generated: list[str] | None = None,
    max_auto: int = 3,
) -> list[str]:
    """Merge synonyms as UI overrides > TMSL/user-defined > auto-generated.

    For backward compatibility with the earlier two-list design,
    ``merge_synonyms(user_defined, auto_generated)`` is also accepted.
    """
    if auto_generated is None and user_defined is not None:
        auto_generated = user_defined
        user_defined = ui_overrides
        ui_overrides = []

    result: list[str] = []
    seen: set[str] = set()

    def add_all(values: Iterable[str], cap: int | None = None) -> None:
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
            added += 1

    add_all(_clean_items(ui_overrides))
    add_all(_clean_items(user_defined))
    add_all(_clean_items(auto_generated), cap=max_auto)
    return result


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

