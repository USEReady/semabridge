"""SQL Execution Safety Guard for SemaBridge.

Enforces a strict default-deny (allowlist) policy:
SemaBridge NEVER creates, drops, alters, or modifies physical data tables.
Only Semantic Views (and SemaBridge-owned helper views) and read-only inspection queries are allowed.

Strict Allowlist:
- SELECT (including CTEs with WITH ... SELECT)
- SHOW
- DESCRIBE / DESC
- USE (e.g. USE ROLE, USE WAREHOUSE, USE DATABASE, USE SCHEMA)
- ALTER SESSION
- CREATE OR REPLACE SEMANTIC VIEW
- ALTER SEMANTIC VIEW ... (SET|UNSET) COMMENT
- CREATE OR REPLACE VIEW / DROP VIEW / DROP SEMANTIC VIEW (under SemaBridge ownership rules)
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import os
import re
from typing import Any, Callable, FrozenSet, List, Optional, Set, Tuple
from semabridge.core.exceptions import ConnectorError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

SEMABRIDGE_SIGNATURE_COMMENT = "Created by SemaBridge"


class SQLSecurityGuardError(ConnectorError):
    """Raised when an execution attempts a disallowed or dangerous SQL operation."""
    pass


class SQLOwnershipSecurityError(SQLSecurityGuardError):
    """Raised when an execution targets a view not owned by SemaBridge."""
    pass


# Object names SemaBridge may write for the CURRENT deploy. Each run executes
# in its own worker thread (and asyncio tasks copy their context), so a
# ContextVar scopes the allowlist to one run: concurrent runs can neither see
# nor widen each other's allowlist, and one run clearing its list never
# clears another's. Call reset_guard_scope() when a deploy starts, because a
# pooled thread keeps its context between tasks.
_ALLOWED_MODEL_OBJECTS: ContextVar[FrozenSet[str]] = ContextVar("sb_allowed_model_objects", default=frozenset())
_ALLOWED_ABSENT_TABLES: ContextVar[FrozenSet[str]] = ContextVar("sb_allowed_absent_tables", default=frozenset())


def _normalized(names: Set[str]) -> FrozenSet[str]:
    return frozenset(n.strip('"').upper() for n in names if n)


def set_allowed_model_objects(names: Set[str]) -> None:
    """Add object names (uppercase) SemaBridge produces for the current run."""
    _ALLOWED_MODEL_OBJECTS.set(_ALLOWED_MODEL_OBJECTS.get() | _normalized(names))


def get_allowed_model_objects() -> Set[str]:
    """Object names allowed for the current run (a copy)."""
    return set(_ALLOWED_MODEL_OBJECTS.get())


def clear_allowed_model_objects() -> None:
    """Clear the current run's allowed model objects."""
    _ALLOWED_MODEL_OBJECTS.set(frozenset())


def set_allowed_absent_tables(names: Set[str]) -> None:
    """Add physical table names confirmed absent that this run may create empty."""
    _ALLOWED_ABSENT_TABLES.set(_ALLOWED_ABSENT_TABLES.get() | _normalized(names))


def get_allowed_absent_tables() -> Set[str]:
    """Absent table names allowed for the current run (a copy)."""
    return set(_ALLOWED_ABSENT_TABLES.get())


def clear_allowed_absent_tables() -> None:
    """Clear the current run's allowed absent table names."""
    _ALLOWED_ABSENT_TABLES.set(frozenset())


def reset_guard_scope() -> None:
    """Start a fresh allowlist scope for a new deploy in this execution context."""
    clear_allowed_model_objects()
    clear_allowed_absent_tables()


# Strip SQL comments: /* ... */ and -- ...
_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\r\n]*", re.DOTALL)

