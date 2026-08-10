"""Identifier sanitization, naming utilities, and alias resolution."""

import re
from typing import Optional, Set

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def sanitize_col_name(emitter, name: str) -> str:
    """Sanitize column name via unified IdentifierSanitizer (Mandate 1)."""
    return emitter._id.sanitize_column(name)


def sanitize_semantic_name(emitter, name: str) -> str:
    """Sanitize semantic name and ensure it does not start with a digit."""
    sanitized = emitter._id.sanitize_column(name)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def to_snowflake_relationship_name(emitter, name: str) -> str:
    """Convert canonical relationship name to Snowflake-layer identifier.

    Canonical model names retain the REL_ prefix. Snowflake output removes
    only that leading REL_ for cleaner relationship identifiers.
    """
    rel_name = sanitize_semantic_name(emitter, name)
    if rel_name.startswith("REL_"):
        return rel_name[4:]
    return rel_name


def get_safe_object_name(emitter, name: str) -> str:
    """Sanitize object name for Snowflake."""
    return emitter._id.sanitize_column(name)


def sanitize_alias(emitter, name: str) -> str:
    """Sanitize alias names and ensure they do not start with a digit."""
    sanitized = emitter._id.sanitize_alias(name)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def resolve_unique_table_alias(
    emitter,
    base_alias: str,
    used_aliases: set[str],
) -> str:
    """Ensure table alias is unique within a semantic view TABLES clause."""
    if base_alias not in used_aliases:
        used_aliases.add(base_alias)
        return base_alias

    idx = 2
    while True:
        candidate = f"{base_alias}_{idx}"
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            return candidate
        idx += 1


def resolve_unique_metric_alias(
    emitter,
    base_alias: str,
    used_aliases: set[str],
    original_metric_name: str,
) -> str:
    """Ensure metric alias is unique within a single METRICS clause."""
    if base_alias not in used_aliases:
        used_aliases.add(base_alias)
        return base_alias

    idx = 2
    while True:
        candidate = f"{base_alias}_{idx}"
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            logger.warning(
                "Metric alias collision for '%s' (base '%s'); using '%s'",
                original_metric_name,
                base_alias,
                candidate,
            )
            return candidate
        idx += 1


def resolve_unique_dimension_alias(
    emitter,
    base_alias: str,
    table_alias: str,
    used_aliases: set[str],
    original_dimension_name: str,
) -> str:
    """Ensure dimension alias is unique across a semantic view."""
    if base_alias not in used_aliases:
        used_aliases.add(base_alias)
        return base_alias

    idx = 2
    while True:
        candidate = sanitize_semantic_name(emitter, f"{base_alias}_{idx}")
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            logger.warning(
                "Dimension alias collision for '%s' (base '%s'); using '%s'",
                original_dimension_name,
                base_alias,
                candidate,
            )
            return candidate
        idx += 1


def quote_if_needed(emitter, name: str) -> str:
    """Quote column names to preserve case."""
    return emitter._id.quote(name)


def safe_table_name(emitter, name: str) -> str:
    """Sanitize a physical table name via unified IdentifierSanitizer."""
    return emitter._id.sanitize_table_name(name)


def safe_table_name_static(name: str) -> str:
    """Sanitise a string for use as a Snowflake table/view name.

    Static fallback — use the instance method ``safe_table_name``
    when a ``self`` reference is available for consistency.

    Args:
        name: Raw model or metric name.

    Returns:
        Snowflake-safe uppercase identifier. Never empty — a fully
        degenerate input (e.g. all punctuation/whitespace, or a name
        made entirely of characters this function strips to
        underscores) falls back to ``"UNKNOWN"``, matching the
        non-empty guarantee every sibling sanitizer in
        ``identifiers.py`` (``sanitize_column`` → ``"COLUMN_UNKNOWN"``,
        ``sanitize_alias`` → ``"ALIAS"``) already makes. Without this,
        callers that build a compound identifier by concatenating this
        result with a fixed separator (e.g. ``f"{prefix}_{safe}"``)
        would silently emit a truncated or bare-separator identifier.
    """
    if not name:
        return "UNKNOWN"
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    if not safe:
        return "UNKNOWN"
    return safe.upper()


