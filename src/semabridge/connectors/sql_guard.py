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

import re
from typing import Any, Callable, List, Optional, Set, Tuple
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


# Global or thread-local set of object names allowed for current project/model
_ALLOWED_MODEL_OBJECTS: Set[str] = set()
_ALLOWED_ABSENT_TABLES: Set[str] = set()

def set_allowed_model_objects(names: Set[str]) -> None:
    """Register the set of valid object names (uppercase) produced by SemaBridge for current project/model."""
    global _ALLOWED_MODEL_OBJECTS
    _ALLOWED_MODEL_OBJECTS.update({n.strip('"').upper() for n in names if n})

def get_allowed_model_objects() -> Set[str]:
    """Get currently registered allowed object names."""
    return _ALLOWED_MODEL_OBJECTS

def clear_allowed_model_objects() -> None:
    """Clear registered allowed model objects."""
    global _ALLOWED_MODEL_OBJECTS
    _ALLOWED_MODEL_OBJECTS = set()


def set_allowed_absent_tables(names: Set[str]) -> None:
    """Register uppercase physical table names confirmed absent in the live schema that SemaBridge may create empty."""
    global _ALLOWED_ABSENT_TABLES
    _ALLOWED_ABSENT_TABLES.update({n.strip('"').upper() for n in names if n})

def get_allowed_absent_tables() -> Set[str]:
    """Get currently registered allowed absent table names."""
    return _ALLOWED_ABSENT_TABLES

def clear_allowed_absent_tables() -> None:
    """Clear registered allowed absent table names."""
    global _ALLOWED_ABSENT_TABLES
    _ALLOWED_ABSENT_TABLES = set()


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