# Explicitly forbidden patterns (high-priority error messages)
_EXPLICIT_FORBIDDEN_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.IGNORECASE), "EXECUTE IMMEDIATE"),
    (re.compile(r"^\s*CALL\s+(?!SYSTEM\$CREATE_SEMANTIC_VIEW_FROM_YAML\b)", re.IGNORECASE), "CALL"),
    (re.compile(r"^\s*CALL\s+SYSTEM\$CREATE_SEMANTIC_VIEW_FROM_YAML\b(?!.*?(?:,\s*TRUE\s*|\bTRUE\b\s*)\)\s*$)", re.IGNORECASE | re.DOTALL), "CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML (non-verify mode)"),
    (re.compile(r"\bCLONE\b", re.IGNORECASE), "CLONE"),
    (re.compile(r"^\s*CREATE\s+OR\s+REPLACE\s+(?:TEMPORARY\s+|TRANSIENT\s+)?TABLE\b", re.IGNORECASE), "CREATE OR REPLACE TABLE"),
    (re.compile(r"^\s*CREATE\s+(?:TEMPORARY\s+|TRANSIENT\s+)TABLE\b", re.IGNORECASE), "CREATE TEMPORARY/TRANSIENT TABLE"),
    (re.compile(r"^\s*CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE), "CREATE TABLE (without IF NOT EXISTS)"),
    (re.compile(r"\bAS\s+SELECT\b.*\bTABLE\b", re.IGNORECASE), "CTAS"),
    (re.compile(r"^\s*CREATE\s+TABLE\b.*?\bAS\s+SELECT\b", re.IGNORECASE | re.DOTALL), "CTAS"),
    (re.compile(r"^\s*ALTER\s+TABLE\b", re.IGNORECASE), "ALTER TABLE"),
    (re.compile(r"^\s*DROP\s+TABLE\b", re.IGNORECASE), "DROP TABLE"),
    (re.compile(r"^\s*DROP\s+(?:DATABASE|SCHEMA)\b", re.IGNORECASE), "DROP DATABASE/SCHEMA"),
    (re.compile(r"^\s*(?:GRANT|REVOKE)\b", re.IGNORECASE), "GRANT/REVOKE"),
    (re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?STAGE\b", re.IGNORECASE), "CREATE STAGE"),
    (re.compile(r"^\s*TRUNCATE\s+(?:TABLE\s+)?", re.IGNORECASE), "TRUNCATE TABLE"),
    (re.compile(r"^\s*INSERT\s+(?:INTO\s+)?", re.IGNORECASE), "INSERT"),
    (re.compile(r"^\s*UPDATE\b", re.IGNORECASE), "UPDATE"),
    (re.compile(r"^\s*DELETE\s+FROM\b|^\s*DELETE\b", re.IGNORECASE), "DELETE"),
    (re.compile(r"^\s*COPY\s+INTO\b", re.IGNORECASE), "COPY INTO"),
    (re.compile(r"^\s*PUT\b", re.IGNORECASE), "PUT"),
    (re.compile(r"^\s*GET\b", re.IGNORECASE), "GET"),
    (re.compile(r"\bSWAP\s+WITH\b", re.IGNORECASE), "SWAP WITH"),
    (re.compile(r"^\s*MERGE\s+INTO\b", re.IGNORECASE), "MERGE INTO"),
]

# Specifically allowed verify-only CALL pattern
_VERIFY_YAML_PATTERN = re.compile(
    r"^\s*CALL\s+SYSTEM\$CREATE_SEMANTIC_VIEW_FROM_YAML\s*\(.*?(?:,\s*TRUE\s*|\bTRUE\b\s*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Strict Allowlist Patterns: ONLY these statement types can ever pass
_ALLOWLIST_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*SELECT\b", re.IGNORECASE), "SELECT"),
    (re.compile(r"^\s*WITH\s+[a-zA-Z0-9_\"`]+\s+AS\s*\(.*?\)\s*SELECT\b", re.IGNORECASE | re.DOTALL), "WITH_SELECT"),
    (re.compile(r"^\s*SHOW\b", re.IGNORECASE), "SHOW"),
    (re.compile(r"^\s*DESC(?:RIBE)?\b", re.IGNORECASE), "DESCRIBE"),
    (re.compile(r"^\s*USE\s+(?:ROLE|WAREHOUSE|DATABASE|SCHEMA)\b", re.IGNORECASE), "USE"),
    (_VERIFY_YAML_PATTERN, "VERIFY_SEMANTIC_VIEW_YAML"),
    (re.compile(r"^\s*ALTER\s+SESSION\b", re.IGNORECASE), "ALTER_SESSION"),
    (re.compile(r"^\s*CREATE\s+OR\s+REPLACE\s+SEMANTIC\s+VIEW\s+([a-zA-Z0-9_\"`.]+)", re.IGNORECASE), "CREATE_SEMANTIC_VIEW"),
    (re.compile(r"^\s*ALTER\s+SEMANTIC\s+VIEW\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"`.]+)\s+(?:SET|UNSET)\s+COMMENT\b", re.IGNORECASE), "ALTER_SEMANTIC_VIEW_COMMENT"),
    (re.compile(r"^\s*CREATE\s+OR\s+REPLACE\s+(?:TEMPORARY\s+)?VIEW\s+([a-zA-Z0-9_\"`.]+)", re.IGNORECASE), "CREATE_VIEW"),
    (re.compile(r"^\s*DROP\s+VIEW\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"`.]+)", re.IGNORECASE), "DROP_VIEW"),
    (re.compile(r"^\s*DROP\s+SEMANTIC\s+VIEW\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"`.]+)", re.IGNORECASE), "DROP_SEMANTIC_VIEW"),
    (re.compile(r"^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z0-9_\"`.]+)", re.IGNORECASE), "CREATE_TABLE_IF_NOT_EXISTS"),
]


def strip_sql_comments(sql: str) -> str:
    """Remove single-line (-- ...) and multi-line (/* ... */) comments from SQL."""
    return _COMMENT_RE.sub(" ", sql).strip()


def split_statements(sql: str) -> List[str]:
    """Split SQL string into individual statements by semicolon, ignoring quotes."""
    statements: List[str] = []
    current: List[str] = []
    in_single_quote = False
    in_double_quote = False
    in_dollar_quote = False
    i = 0
    length = len(sql)

    while i < length:
        ch = sql[i]

        if ch == "$" and i + 1 < length and sql[i + 1] == "$":
            in_dollar_quote = not in_dollar_quote
            current.append("$$")
            i += 2
            continue

        if not in_dollar_quote:
            if ch == "'" and not in_double_quote:
                if in_single_quote and i + 1 < length and sql[i + 1] == "'":
                    current.append("''")
                    i += 2
                    continue
                in_single_quote = not in_single_quote
            elif ch == '"' and not in_single_quote:
                if in_double_quote and i + 1 < length and sql[i + 1] == '"':
                    current.append('""')
                    i += 2
                    continue
                in_double_quote = not in_double_quote

        if ch == ";" and not in_single_quote and not in_double_quote and not in_dollar_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)

        i += 1

    remaining = "".join(current).strip()
    if remaining:
        statements.append(remaining)

    return statements


def extract_base_object_name(raw_name: str) -> str:
    """Extract unqualified, uppercase base object name from identifier (e.g. DB.SCHEMA."V" -> V)."""
    clean = raw_name.strip()
    # Split by dot (ignoring dots inside quotes)
    parts = re.findall(r'"[^"]+"|`[^`]+`|[a-zA-Z0-9_]+', clean)
    if parts:
        return parts[-1].strip('"').strip('`').upper()
    return clean.strip('"').upper()


def check_view_ownership(
    object_name: str,
    cursor: Optional[Any] = None,
    database: Optional[str] = None,
    schema: Optional[str] = None,
    is_semantic_view: bool = False,
) -> None:
    """Verify ownership of a view before replacing or dropping it.

    CREATE OR REPLACE / DROP of a VIEW or SEMANTIC VIEW is allowed if the object name
    is one that SemaBridge's naming function generates for the project/model being synced
    (its semantic view and its enriched views). No comment check is performed.
    Any other name is refused under ownership rules (fail closed).
    """
    base_name = extract_base_object_name(object_name)

    # Allowed object names check (FAIL CLOSED: empty set denies everything)
    allowed = get_allowed_model_objects()
    if not allowed:
        err = (
            f"Ownership rule violation: No allowed model objects are registered "
            f"(empty allowed set). Refusing view operation for '{base_name}' (fail closed)."
        )
        logger.error("🛑 OWNERSHIP GUARD: %s", err)
        raise SQLOwnershipSecurityError(err)

    if base_name not in allowed:
        err = (
            f"Ownership rule violation: Object '{base_name}' is not in the list of "
            f"allowed objects for the current project/model ({sorted(allowed)})."
        )
        logger.error("🛑 OWNERSHIP GUARD: %s", err)
        raise SQLOwnershipSecurityError(err)


def validate_safe_sql(
    sql: str,
    cursor: Optional[Any] = None,
    check_ownership: bool = True,
) -> None:
    """Validate that a SQL statement conforms to the strict allowlist and ownership rules.

    Default-Deny Policy:
    Only explicitly allowed statements pass. Everything else raises SQLSecurityGuardError.

    Args:
        sql: SQL query string (may contain multiple statements).
        cursor: Optional Snowflake cursor for live ownership checks.
        check_ownership: Kept for backward compatibility; ownership check is always active
            for CREATE OR REPLACE VIEW / DROP VIEW / DROP SEMANTIC VIEW / CREATE OR REPLACE SEMANTIC VIEW.

    Raises:
        SQLSecurityGuardError: If any statement is disallowed.
        SQLOwnershipSecurityError: If statement violates object ownership rules.
    """
    if not sql:
        return

    statements = split_statements(sql)
    for raw_stmt in statements:
        clean = strip_sql_comments(raw_stmt).strip()
        if not clean:
            continue

        # 1. Check explicitly forbidden patterns
        for pattern, op_name in _EXPLICIT_FORBIDDEN_PATTERNS:
            if pattern.search(clean):
                preview = clean[:120].replace("\n", " ")
                err_msg = (
                    f"SQL execution guard blocked forbidden operation '{op_name}': {preview!r}. "
                    "SemaBridge is strictly prohibited from executing this statement."
                )
                logger.error("🛑 SECURITY GUARD: %s", err_msg)
                raise SQLSecurityGuardError(err_msg)

        # 2. Check strict allowlist
        matched_allowlist = False
        matched_action = ""
        matched_obj = ""

        for pattern, action_name in _ALLOWLIST_PATTERNS:
            match = pattern.search(clean)
            if match:
                matched_allowlist = True
                matched_action = action_name
                if match.groups():
                    matched_obj = match.group(1)
                break

        if not matched_allowlist:
            preview = clean[:120].replace("\n", " ")
            err_msg = (
                f"SQL execution guard blocked statement (default-deny allowlist): {preview!r}. "
                "Only SELECT, SHOW, DESCRIBE, USE, ALTER SESSION, CREATE OR REPLACE SEMANTIC VIEW, "
                "ALTER SEMANTIC VIEW ... (SET|UNSET) COMMENT, and SemaBridge-owned VIEW statements are permitted."
            )
            logger.error("🛑 SECURITY GUARD: %s", err_msg)
            raise SQLSecurityGuardError(err_msg)

        # 3. Absent Table enforcement for CREATE TABLE IF NOT EXISTS
        if matched_action == "CREATE_TABLE_IF_NOT_EXISTS":
            base_tbl = extract_base_object_name(matched_obj)
            allowed_absent = get_allowed_absent_tables()
            if not allowed_absent:
                err = (
                    f"SQL execution guard blocked CREATE TABLE IF NOT EXISTS for '{base_tbl}': "
                    "No allowed absent tables are registered for the current model (fail closed)."
                )
                logger.error("🛑 SECURITY GUARD: %s", err)
                raise SQLSecurityGuardError(err)
            if base_tbl not in allowed_absent:
                err = (
                    f"SQL execution guard blocked CREATE TABLE IF NOT EXISTS for '{base_tbl}': "
                    f"Table is not in the confirmed absent tables for the current model ({sorted(allowed_absent)})."
                )
                logger.error("🛑 SECURITY GUARD: %s", err)
                raise SQLSecurityGuardError(err)

        # 4. Ownership Rule enforcement for VIEW / SEMANTIC VIEW drops and creates
        # Always on for CREATE OR REPLACE VIEW / DROP VIEW / DROP SEMANTIC VIEW / CREATE OR REPLACE SEMANTIC VIEW
        if matched_action in ("CREATE_VIEW", "DROP_VIEW", "DROP_SEMANTIC_VIEW", "CREATE_SEMANTIC_VIEW"):
            is_semantic = (matched_action in ("DROP_SEMANTIC_VIEW", "CREATE_SEMANTIC_VIEW"))
            check_view_ownership(
                object_name=matched_obj,
                cursor=cursor,
                is_semantic_view=is_semantic,
            )


_READ_ONLY_ALLOWED_ACTIONS = frozenset({"SELECT", "WITH_SELECT", "SHOW", "DESCRIBE"})


def validate_read_only_sql(sql: str, *, warn_only: Optional[bool] = None) -> bool:
    """Validate a single read-only SQL query (SELECT / WITH ... SELECT / SHOW / DESCRIBE).

    Args:
        sql: SQL string to validate.
        warn_only: When True, log a warning and return False instead of raising
            SQLSecurityGuardError. If None, checks SEMABRIDGE_SQL_GUARD_READONLY_MODE
            ('warn' vs default 'enforce').
    """
    if warn_only is None:
        warn_only = os.getenv("SEMABRIDGE_SQL_GUARD_READONLY_MODE", "enforce").strip().lower() == "warn"

    def _reject(msg: str) -> bool:
        if warn_only:
            logger.warning("⚠️ READ-ONLY SQL GUARD (WARN): %s", msg)
            return False
        logger.error("🛑 READ-ONLY SQL GUARD: %s", msg)
        raise SQLSecurityGuardError(msg)

    if not sql or not sql.strip():
        return _reject("Read-only SQL guard blocked empty SQL statement.")

    non_empty = [strip_sql_comments(s).strip() for s in split_statements(sql)]
    non_empty = [s for s in non_empty if s]
    if len(non_empty) != 1:
        return _reject(
            f"Read-only SQL guard requires exactly one statement (got {len(non_empty)})."
        )

    clean = non_empty[0]
    for pattern, op_name in _EXPLICIT_FORBIDDEN_PATTERNS:
        if pattern.search(clean):
            preview = clean[:120].replace("\n", " ")
            return _reject(
                f"Read-only SQL guard blocked forbidden operation '{op_name}': {preview!r}."
            )

    # Block side-effecting system functions (e.g. SELECT SYSTEM$CANCEL_QUERY(...))
    for m in re.finditer(r"\b(SYSTEM\$[A-Za-z0-9_]+)\b", clean, re.IGNORECASE):
        sys_func = m.group(1).upper()
        if sys_func not in {
            "SYSTEM$TYPEOF",
            "SYSTEM$EXPLAIN_PLAN_JSON",
            "SYSTEM$ESTIMATE_SEARCH_OPTIMIZATION_COSTS",
        }:
            preview = clean[:120].replace("\n", " ")
            return _reject(
                f"Read-only SQL guard blocked potentially side-effecting system function '{sys_func}': {preview!r}."
            )

    for pattern, action_name in _ALLOWLIST_PATTERNS:
        if action_name in _READ_ONLY_ALLOWED_ACTIONS and pattern.search(clean):
            return True

    preview = clean[:120].replace("\n", " ")
    return _reject(
        f"Read-only SQL guard blocked statement: {preview!r}. "
        "Only a single SELECT, WITH ... SELECT, SHOW, or DESCRIBE statement is permitted."
    )


class GuardedCursor:
    """Cursor wrapper that enforces sql_guard before every execute()."""

    def __init__(self, cursor: Any, *, read_only: bool = True, check_ownership: bool = False) -> None:
        self._cursor = cursor
        self._read_only = read_only
        self._check_ownership = check_ownership

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if self._read_only:
            validate_read_only_sql(sql)
        else:
            validate_safe_sql(sql, cursor=self._cursor, check_ownership=self._check_ownership)
        return self._cursor.execute(sql, *args, **kwargs)

    def executemany(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if self._read_only:
            validate_read_only_sql(sql)
        else:
            validate_safe_sql(sql, cursor=self._cursor, check_ownership=self._check_ownership)
        return self._cursor.executemany(sql, *args, **kwargs)

    def __enter__(self) -> "GuardedCursor":
        if hasattr(self._cursor, "__enter__"):
            self._cursor.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        if hasattr(self._cursor, "__exit__"):
            return self._cursor.__exit__(*args)
        return None

    def __iter__(self) -> Any:
        return iter(self._cursor)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class GuardedConnection:
    """Connection wrapper whose cursor() returns a GuardedCursor."""

    def __init__(self, conn: Any, *, read_only: bool = True, check_ownership: bool = False) -> None:
        self._conn = conn
        self._read_only = read_only
        self._check_ownership = check_ownership

    def cursor(self, *args: Any, **kwargs: Any) -> GuardedCursor:
        return GuardedCursor(
            self._conn.cursor(*args, **kwargs),
            read_only=self._read_only,
            check_ownership=self._check_ownership,
        )

    def __enter__(self) -> "GuardedConnection":
        if hasattr(self._conn, "__enter__"):
            self._conn.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        if hasattr(self._conn, "__exit__"):
            return self._conn.__exit__(*args)
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def guard_connection(conn: Any, *, read_only: bool = True, check_ownership: bool = False) -> GuardedConnection:
    """Wrap a DB-API connection so all cursor executions pass sql_guard."""
    return GuardedConnection(conn, read_only=read_only, check_ownership=check_ownership)


# ─────────────────────────────────────────────────────────────────────────────
# Databricks Unity Catalog SQL Guard
# ─────────────────────────────────────────────────────────────────────────────

_DBX_IDENT_PART = r"(?:`[^`]+`|\"[^\"]+\"|[A-Za-z0-9_$]+)"
_DBX_FQN_RE = re.compile(rf"^\s*({_DBX_IDENT_PART})\s*\.\s*({_DBX_IDENT_PART})\s*\.\s*({_DBX_IDENT_PART})\s*$")
_DBX_SCHEMA_RE = re.compile(rf"^\s*({_DBX_IDENT_PART})\s*\.\s*({_DBX_IDENT_PART})\s*$")

_DBX_CREATE_VIEW_RE = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?({_DBX_IDENT_PART}(?:\s*\.\s*{_DBX_IDENT_PART}){{0,2}})(?=\s|\(|$)",
    re.IGNORECASE,
)
_DBX_DROP_VIEW_RE = re.compile(
    rf"^\s*DROP\s+VIEW\s+(?:IF\s+EXISTS\s+)?({_DBX_IDENT_PART}(?:\s*\.\s*{_DBX_IDENT_PART}){{0,2}})\s*$",
    re.IGNORECASE,
)
_DBX_CREATE_SCHEMA_RE = re.compile(
    rf"^\s*CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+({_DBX_IDENT_PART}\s*\.\s*{_DBX_IDENT_PART})\s*$",
    re.IGNORECASE,
)
_DBX_GRANT_RE = re.compile(
    rf"^\s*GRANT\s+([A-Za-z0-9_\s]+?)\s+ON\s+(?:VIEW\s+)?({_DBX_IDENT_PART}(?:\s*\.\s*{_DBX_IDENT_PART}){{0,2}})\s+TO\s+(.+?)\s*$",
    re.IGNORECASE,
)
_DBX_META_CREATE_TABLE_RE = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_DBX_IDENT_PART}(?:\s*\.\s*{_DBX_IDENT_PART}){{0,2}})(?=\s|\(|$)",
    re.IGNORECASE,
)
_DBX_META_DROP_TABLE_RE = re.compile(
    rf"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?({_DBX_IDENT_PART}(?:\s*\.\s*{_DBX_IDENT_PART}){{0,2}})\s*$",
    re.IGNORECASE,
)
_DBX_META_INSERT_RE = re.compile(
    rf"^\s*INSERT\s+INTO\s+({_DBX_IDENT_PART}(?:\s*\.\s*{_DBX_IDENT_PART}){{0,2}})(?=\s|\(|$)",
    re.IGNORECASE,
)
_DBX_READ_ONLY_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*SELECT\b", re.IGNORECASE), "SELECT"),
    (re.compile(r"^\s*WITH\s+[A-Za-z0-9_\"`]+\s+AS\s*\(.*?\)\s*SELECT\b", re.IGNORECASE | re.DOTALL), "WITH_SELECT"),
    (re.compile(r"^\s*SHOW\b", re.IGNORECASE), "SHOW"),
    (re.compile(r"^\s*DESC(?:RIBE)?\b", re.IGNORECASE), "DESCRIBE"),
]
_DBX_FORBIDDEN_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*DROP\s+TABLE\b", re.IGNORECASE), "DROP TABLE"),
    (re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+|EXTERNAL\s+|STREAMING\s+)?TABLE\b", re.IGNORECASE), "CREATE TABLE"),
    (re.compile(r"^\s*INSERT\b", re.IGNORECASE), "INSERT"),
    (re.compile(r"^\s*UPDATE\b", re.IGNORECASE), "UPDATE"),
    (re.compile(r"^\s*DELETE\b", re.IGNORECASE), "DELETE"),
    (re.compile(r"^\s*MERGE\b", re.IGNORECASE), "MERGE"),
    (re.compile(r"^\s*ALTER\s+TABLE\b", re.IGNORECASE), "ALTER TABLE"),
    (re.compile(r"^\s*TRUNCATE\b", re.IGNORECASE), "TRUNCATE"),
    (re.compile(r"^\s*COPY\s+INTO\b", re.IGNORECASE), "COPY INTO"),
    (re.compile(r"^\s*CALL\b", re.IGNORECASE), "CALL"),
    (re.compile(r"^\s*DROP\s+(?:CATALOG|DATABASE|SCHEMA)\b", re.IGNORECASE), "DROP CATALOG/DATABASE/SCHEMA"),
    (re.compile(r"^\s*CREATE\s+CATALOG\b", re.IGNORECASE), "CREATE CATALOG"),
    (re.compile(r"^\s*REVOKE\b", re.IGNORECASE), "REVOKE"),
    (re.compile(r"^\s*OPTIMIZE\b", re.IGNORECASE), "OPTIMIZE"),
    (re.compile(r"^\s*VACUUM\b", re.IGNORECASE), "VACUUM"),
    (re.compile(r"^\s*RESTORE\s+TABLE\b", re.IGNORECASE), "RESTORE TABLE"),
    (re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.IGNORECASE), "EXECUTE IMMEDIATE"),
]


