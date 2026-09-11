"""Synonym merge and override helpers.

The merge logic is deliberately additive: missing or malformed synonym sources
become empty lists so a synonym issue cannot break model conversion or sync.
"""

from __future__ import annotations

from typing import Any, Iterable

from semabridge.core.exceptions import AmbiguousColumnReferenceError
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


def _find_column_unambiguously(columns: list[Any], candidate: str, *, dataset_name: str | None) -> Any | None:
    """Find the single column object `candidate` refers to, by name, without
    ever silently picking a winner when more than one column could match.

    Tries an exact (case-sensitive) match against unique_name first, then
    falls back to case-insensitive. At each stage, if MORE THAN ONE column
    matches, raises AmbiguousColumnReferenceError instead of returning the
    first (schema_manager._resolve_duplicate_sibling_physical_name already
    takes this same fail-closed approach for physical-column resolution --
    see that function's docstring for why "first match wins" is unsafe here:
    two distinct columns that collide by name are not interchangeable, and
    picking one arbitrarily risks attaching the wrong column's synonyms to
    an attribute that means something else entirely).

    Returns None when nothing matches at all (not an error -- the caller
    should try its next candidate or give up quietly, same as before).
    """
    exact_matches = [col for col in columns if getattr(col, "unique_name", None) == candidate]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise AmbiguousColumnReferenceError(
            f"Column reference '{candidate}' matches {len(exact_matches)} distinct "
            "columns and cannot be resolved to one without guessing.",
            dataset_name=dataset_name,
            raw_col_name=candidate,
            candidates=[str(getattr(col, "unique_name", "")) for col in exact_matches],
        )

    candidate_cf = str(candidate).casefold()
    case_insensitive_matches = [
        col for col in columns
        if str(getattr(col, "unique_name", "")).casefold() == candidate_cf
    ]
    if len(case_insensitive_matches) == 1:
        return case_insensitive_matches[0]
    if len(case_insensitive_matches) > 1:
        raise AmbiguousColumnReferenceError(
            f"Column reference '{candidate}' matches {len(case_insensitive_matches)} "
            "distinct columns differing only in casing and cannot be resolved to one "
            "without guessing.",
            dataset_name=dataset_name,
            raw_col_name=candidate,
            candidates=[str(getattr(col, "unique_name", "")) for col in case_insensitive_matches],
        )

    return None


def lookup_attribute_synonyms(attr: Any, dataset_obj: Any, phys_col: str | None = None) -> list[str]:
    """Retrieve synonyms for a dimension attribute by looking up the backing column.

    Raises AmbiguousColumnReferenceError when a candidate name matches more
    than one column in the dataset (e.g. two distinct columns collide by
    name) -- callers must catch this and decide how to degrade (see
    dimensions_clause_builder.py's _lookup_attribute_synonyms: keep the
    attribute, skip its synonyms, log a note -- the attribute's own
    physical column was already resolved unambiguously elsewhere, so only
    the synonym enrichment is affected, not the attribute's validity).
    """
    if not dataset_obj:
        return []

    candidates = [
        getattr(attr, "source_column", None),
        getattr(attr, "dataset_column", None),
        getattr(attr, "unique_name", None),
        phys_col,
    ]
    columns = list(getattr(dataset_obj, "columns", []) or [])
    dataset_name = getattr(dataset_obj, "unique_name", None)
    for candidate in candidates:
        if not candidate:
            continue
        col = _find_column_unambiguously(columns, candidate, dataset_name=dataset_name)
        if col:
            return list(getattr(col, "synonyms", []) or [])
    return []

