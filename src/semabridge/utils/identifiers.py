"""
Mandate 1: IdentifierSanitizer — unified identifier hygiene across semabridge.

Provides:
- Centralized column/alias/table name sanitization
- Snowflake reserved word detection and suppression  
- Physical table reference validation (TABLE.COLUMN dot notation parsing)
- SQL function name detection for DAX-to-SQL translation

This module consolidates identifier handling that was previously scattered
across emitter, translator, and validator modules.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Set, Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────────
# Reserved Words
# ───────────────────────────────────────────────────────────────────────────

SNOWFLAKE_RESERVED_WORDS: Set[str] = {
    # SQL Keywords
    "table", "date", "group", "order", "join", "view", "select", "from", "where",
    "and", "or", "not", "null", "true", "false", "as", "by", "on", "in", "is",
    # Additional SQL clauses
    "having", "limit", "offset", "union", "except", "intersect", "into",
    "insert", "update", "delete", "create", "drop", "alter", "grant", "revoke",
    # Aggregate functions
    "count", "sum", "avg", "min", "max",
    # Snowflake-specific
    "current", "session", "account", "database", "schema", "user", "role",
    "warehouse", "stage", "sequence", "stream", "task", "pipe",
    # Data types
    "integer", "varchar", "boolean", "float", "number", "string", "timestamp",
    # Window functions
    "over", "partition", "row", "rows", "range", "between",
    # Other reserved
    "all", "any", "some", "exists", "case", "when", "then", "else", "end",
    "distinct", "unique", "primary", "foreign", "key", "references",
    "constraint", "index", "default", "check", "like", "ilike",
}

SQL_FUNCTION_NAMES: Set[str] = {
    # Aggregate functions
    "count", "sum", "avg", "min", "max", "stddev", "variance",
    # String functions  
    "concat", "substr", "length", "upper", "lower", "trim", "ltrim", "rtrim",
    "replace", "instr", "substring",
    # Numeric functions
    "abs", "round", "floor", "ceiling", "power", "log", "ln", "exp",
    # Date functions
    "date_part", "extract", "dateadd", "datediff", "now", "current_date", "current_time",
    # Conditional functions
    "if", "ifnull", "nullif", "coalesce",
    # Type conversion
    "cast", "convert", "to_char", "to_number", "to_date", "to_timestamp",
    # Window functions
    "row_number", "rank", "dense_rank", "lead", "lag", "first_value", "last_value",
}

# ───────────────────────────────────────────────────────────────────────────
# Pattern Definitions
# ───────────────────────────────────────────────────────────────────────────

# Regex to match physical table references (TABLE.COLUMN)
_PHYSICAL_SOURCE_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$')

# Regex to match illegal identifier characters (anything not alphanumeric, underscore, or dollar sign)
_ILLEGAL_CHAR_PATTERN = re.compile(r'[^A-Za-z0-9_$]')


class IdentifierSanitizer:
    """
    Unified identifier sanitization for semantic models.

    Responsibilities:
    1. Sanitize column names (remove special chars, uppercase)
    2. Sanitize alias names (add prefix for reserved words)
    3. Sanitize table names (convert to uppercase snake_case)
    4. Parse physical source column references (TABLE.COLUMN)
    5. Detect reserved words and SQL functions
    """

    def __init__(
        self,
        *,
        force_uppercase: bool = True,
        always_quote: bool = True,
        suppress_reserved: bool = True,
        additional_reserved: "Optional[set]" = None,
    ):
        """
        Initialize the sanitizer.

        Args:
            force_uppercase: If True, uppercase all identifiers
            always_quote: If True, always quote identifiers in SQL
            suppress_reserved: If True, prefix reserved words with 'L_' in aliases
            additional_reserved: Additional reserved words to treat as reserved (beyond SNOWFLAKE_RESERVED)
        """
        self.force_uppercase = force_uppercase
        self.always_quote = always_quote
        self.suppress_reserved = suppress_reserved
        self._reserved = SNOWFLAKE_RESERVED_WORDS | (additional_reserved or set())

    @staticmethod
    def is_physical_source_column(source_expr: str) -> bool:
        """
        Determine if a source expression is a physical table reference.

        Physical format: ``TABLE.COLUMN`` (e.g., ``L_CUSTOMER.CUSTOMER_ID``)
        Logical format: anything else (e.g., ``[Sales].Amount`` or ``Amount``)

        Args:
            source_expr: Source expression string

        Returns:
            True if the expression matches TABLE.COLUMN pattern, False otherwise
        """
        if not source_expr:
            return False
        return bool(_PHYSICAL_SOURCE_PATTERN.match(source_expr))

    def sanitize_column(self, name: str) -> str:
        """
        Sanitize a column name.

        - Strips DAX table qualifiers (``'Table'[Column]`` → ``Column``).
        - Splits dot-notation (``TABLE.COLUMN`` → ``COLUMN``).
        - Replaces non-alphanumeric characters with ``_``.
        - Collapses consecutive underscores, strips leading/trailing ``_``.
        - Uppercases (when configured).

        Args:
            name: Raw column name

        Returns:
            Sanitized column name (NOT quoted)
        """
        if not name:
            return "UNKNOWN"

        # Strip DAX table qualifier
        bracket_match = re.search(r"\[(.+?)\]", name)
        if bracket_match:
            name = bracket_match.group(1)

        # Module 2: Split dot-notation — TABLE.COLUMN → COLUMN.
        # Only when *both* parts are simple identifiers (no spaces, no
       # expressions, no quoted segments).  Three-part names like
        # DB.SCHEMA.TABLE are NOT column references — leave as-is.
        if '.' in name and not name.startswith('"'):
            parts = name.split('.')
            if len(parts) == 2:
                left, right = parts
                if re.fullmatch(r'[A-Za-z_]\w*', left) and re.fullmatch(r'[A-Za-z_]\w*', right):
                    name = right  # discard the table qualifier

        # Replace illegal characters with underscore
        clean = re.sub(r"[^a-zA-Z0-9]", "_", name)
        # Collapse consecutive underscores and strip leading/trailing
        clean = re.sub(r"_+", "_", clean).strip("_")

        if not clean:
            return "COLUMN_UNKNOWN"

        return clean.upper() if self.force_uppercase else clean

    def sanitize_alias(self, name: str) -> str:
        """
        Sanitize an alias name with reserved word suppression.

        Steps: 
        1. Sanitize like column (remove special chars, uppercase)
        2. If suppress_reserved and name is a reserved word, prefix with 'L_'

        Args:
            name: Raw alias name

        Returns:
            Sanitized alias name (possibly prefixed)
        """
        if not name:
            return "ALIAS"

        # First sanitize like a column
        sanitized = self.sanitize_column(name)

        # Check if reserved and suppress if configured
        # Note: Reserved words are stored in lowercase, so compare lowercase
        if self.suppress_reserved and sanitized.lower() in self._reserved:
            sanitized = "L_" + sanitized

        return sanitized

    def sanitize_table_name(self, name: str) -> str:
        """Sanitize a physical table name for Snowflake.

        Like :meth:`sanitize_column` but tailored for table-level
        identifiers — strips special characters, uppercases.
        """
        if not name:
            return name

        clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
        clean = re.sub(r"_+", "_", clean).strip("_")
        return clean.upper() if self.force_uppercase else clean

    def split_dot_identifier(self, identifier: str) -> tuple[Optional[str], str]:
        """
        Split a dot notation identifier into table and column parts.

        Examples:
            "TABLE.COLUMN" → ("TABLE", "COLUMN")
            "COLUMN" → (None, "COLUMN")
            "A.B.C" → ("A.B", "C")

        Args:
            identifier: Dot-delimited identifier string

        Returns:
            Tuple of (table_part, column_part) or (None, column_part) if no dots
        """
        if not identifier:
            return None, ""

        if "." not in identifier:
            return None, identifier

        # Split on the last dot
        parts = identifier.rsplit(".", 1)
        return parts[0], parts[1]

    @staticmethod
    def is_sql_function(name: str) -> bool:
        """
        Determine if a name is a recognized SQL function.

        Args:
            name: Function name to check

        Returns:
            True if name is a known SQL function, False otherwise
        """
        return name.lower() in SQL_FUNCTION_NAMES

    # ─── Module 2: Centralized dot-notation resolution ──────────────────

    # Master regex for cross-table references: matches WORD."COL" or WORD.WORD
    _DOT_REF_RE = re.compile(
        r'\b([A-Za-z_]\w*)\s*(\.\.?\s*"[^"]+"|\.\.?(?:[A-Za-z_]\w*))'
    )
    # Simpler pattern for defence-in-depth: finds remaining TABLE. prefixes
    _TABLE_PREFIX_RE = re.compile(r'\b([A-Z_]\w*)\.')

    def resolve_dot_notation(
        self,
        expr: str,
        alias_lookup: Dict[str, str],
        *,
        sanitize_col_fn: Optional[object] = None,
    ) -> str:
        """Rewrite ``TABLE.COLUMN`` references in *expr* using *alias_lookup*.

        Replaces every ``TABLE.COLUMN`` (or ``TABLE."COL"``) where
        ``TABLE`` maps to a known alias with the sanitized alias +
        quoted column form.

        This consolidates the 6 duplicated regex rewrite closures
        previously scattered in ``generate_ddls`` and
        ``generate_ddls_from_osi``.

        Args:
            expr:  SQL expression to rewrite.
            alias_lookup: ``{RAW_TABLE_UPPER: sanitized_alias}`` mapping.
            sanitize_col_fn: Optional callable ``(col_name) -> str``.  Defaults
                to ``self.sanitize_column``.

        Returns:
            Rewritten expression.
        """
        _sanitize = sanitize_col_fn or self.sanitize_column

        def _rewrite(m: re.Match) -> str:
            raw_table = m.group(1).upper()
            dot_rest = m.group(2)  # e.g. ."COL" or .COL
            resolved = alias_lookup.get(raw_table)
            if resolved:
                col_part = dot_rest.lstrip('. ')
                if not col_part.startswith('"'):
                    col_part = f'"{_sanitize(col_part)}"'
                return f"{resolved}.{col_part}"
            return m.group(0)  # leave untouched

        return self._DOT_REF_RE.sub(_rewrite, expr)

    def validate_table_refs(
        self,
        expr: str,
        valid_aliases: Set[str],
        valid_functions: Optional[Set[str]] = None,
    ) -> List[str]:
        """Return unresolved ``TABLE.`` prefix references in *expr*.

        Any ``TABLE.`` prefix that is not in *valid_aliases* and not a
        known SQL function is returned as an invalid reference.

        Args:
            expr: SQL expression to inspect.
            valid_aliases: Set of valid table aliases (uppercase).
            valid_functions: Set of SQL function names to skip.
                Defaults to :data:`SQL_FUNCTION_NAMES`.

        Returns:
            List of invalid table reference strings.
        """
        funcs = valid_functions if valid_functions is not None else SQL_FUNCTION_NAMES
        refs = self._TABLE_PREFIX_RE.findall(expr)
        return [
            t for t in refs
            if t not in valid_aliases and t not in funcs
        ]

    @staticmethod
    def split_dot_identifier(name: str) -> Tuple[Optional[str], str]:
        """Split a potentially dot-qualified identifier.

        Handles:
          - ``COLUMN``           → ``(None, "COLUMN")``
          - ``TABLE.COLUMN``     → ``("TABLE", "COLUMN")``
          - ``SCHEMA.TABLE.COL`` → ``("TABLE", "COL")``  (last 2 parts)
          - ``DB.SCH.TBL.COL``   → ``("TBL", "COL")``

        Quoted segments (``"My Col"``) are preserved.

        Returns:
            Tuple of ``(table_or_none, column)``.
        """
        if not name or '.' not in name:
            return (None, name or "")

        # Split on dots, respecting double-quoted segments
        parts: List[str] = []
        current: List[str] = []
        in_quote = False
        for ch in name:
            if ch == '"':
                in_quote = not in_quote
                current.append(ch)
            elif ch == '.' and not in_quote:
                parts.append(''.join(current))
                current = []
            else:
                current.append(ch)
        parts.append(''.join(current))

        if len(parts) == 1:
            return (None, parts[0])
        # Take the last two parts (TABLE.COLUMN)
        return (parts[-2], parts[-1])