def _unquote_dbx_part(part: str) -> str:
    s = str(part or "").strip()
    if len(s) >= 2 and ((s[0] == "`" and s[-1] == "`") or (s[0] == '"' and s[-1] == '"')):
        s = s[1:-1]
    return s.strip().lower()


def normalize_databricks_fqn(raw: str) -> Optional[Tuple[str, str, str]]:
    """Parse a 3-part Databricks identifier into lowercase ``(catalog, schema, name)``."""
    match = _DBX_FQN_RE.match(str(raw or ""))
    if not match:
        return None
    return (
        _unquote_dbx_part(match.group(1)),
        _unquote_dbx_part(match.group(2)),
        _unquote_dbx_part(match.group(3)),
    )


def normalize_databricks_fqn_str(raw: str) -> Optional[str]:
    """Return lowercase ``catalog.schema.name`` for a 3-part Databricks identifier."""
    parsed = normalize_databricks_fqn(raw)
    if not parsed:
        return None
    return f"{parsed[0]}.{parsed[1]}.{parsed[2]}"


def normalize_databricks_principal(raw: str) -> str:
    """Normalize a Databricks principal (user/group/service principal) for grant comparison."""
    s = str(raw or "").strip()
    if len(s) >= 2 and ((s[0] == "`" and s[-1] == "`") or (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1]
    return s.strip().lower()


@dataclass
class DatabricksSQLGuardPolicy:
    """Per-deploy allowlist policy for Databricks SQL statement execution."""

    target_catalog: str
    target_schema: str
    allowed_views: Set[str] = field(default_factory=set)
    allowed_drop_views: Set[str] = field(default_factory=set)
    allowed_grants: Set[Tuple[str, str, str]] = field(default_factory=set)
    allow_create_schema: bool = False
    allow_metadata_table: bool = False
    metadata_table_fqn: Optional[str] = None
    read_only: bool = False

    def _target_prefix(self) -> Tuple[str, str]:
        return (
            _unquote_dbx_part(self.target_catalog),
            _unquote_dbx_part(self.target_schema),
        )

    def register_view(self, fqn: str) -> Optional[str]:
        norm = normalize_databricks_fqn_str(fqn)
        if norm:
            self.allowed_views.add(norm)
        return norm

    def register_drop_view(self, fqn: str) -> Optional[str]:
        norm = normalize_databricks_fqn_str(fqn)
        if norm:
            self.allowed_drop_views.add(norm)
        return norm

    def register_grant(self, privilege: str, fqn: str, principal: str) -> None:
        norm = normalize_databricks_fqn_str(fqn)
        priv = " ".join(str(privilege or "").strip().upper().split())
        princ = normalize_databricks_principal(principal)
        if norm and priv and princ:
            self.allowed_grants.add((priv, norm, princ))


def _is_allowed_metadata_table_target(
    raw_target: str,
    policy: DatabricksSQLGuardPolicy,
) -> bool:
    if not policy.allow_metadata_table or not policy.metadata_table_fqn:
        return False
    parsed = normalize_databricks_fqn(raw_target)
    if not parsed:
        return False
    expected = normalize_databricks_fqn(policy.metadata_table_fqn)
    if not expected:
        return False
    target_cat, target_sch = policy._target_prefix()
    if parsed[0] != target_cat or parsed[1] != target_sch:
        return False
    if "_semabridge_metadata" not in parsed[2]:
        return False
    return parsed == expected


def validate_databricks_sql(sql: str, policy: DatabricksSQLGuardPolicy) -> None:
    """Validate SQL statements for Databricks Unity Catalog execution.

    Enforces a strict default-deny allowlist:
    - Read-only: ``SELECT`` / ``WITH ... SELECT`` / ``SHOW`` / ``DESCRIBE``
    - ``CREATE [OR REPLACE] VIEW`` (incl. ``WITH METRICS``) only for objects in
      ``policy.target_catalog.target_schema`` registered in ``policy.allowed_views``
    - ``DROP VIEW [IF EXISTS]`` only for objects in ``policy.allowed_drop_views``
      (staging views, probe view, or verified SemaBridge-owned views)
    - ``GRANT`` only for exact ``(privilege, view_fqn, principal)`` tuples captured
      from the same live object
    - ``CREATE SCHEMA IF NOT EXISTS <target_catalog>.<target_schema>`` only when
      ``policy.allow_create_schema`` is enabled
    - Metadata table statements (``CREATE [OR REPLACE] TABLE``, ``DROP TABLE IF EXISTS``,
      ``INSERT INTO``) only when ``policy.allow_metadata_table`` is enabled and targeting
      exclusively ``policy.metadata_table_fqn`` (which must include ``_semabridge_metadata``).
    - Everything else raises ``SQLSecurityGuardError`` / ``SQLOwnershipSecurityError``.
    """
    if not sql or not sql.strip():
        return

    target_cat, target_sch = policy._target_prefix()
    statements = split_statements(sql)
    for raw_stmt in statements:
        clean = strip_sql_comments(raw_stmt).strip()
        if not clean:
            continue

        # 1. Read-only statements (always allowed)
        if any(pat.search(clean) for pat, _ in _DBX_READ_ONLY_PATTERNS):
            continue

        if policy.read_only:
            preview = clean[:120].replace("\n", " ")
            err = (
                f"Databricks SQL guard (read-only mode) blocked statement: {preview!r}. "
                "Only SELECT, WITH ... SELECT, SHOW, and DESCRIBE are permitted."
            )
            logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
            raise SQLSecurityGuardError(err)

        # 2. CREATE [OR REPLACE] VIEW (including WITH METRICS)
        m_create_view = _DBX_CREATE_VIEW_RE.match(clean)
        if m_create_view:
            raw_obj = m_create_view.group(1)
            parsed = normalize_databricks_fqn(raw_obj)
            if not parsed:
                err = (
                    f"Databricks SQL guard blocked CREATE VIEW with unqualified target '{raw_obj}'. "
                    "A 3-part catalog.schema.view name is required."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLOwnershipSecurityError(err)
            if parsed[0] != target_cat or parsed[1] != target_sch:
                err = (
                    f"Databricks SQL guard blocked CREATE VIEW '{raw_obj}': "
                    f"outside target schema '{target_cat}.{target_sch}'."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLOwnershipSecurityError(err)
            fq_str = f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
            if fq_str not in policy.allowed_views:
                err = (
                    f"Databricks SQL guard blocked CREATE VIEW '{fq_str}': "
                    "view is not registered in this deploy's allowed view set."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLOwnershipSecurityError(err)
            continue

        # 3. DROP VIEW [IF EXISTS]
        m_drop_view = _DBX_DROP_VIEW_RE.match(clean)
        if m_drop_view:
            raw_obj = m_drop_view.group(1)
            parsed = normalize_databricks_fqn(raw_obj)
            if not parsed or parsed[0] != target_cat or parsed[1] != target_sch:
                err = (
                    f"Databricks SQL guard blocked DROP VIEW '{raw_obj}': "
                    f"outside target schema '{target_cat}.{target_sch}'."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLOwnershipSecurityError(err)
            fq_str = f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
            if fq_str not in policy.allowed_drop_views:
                err = (
                    f"Databricks SQL guard blocked DROP VIEW '{fq_str}': "
                    "only staging views or verified SemaBridge-owned views may be dropped."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLOwnershipSecurityError(err)
            continue

        # 4. GRANT re-apply (restricted to captured grants on the same object)
        m_grant = _DBX_GRANT_RE.match(clean)
        if m_grant:
            priv = " ".join(m_grant.group(1).strip().upper().split())
            raw_obj = m_grant.group(2)
            raw_principal = m_grant.group(3)
            parsed = normalize_databricks_fqn(raw_obj)
            princ = normalize_databricks_principal(raw_principal)
            if not parsed or parsed[0] != target_cat or parsed[1] != target_sch:
                err = (
                    f"Databricks SQL guard blocked GRANT on '{raw_obj}': "
                    f"outside target schema '{target_cat}.{target_sch}'."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLSecurityGuardError(err)
            fq_str = f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
            if (priv, fq_str, princ) not in policy.allowed_grants:
                err = (
                    f"Databricks SQL guard blocked GRANT {priv} ON {fq_str} TO {raw_principal}: "
                    "only grants captured from the same live view before promotion may be re-applied."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLSecurityGuardError(err)
            continue

        # 5. CREATE SCHEMA IF NOT EXISTS (target schema only, when explicitly enabled)
        m_schema = _DBX_CREATE_SCHEMA_RE.match(clean)
        if m_schema:
            raw_schema = m_schema.group(1)
            sm = _DBX_SCHEMA_RE.match(raw_schema)
            if not sm:
                err = f"Databricks SQL guard blocked CREATE SCHEMA '{raw_schema}': invalid schema identifier."
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLSecurityGuardError(err)
            cat_part = _unquote_dbx_part(sm.group(1))
            sch_part = _unquote_dbx_part(sm.group(2))
            if not policy.allow_create_schema:
                err = (
                    f"Databricks SQL guard blocked CREATE SCHEMA '{cat_part}.{sch_part}': "
                    "create_schema_if_not_exists is disabled."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLSecurityGuardError(err)
            if cat_part != target_cat or sch_part != target_sch:
                err = (
                    f"Databricks SQL guard blocked CREATE SCHEMA '{cat_part}.{sch_part}': "
                    f"only target schema '{target_cat}.{target_sch}' is permitted."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLSecurityGuardError(err)
            continue

        # 6. Metadata table exception (ONLY when create_metadata_table is enabled AND targeting _semabridge_metadata)
        m_meta_create = _DBX_META_CREATE_TABLE_RE.match(clean)
        if m_meta_create and _is_allowed_metadata_table_target(m_meta_create.group(1), policy):
            continue
        m_meta_drop = _DBX_META_DROP_TABLE_RE.match(clean)
        if m_meta_drop and _is_allowed_metadata_table_target(m_meta_drop.group(1), policy):
            continue
        m_meta_insert = _DBX_META_INSERT_RE.match(clean)
        if m_meta_insert and _is_allowed_metadata_table_target(m_meta_insert.group(1), policy):
            continue

        # 7. Explicit forbidden error messages
        for pattern, op_name in _DBX_FORBIDDEN_PATTERNS:
            if pattern.search(clean):
                preview = clean[:120].replace("\n", " ")
                err = (
                    f"Databricks SQL guard blocked forbidden operation '{op_name}': {preview!r}. "
                    "SemaBridge never creates, drops, alters, or mutates user tables."
                )
                logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
                raise SQLSecurityGuardError(err)

        # 8. Default-deny catch-all
        preview = clean[:120].replace("\n", " ")
        err = (
            f"Databricks SQL guard blocked statement (default-deny allowlist): {preview!r}."
        )
        logger.error("🛑 DATABRICKS SQL GUARD: %s", err)
        raise SQLSecurityGuardError(err)
