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
    "table", "column", "date", "group", "order", "join", "view", "select", "from", "where",
    "and", "or", "not", "null", "true", "false", "as", "by", "on", "in", "is",
    # Additional SQL clauses
    "having", "limit", "offset", "union", "except", "intersect", "into",
    "insert", "update", "delete", "create", "drop", "alter", "grant", "revoke",
    "value",
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
            suppress_reserved: If True, prefix reserved words with 'COL_' in aliases
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
        Sanitize a column name to Snowflake-compatible format.

        Steps:
        1. Strips DAX table qualifiers (``'Table'[Column]`` → ``Column``).
        2. Splits dot-notation (``TABLE.COLUMN`` → ``COLUMN``).
        3. Replaces non-alphanumeric characters (except ``_`` and ``$``) with ``_``.
        4. Collapses consecutive underscores.
        5. Removes leading/trailing underscores (unless from digit-prefixing).
        6. If name starts with digit → prefix with underscore.
        7. Uppercases (when configured).

        Snowflake Rules:
        - Identifiers cannot start with a digit → prefix with ``_``
        - Only ``[A-Z0-9_$]`` allowed
        - Spaces/special chars → ``_``
        - Multiple underscores → collapse to single

        Examples:
            "18_MONTH_FORWARD" → "_18_MONTH_FORWARD"
            "Revenue % Growth" → "REVENUE__GROWTH"
            "customer-id" → "CUSTOMER_ID"

        Args:
            name: Raw column name

        Returns:
            Sanitized column name (NOT quoted)
        """
        if not name:
            return "UNKNOWN"

        original = name  # Track for logging

        # Strip DAX table qualifier (e.g., 'Sales'[Amount] → Amount)
        bracket_match = re.search(r"\[(.+?)\]", name)
        if bracket_match:
            name = bracket_match.group(1)

        # Split dot-notation — TABLE.COLUMN → COLUMN (only for 2-part refs)
        # Preserve 3+ part names (e.g., DB.SCHEMA.TABLE)
        if '.' in name and not name.startswith('"'):
            parts = name.split('.')
            if len(parts) == 2:
                left, right = parts
                # Only split if both parts are simple identifiers
                if re.fullmatch(r'[A-Za-z_]\w*', left) and re.fullmatch(r'[A-Za-z_]\w*', right):
                    name = right  # discard the table qualifier

        # Replace illegal characters with underscore (keep only [A-Za-z0-9_$])
        clean = re.sub(r"[^A-Za-z0-9_$]", "_", name)

        # Collapse consecutive underscores
        clean = re.sub(r"_+", "_", clean)

        # Strip leading/trailing underscores ONLY from replacements
        # but preserve those needed for digit-prefixing below
        clean = clean.strip("_")

        # Handle empty result
        if not clean:
            return "COLUMN_UNKNOWN"

        # CRITICAL: If name starts with digit, prefix with underscore (Snowflake requirement)
        if clean[0].isdigit():
            clean = f"_{clean}"

        # Uppercase
        result = clean.upper() if self.force_uppercase else clean

        # Reserved words are invalid semantic identifiers in Snowflake.
        # Apply mandatory COL_ prefix when enabled.
        if self.suppress_reserved and result.lower() in self._reserved:
            result = f"COL_{result}"

        # Validate: should only contain [A-Z0-9_$]
        if not re.match(r"^[A-Z_][A-Z0-9_$]*$", result):
            logger.warning(
                f"Identifier sanitization: '{original}' → '{result}' "
                f"contains unexpected characters after normalization"
            )

        logger.debug(f"sanitize_column: '{original}' → '{result}'")
        return result

    def sanitize_alias(self, name: str) -> str:
        """
        Sanitize an alias name with reserved word suppression.

        Steps: 
        1. Sanitize like column (remove special chars, uppercase, digit-prefix)
        2. If suppress_reserved and name is a reserved word, prefix with 'COL_'

        Args:
            name: Raw alias name

        Returns:
            Sanitized alias name (possibly prefixed with 'COL_' if reserved)
        """
        if not name:
            return "ALIAS"

        original = name
        # Alias follows the same normalization as columns so reserved words,
        # leading digits, and invalid characters are handled uniformly.
        sanitized = self.sanitize_column(name)
        logger.debug(f"sanitize_alias: '{original}' → '{sanitized}'")

        return sanitized

    def sanitize_table_name(self, name: str) -> str:
        """
        Sanitize a physical table name for Snowflake.

        Rules:
        - Replaces invalid characters (keep only [A-Za-z0-9_$]) with underscore
        - Collapses consecutive underscores
        - If name starts with digit → prefix with underscore
        - Uppercases

        Args:
            name: Raw table name

        Returns:
            Sanitized uppercase table name
        """
        if not name:
            return name

        original = name

        # Replace invalid characters (keep only [A-Za-z0-9_$])
        clean = re.sub(r"[^A-Za-z0-9_$]", "_", name)
        # Collapse consecutive underscores
        clean = re.sub(r"_+", "_", clean).strip("_")

        if not clean:
            return name.upper() if self.force_uppercase else name

        # Handle leading digit
        if clean[0].isdigit():
            clean = f"_{clean}"

        result = clean.upper() if self.force_uppercase else clean
        logger.debug(f"sanitize_table_name: '{original}' → '{result}'")
        return result

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


# ───────────────────────────────────────────────────────────────────────────
# IdentifierRegistry — Collision Detection & Resolution
# ───────────────────────────────────────────────────────────────────────────

class IdentifierRegistry:
    """
    Track sanitized identifiers and resolve collisions with numeric suffixes.

    When multiple different source identifiers sanitize to the same name,
    append numeric suffixes (_2, _3, etc.) to maintain uniqueness while keeping
    names deterministic.

    Example:
        "Revenue %" and "Revenue $" both sanitize to "REVENUE".
        First one: "REVENUE" (no suffix)
        Second one: "REVENUE_2" (collision, append _2)
    """

    def __init__(self, sanitizer: Optional[IdentifierSanitizer] = None):
        """
        Initialize the registry.

        Args:
            sanitizer: Optional IdentifierSanitizer instance to use for sanitization
                      Defaults to a new instance with standard settings
        """
        self._sanitizer = sanitizer or IdentifierSanitizer()
        # Map: original_name → sanitized_name (deterministic)
        self._seen: Dict[str, str] = {}
        # Map: sanitized_name → count of collisions
        self._collision_count: Dict[str, int] = {}
        # Log all transformations
        self._transformations: List[Tuple[str, str]] = []

    def register(self, name: str, name_type: str = "column") -> str:
        """
        Register an identifier and resolve any collisions.

        Steps:
        1. Sanitize the name
        2. Check if already seen (return cached)
        3. Check if collision (append numeric suffix if needed)
        4. Log transformation
        5. Return final name

        Args:
            name: Original identifier name
            name_type: Type of identifier ('column', 'table', 'alias', 'metric')

        Returns:
            Final sanitized identifier (possibly with _2, _3, etc. suffix for collisions)
        """
        # Check if already registered
        if name in self._seen:
            return self._seen[name]

        # Sanitize based on type
        if name_type == "table":
            sanitized = self._sanitizer.sanitize_table_name(name)
        elif name_type == "alias":
            sanitized = self._sanitizer.sanitize_alias(name)
        else:  # column, metric, or default
            sanitized = self._sanitizer.sanitize_column(name)

        # Check for collision
        final_name = sanitized
        if sanitized in self._collision_count:
            # Collision detected - use occurrence number for suffix
            # First occurrence: counter=0, suffix=_2
            # Second occurrence: counter=1, suffix=_3
            occurrence_num = self._collision_count[sanitized] + 2
            final_name = f"{sanitized}_{occurrence_num}"
            self._collision_count[sanitized] += 1
        else:
            # First occurrence of this sanitized name - track it
            self._collision_count[sanitized] = 0

        # Cache the result
        self._seen[name] = final_name

        # Log transformation
        if name != final_name:
            log_msg = f"{name_type}: '{name}' → '{final_name}'"
            if sanitized != final_name:
                log_msg = f"{name_type}: '{name}' → '{sanitized}' (collision) → '{final_name}'"
            self._transformations.append((name, final_name))
            logger.info(log_msg)
        else:
            logger.debug(f"{name_type}: '{name}' (unchanged)")

        return final_name

    @property
    def transformations(self) -> List[Tuple[str, str]]:
        """Get list of all name transformations (original → final)."""
        return self._transformations.copy()

    def get_collision_summary(self) -> Dict[str, int]:
        """
        Get summary of collisions detected.

        Returns:
            Dict mapping sanitized_name → collision_count
                (entries with count > 0 had collisions)
        """
        return {name: count for name, count in self._collision_count.items() if count > 0}

    def log_summary(self) -> None:
        """Log a summary of all transformations and collisions."""
        logger.info(f"Identifier Registry Summary:")
        logger.info(f"  Total identifiers registered: {len(self._seen)}")
        logger.info(f"  Total transformations: {len(self._transformations)}")

        collisions = self.get_collision_summary()
        if collisions:
            logger.info(f"  Collisions detected: {len(collisions)}")
            for name, count in sorted(collisions.items()):
                logger.info(f"    - '{name}': {count} collision(s)")
        else:
            logger.info(f"  Collisions detected: 0")

        if self._transformations:
            logger.debug("Transformations:")
            for original, final in self._transformations:
                logger.debug(f"  '{original}' → '{final}'")

