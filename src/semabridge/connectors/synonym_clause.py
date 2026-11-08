"""Snowflake semantic-view synonym / comment clause rendering.

Snowflake ``CREATE SEMANTIC VIEW`` accepts business metadata on every logical
table, fact, dimension and metric::

    <alias> AS <db>.<schema>.<table> [PRIMARY KEY (...)] [UNIQUE (...)]
        [WITH SYNONYMS [=] ('s1', ...)] [COMMENT = '...']
    [PRIVATE] <alias>.<name> [NON ADDITIVE BY (...)] AS <sql_expr>
        [WITH SYNONYMS [=] ('s1', ...)] [COMMENT = '...']

i.e. the optional ``WITH SYNONYMS`` clause follows the definition (the table
reference or the ``AS <sql_expr>`` expression) and precedes ``COMMENT``.
Every clause builder (tables / dimensions / metrics) appends the string
returned by :func:`synonyms_clause` directly after the definition, and the
downstream line parsers (metric inlining, ddl_validator, reconciliation,
null_sentinel) all expect that trailing position.

Emission can be switched off without a code change (deploy fallback):

* ``behavior.yaml``: ``snowflake.emit_ddl_synonyms: false`` /
  ``snowflake.emit_ddl_comments: false``
* environment: ``SEMABRIDGE_DDL_SYNONYMS=0`` / ``SEMABRIDGE_DDL_COMMENTS=0``
* programmatically: ``with ddl_annotations_disabled(): ...`` (thread/async
  local) — intended for a deploy retry when Snowflake rejects the view and
  the error is attributable to a synonym/comment clause.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
from typing import Any, Iterable, Iterator, Optional

# Snowflake caps COMMENT strings far higher, but semantic-view comments are
# shown to users and to Cortex Analyst: keep them readable and bounded.
MAX_COMMENT_LENGTH = 1000
MAX_SYNONYM_LENGTH = 255

_OVERRIDE: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "semabridge_ddl_annotation_override", default=None
)

_FALSEY = {"0", "false", "no", "off"}


def _env_flag(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() not in _FALSEY


def _behavior_flag(attr: str) -> Optional[bool]:
    try:
        from semabridge.core.settings import get_settings

        sf = getattr(get_settings().behavior, "snowflake", None)
        value = getattr(sf, attr, None)
        return None if value is None else bool(value)
    except Exception:
        return None


def ddl_synonyms_enabled() -> bool:
    """Whether WITH SYNONYMS clauses are emitted (default: True)."""
    override = _OVERRIDE.get()
    if override is not None and "synonyms" in override:
        return bool(override["synonyms"])
    env = _env_flag("SEMABRIDGE_DDL_SYNONYMS")
    if env is not None:
        return env
    flag = _behavior_flag("emit_ddl_synonyms")
    return True if flag is None else flag


def ddl_comments_enabled() -> bool:
    """Whether business-metadata COMMENT clauses are emitted (default: True)."""
    override = _OVERRIDE.get()
    if override is not None and "comments" in override:
        return bool(override["comments"])
    env = _env_flag("SEMABRIDGE_DDL_COMMENTS")
    if env is not None:
        return env
    flag = _behavior_flag("emit_ddl_comments")
    return True if flag is None else flag


@contextlib.contextmanager
def ddl_annotations_disabled(synonyms: bool = True, comments: bool = False) -> Iterator[None]:
    """Temporarily suppress synonym (and optionally comment) emission.

    ``synonyms=True`` disables WITH SYNONYMS; ``comments=True`` additionally
    disables ontology/BI description COMMENTs. Status comments the metric
    builder writes for placeholder metrics are not affected.
    """
    current = dict(_OVERRIDE.get() or {})
    if synonyms:
        current["synonyms"] = False
    if comments:
        current["comments"] = False
    token = _OVERRIDE.set(current)
    try:
        yield
    finally:
        _OVERRIDE.reset(token)


def escape_sql_string_literal(value: Any) -> str:
    """Escape text for a single-quoted Snowflake string literal.

    Backslash is an escape character in Snowflake string literals, so it is
    doubled first; single quotes are doubled; control characters / newlines
    collapse to a single space so the DDL stays one definition per line.
    """
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text.replace("\\", "\\\\").replace("'", "''")


def _quote_synonym(value: str) -> str:
    text = str(value or "").strip()[:MAX_SYNONYM_LENGTH]
    return "'" + escape_sql_string_literal(text) + "'"


def clean_synonyms(synonyms: Optional[Iterable[Any]]) -> list[str]:
    """Strip, drop empties and de-duplicate case-insensitively (order kept)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in synonyms or []:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def synonyms_clause(synonyms: Optional[Iterable[Any]], comment: Optional[str] = None) -> str:
    """Render ``" WITH SYNONYMS = ('a', 'b')"`` (+ optional ``" COMMENT = '...'"``).

    Appended directly after an element's definition (see module docstring).
    Returns "" when there is nothing to emit or emission is disabled.
    """
    parts = ""
    cleaned = clean_synonyms(synonyms)
    if cleaned and ddl_synonyms_enabled():
        parts += f" WITH SYNONYMS = ({', '.join(_quote_synonym(s) for s in cleaned)})"
    parts += comment_clause(comment)
    return parts


def comment_clause(comment: Optional[str]) -> str:
    """Render ``" COMMENT = '...'"`` for a business description ("" if empty/disabled)."""
    text = str(comment or "").strip()
    if not text or not ddl_comments_enabled():
        return ""
    if len(text) > MAX_COMMENT_LENGTH:
        text = text[: MAX_COMMENT_LENGTH - 3].rstrip() + "..."
    return f" COMMENT = '{escape_sql_string_literal(text)}'"


def element_description(*elements: Any) -> str:
    """First non-empty ``description`` among the given model elements."""
    for el in elements:
        text = str(getattr(el, "description", "") or "").strip() if el is not None else ""
        if text:
            return text
    return ""


def metric_comment_text(metric: Any) -> str:
    """Description plus translation advisories for a deployed metric's COMMENT.

    Advisories (e.g. CALCULATE filter-override semantics, time intelligence
    anchored to CURRENT_DATE) tell the consumer where the SQL can differ from
    the source BI tool, so they must travel with the deployed metric.
    """
    desc = element_description(metric)
    notes = [str(n).strip() for n in (getattr(metric, "advisory_notes", None) or []) if str(n or "").strip()]
    if not notes:
        return desc
    note_text = "Note: " + " ".join(notes)
    return f"{desc} {note_text}" if desc else note_text


def column_of(dataset: Any, name: Optional[str]) -> Any:
    """Column object of ``dataset`` named ``name`` (case-insensitive) or None."""
    if dataset is None or not name:
        return None
    for col in getattr(dataset, "columns", None) or []:
        if str(getattr(col, "unique_name", "")).casefold() == str(name).casefold():
            return col
    return None


def table_annotation_clause(dataset: Any) -> str:
    """WITH SYNONYMS + COMMENT for a TABLES entry (after PRIMARY KEY / UNIQUE)."""
    if dataset is None:
        return ""
    return synonyms_clause(getattr(dataset, "synonyms", None), element_description(dataset))


_SYNONYMS_ANYWHERE_RE = re.compile(
    r"\s+WITH\s+SYNONYMS\s*=?\s*\((?:[^()']|'(?:''|[^'])*')*\)", re.IGNORECASE
)


def strip_synonyms_clauses(ddl: str) -> str:
    """Remove every WITH SYNONYMS clause from DDL text (deploy fallback helper)."""
    return _SYNONYMS_ANYWHERE_RE.sub("", ddl or "")
