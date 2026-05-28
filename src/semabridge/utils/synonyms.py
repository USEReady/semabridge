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


_GLOSSARY_CACHE: dict[str, dict[str, list[str]]] = {}


def load_domain_glossary(glossary_path: str | None) -> dict[str, list[str]]:
    """Load a domain glossary YAML file mapping column/term to list of synonyms."""
    if not glossary_path:
        return {}
    
    import yaml
    from pathlib import Path
    
    p = Path(glossary_path)
    if not p.exists():
        logger.warning(f"Domain glossary file not found: {glossary_path}")
        return {}
        
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        
        glossary = {}
        for key, value in raw.items():
            norm_key = str(key).strip().casefold()
            if isinstance(value, list):
                cleaned = [str(v).strip() for v in value if str(v).strip()]
                glossary[norm_key] = cleaned
            elif isinstance(value, str):
                cleaned_str = value.strip()
                if cleaned_str:
                    glossary[norm_key] = [cleaned_str]
        logger.info(f"Loaded {len(glossary)} entries from domain glossary: {glossary_path}")
        return glossary
    except Exception as exc:
        logger.warning(f"Failed to load domain glossary from '{glossary_path}': {exc}")
        return {}


def get_cached_glossary(glossary_path: str | None) -> dict[str, list[str]]:
    if not glossary_path:
        return {}
    path_key = str(glossary_path).strip()
    if path_key not in _GLOSSARY_CACHE:
        _GLOSSARY_CACHE[path_key] = load_domain_glossary(path_key)
    return _GLOSSARY_CACHE[path_key]


def match_column_patterns(field_name: str, patterns: list[Any]) -> list[str]:
    """Match a field name against regex column patterns in behavior."""
    if not field_name or not patterns:
        return []
    import re
    matched = []
    for pattern in patterns:
        match_expr = getattr(pattern, "match", None) or (pattern.get("match") if isinstance(pattern, dict) else None)
        label = getattr(pattern, "label", None) or (pattern.get("label") if isinstance(pattern, dict) else None)
        if match_expr and label:
            try:
                if re.search(match_expr, field_name, re.IGNORECASE):
                    matched.append(str(label).strip())
            except Exception as e:
                logger.warning(f"Invalid regex pattern match '{match_expr}': {e}")
    return matched


def merge_synonyms(
    ui_overrides: list[str] | None = None,
    user_defined: list[str] | None = None,
    auto_generated: list[str] | None = None,
    max_auto: int = 3,
    field_name: str | None = None,
    behavior: Any = None,
) -> list[str]:
    """Merge synonyms layered by priority:
    UI overrides > Domain Glossary > Column Patterns > User defined (TMSL) > Auto-generated (Fallback).
    
    Capped by max_per_field.
    """
    if auto_generated is None and user_defined is not None:
        auto_generated = user_defined
        user_defined = ui_overrides
        ui_overrides = []

    # Safe defaults
    max_cap = 100
    preserve_auto = True
    glossary_syns = []
    pattern_syns = []
    auto_generated_cap = max_auto

    if behavior and hasattr(behavior, "synonyms"):
        syn_behavior = behavior.synonyms
        max_cap = min(max(int(getattr(syn_behavior, "max_per_field", 3)), 1), 7)
        preserve_auto = bool(getattr(syn_behavior, "preserve_auto", True))
        
        # Load glossary if configured
        glossary_path = getattr(syn_behavior, "domain_glossary", None)
        if glossary_path and field_name:
            glossary = get_cached_glossary(glossary_path)
            glossary_syns = glossary.get(str(field_name).strip().casefold(), [])
            
        # Match column patterns if configured
        patterns = getattr(syn_behavior, "column_patterns", []) or []
        if patterns and field_name:
            pattern_syns = match_column_patterns(field_name, patterns)
        auto_generated_cap = max_cap
    else:
        auto_generated_cap = max_auto

    result: list[str] = []
    seen: set[str] = set()

    def add_all(values: Iterable[str], cap: int | None = None) -> None:
        added = 0
        for raw in values:
            if len(result) >= max_cap:
                break
            text = str(raw or "").strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            if cap is not None and added >= cap:
                break
            seen.add(key)
            result.append(text)
            added += 1

    # 1. UI overrides (highest priority)
    add_all(_clean_items(ui_overrides))

    # 2. Domain Glossary synonyms
    add_all(_clean_items(glossary_syns))

    # 3. Regex Column Patterns synonyms
    add_all(_clean_items(pattern_syns))

    # 4. User-defined / TMSL synonyms
    add_all(_clean_items(user_defined))

    # 5. Auto-generated synonyms (only if preserve_auto is True)
    if preserve_auto:
        add_all(_clean_items(auto_generated), cap=auto_generated_cap)

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