def resolve_column_name_for_dataset(
    known_columns: set[str],
    candidate: str,
) -> Optional[str]:
    """Resolve LLM-drifted column names to an actual dataset column."""
    if not known_columns:
        return None

    if candidate in known_columns:
        return candidate

    # underscore-insensitive match: IS_VAN_ARSDEL -> ISVANARSDEL
    compact = candidate.replace("_", "")
    for col in known_columns:
        if col.replace("_", "") == compact:
            return col

    # common LLM drift: TOTAL_UNITS -> UNITS
    if candidate.startswith("TOTAL_"):
        base = candidate[len("TOTAL_"):]
        if base in known_columns:
            return base

    # lightweight stemming for token overlap: UNITS ~= UNIT, CATEGORIES ~= CATEGORY
    def _stem(token: str) -> str:
        t = token.upper()
        if len(t) > 4 and t.endswith("IES"):
            return t[:-3] + "Y"
        if len(t) > 3 and t.endswith("S"):
            return t[:-1]
        return t

    candidate_tokens = [t for t in candidate.split("_") if t]
    if candidate_tokens:
        scored: list[tuple[int, str]] = []
        candidate_token_set = {_stem(t) for t in candidate_tokens}
        for col in known_columns:
            col_tokens = [t for t in col.split("_") if t]
            if not col_tokens:
                continue
            col_token_set = {_stem(t) for t in col_tokens}
            overlap = len(candidate_token_set.intersection(col_token_set))
            if overlap == 0:
                continue
            score = overlap * 10 - abs(len(candidate_tokens) - len(col_tokens))
            scored.append((score, col))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            best_score = scored[0][0]
            best = [col for score, col in scored if score == best_score]
            if len(best) == 1:
                return best[0]

    # Generic semantic synonym fallback for common business-measure drift
    synonym_groups = [
        {"AMOUNT", "REVENUE", "SALES", "VALUE"},
        {"UNIT", "UNITS", "QUANTITY", "QTY", "COUNT", "VOLUME"},
    ]
    compact_candidate_tokens = {_stem(t) for t in candidate.split("_") if t}
    for group in synonym_groups:
        normalized_group = {_stem(t) for t in group}
        if not compact_candidate_tokens.intersection(normalized_group):
            continue
        candidates_in_group = []
        for col in known_columns:
            col_tokens = {_stem(t) for t in col.split("_") if t}
            if col_tokens.intersection(normalized_group):
                candidates_in_group.append(col)
        if len(candidates_in_group) == 1:
            return candidates_in_group[0]

    # common drift: SALES_DATE -> DATE
    if candidate.endswith("_DATE") and "DATE" in known_columns:
        return "DATE"

    return None


def resolve_metric_reference_name(
    metric_names: Optional[set[str]],
    candidate: str,
    *,
    allow_fuzzy: bool = True,
) -> Optional[str]:
    """Resolve a possibly drifted identifier to a known semantic metric name."""
    if not metric_names:
        return None

    if candidate in metric_names:
        return candidate

    compact_candidate = candidate.replace("_", "")
    compact_matches = [
        m for m in metric_names
        if m.replace("_", "") == compact_candidate
    ]
    if len(compact_matches) == 1:
        return compact_matches[0]

    if not allow_fuzzy:
        # For qualified TABLE.COLUMN references we should be strict
        return None

    suffix_matches = [
        m for m in metric_names
        if m.endswith(f"_{candidate}") or m.startswith(f"{candidate}_")
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    contains_matches = [
        m for m in metric_names
        if candidate in m
    ]
    if len(contains_matches) == 1:
        return contains_matches[0]

    return None


def migrate_numeric_leading_identifiers(emitter, sml) -> None:
    """Prefix metric/dimension identifiers that begin with numeric tokens.

    This mutates the in-memory model only for the current emission cycle.
    """
    def _starts_with_digit(name: str) -> bool:
        sanitized = emitter._id.sanitize_column(name)
        return bool(sanitized and sanitized[0].isdigit())

    metric_rename_map: dict[str, str] = {}
    attr_rename_map: dict[tuple[str, str], str] = {}
    metric_changes = 0
    attr_changes = 0

    # Metrics: 18_MONTH... -> L_18_MONTH...
    for metric in getattr(sml, "metrics", []):
        old_name = metric.unique_name
        if _starts_with_digit(old_name):
            new_name = old_name if old_name.startswith("L_") else f"L_{old_name}"
            if new_name != old_name:
                metric.unique_name = new_name
                metric_rename_map[old_name] = new_name
                metric_changes += 1

    # Dimensions/attributes/hierarchy levels: 18_MONTH... -> N_18_MONTH...
    for dim in getattr(sml, "dimensions", []):
        dim_name = getattr(dim, "unique_name", "")

        for attr in getattr(dim, "attributes", []):
            old_attr = attr.unique_name
            if _starts_with_digit(old_attr):
                new_attr = old_attr if old_attr.startswith("N_") else f"N_{old_attr}"
                if new_attr != old_attr:
                    attr.unique_name = new_attr
                    attr_rename_map[(dim_name, old_attr)] = new_attr
                    attr_changes += 1

        for lvl in getattr(dim, "hierarchies", []):
            if _starts_with_digit(lvl.unique_name):
                if not lvl.unique_name.startswith("N_"):
                    lvl.unique_name = f"N_{lvl.unique_name}"
            old_ref = lvl.attribute
            mapped_ref = attr_rename_map.get((dim_name, old_ref))
            if mapped_ref:
                lvl.attribute = mapped_ref

    # Update metric dependency references if names were rewritten
    if metric_rename_map:
        for metric in getattr(sml, "metrics", []):
            deps = list(getattr(metric, "depends_on_measures", []) or [])
            if deps:
                metric.depends_on_measures = [metric_rename_map.get(d, d) for d in deps]

    if metric_changes or attr_changes:
        logger.warning(
            "Applied identifier migration before semantic view emit: "
            "%s metric(s), %s attribute(s) renamed to avoid numeric-leading identifiers",
            metric_changes,
            attr_changes,
        )
