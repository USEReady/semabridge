"""
Centralized Snowflake Naming Convention.

Single source of truth for converting logical model names into
physical table names, SQL aliases, and column identifiers.

Naming Levels
─────────────
1. Logical Name   — CamelCase from the semantic YAML/model (``SalesFact``)
2. Physical Name  — ``UPPER_SNAKE_CASE`` for Snowflake tables (``SALES_FACT``)
3. SQL Alias      — ``lowercase`` for use in generated SQL    (``salesfact``)

Every SQL-generation path (emitter, DAX translator, AST renderer,
join builder, metric translator) **must** call these helpers instead
of rolling its own inline conversion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from semabridge.utils.identifiers import SNOWFLAKE_RESERVED_WORDS as SNOWFLAKE_RESERVED

# ── Illegal identifier characters ────────────────────────────────────────────
_ILLEGAL_CHAR_RE = re.compile(r"[^A-Za-z0-9_]")

# ── CamelCase boundary detector ──────────────────────────────────────────────
# Inserts ``_`` at CamelCase boundaries:
#   SalesFact      → Sales_Fact
#   ABCProduct     → ABC_Product
#   CustomerDimV2  → Customer_Dim_V2
_CC_BOUNDARY_1 = re.compile(r"(.)([A-Z][a-z]+)")      # xA → x_A
_CC_BOUNDARY_2 = re.compile(r"([a-z0-9])([A-Z])")     # aB → a_B


# =====================================================================
# Public API
# =====================================================================

def camel_to_snake(name: str) -> str:
    """Convert a CamelCase or mixed-case name to ``snake_case``.

    Already-underscored names pass through unchanged (idempotent).

    Examples::

        >>> camel_to_snake("SalesFact")
        'sales_fact'
        >>> camel_to_snake("CustomerDim")
        'customer_dim'
        >>> camel_to_snake("ABCProduct")
        'abc_product'
        >>> camel_to_snake("SALES_FACT")
        'sales_fact'
        >>> camel_to_snake("Product")
        'product'
    """
    s = _CC_BOUNDARY_1.sub(r"\1_\2", name)
    s = _CC_BOUNDARY_2.sub(r"\1_\2", s)
    # Collapse multiple consecutive underscores (e.g. Fact_Sales → Fact__Sales → Fact_Sales)
    s = re.sub(r"_+", "_", s)
    return s.lower()


def to_physical_name(logical_name: str) -> str:
    """Derive the **physical Snowflake table name** from a logical name.

    Conversion rules:
    * CamelCase → ``UPPER_SNAKE_CASE``
    * Spaces / hyphens / illegal chars → ``_``
    * Collapse consecutive underscores
    * Result is always uppercase

    Examples::

        >>> to_physical_name("SalesFact")
        'SALES_FACT'
        >>> to_physical_name("Customer Dim")
        'CUSTOMER_DIM'
        >>> to_physical_name("Product")
        'PRODUCT'
        >>> to_physical_name("Fact_Sales")
        'FACT_SALES'
    """
    if not logical_name:
        return logical_name

    # First, split CamelCase with underscores
    snake = camel_to_snake(logical_name)

    # Replace remaining illegal chars
    clean = _ILLEGAL_CHAR_RE.sub("_", snake)

    # Collapse multiple underscores and strip leading/trailing
    clean = re.sub(r"_+", "_", clean).strip("_")

    return clean.upper()


def to_alias(logical_name: str) -> str:
    """Derive a **lowercase SQL alias** from a logical name.

    Conversion rules:
    * Strip non-alphanumeric characters (keep only ``[a-z0-9]``)
    * Result is always lowercase, no underscores
    * Reserved words are prefixed with ``l_``

    Examples::

        >>> to_alias("SalesFact")
        'salesfact'
        >>> to_alias("Customer Dim")
        'customerdim'
        >>> to_alias("Date")
        'l_date'
    """
    if not logical_name:
        return logical_name

    clean = "".join(c for c in logical_name if c.isalnum()).lower()

    if not clean:
        clean = "t"
    if clean[0].isdigit():
        clean = f"t{clean}"
    if clean in SNOWFLAKE_RESERVED:
        clean = f"l_{clean}"

    return clean


def build_alias_rewrite_map(
    dataset_aliases: Dict[str, str],
    database: str = "",
    schema_name: str = "",
    extra_prefix_map: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build a comprehensive map of **all** invalid identifier prefixes to
    their correct lowercase alias.

    For each logical name in *dataset_aliases* the map contains:

    * Uppercase-alphanumeric form (e.g. ``SALESFACT``)
    * Physical table name (e.g. ``SALES_FACT``)
    * The original logical name itself (e.g. ``SalesFact``)

    If *database* / *schema_name* are provided they are added as known
    schema-level prefixes that must **never** appear as column prefixes.

    *extra_prefix_map* accepts user-declared or auto-inferred short-alias
    entries (e.g. ``{'FACT': 'factsales', 'CALENDAR': 'calendardim'}``).
    These are merged last so they override auto-derived entries when there
    is a collision.

    Returns:
        ``{invalid_prefix_upper: correct_alias}``  — all keys are uppercase
        so callers can do case-insensitive matching via ``key.upper()``.
    """
    rewrite: Dict[str, str] = {}
    for logical, alias in dataset_aliases.items():
        # 1. Uppercase-alphanumeric  (SALESFACT)
        uc_alpha = "".join(c for c in logical if c.isalnum()).upper()
        if uc_alpha != alias:
            rewrite[uc_alpha] = alias

        # 2. Physical table name  (SALES_FACT)
        physical = to_physical_name(logical)
        if physical and physical != alias.upper():
            rewrite[physical] = alias

        # 3. Original logical name  (SalesFact)
        if logical.upper() != alias.upper():
            rewrite[logical.upper()] = alias

    # Database / schema should never be used as column prefixes
    if database:
        rewrite[database.upper()] = ""  # empty → signals "reject"
    if schema_name:
        rewrite[schema_name.upper()] = ""  # empty → signals "reject"

    # User-declared / auto-inferred short aliases (highest priority)
    if extra_prefix_map:
        for short_prefix, alias in extra_prefix_map.items():
            rewrite[short_prefix.upper()] = alias

    return rewrite


# ── Prefix / alias inference helpers ─────────────────────────────────────────

_PREFIX_IN_EXPR_RE = re.compile(r"\b([A-Z][A-Z0-9_]+)\.", re.IGNORECASE)


def extract_prefixes_from_expressions(expressions: List[str]) -> set:
    """Scan *expressions* and return the set of all ``PREFIX.`` tokens found.

    Only extracts tokens that are fully uppercase (or contain underscores),
    which are the patterns that trigger the validator's Check 3.

    Examples::

        >>> extract_prefixes_from_expressions(['SUM(FACT."REVENUE")', 'MIN(CALENDAR.DATE)'])
        {'FACT', 'CALENDAR'}
    """
    found: set = set()
    for expr in expressions:
        if not expr:
            continue
        for m in _PREFIX_IN_EXPR_RE.finditer(expr):
            token = m.group(1).upper()
            found.add(token)
    return found


def infer_override_alias_map(
    override_prefixes: set,
    dataset_aliases: Dict[str, str],
) -> Dict[str, str]:
    """Attempt to map unknown *override_prefixes* to known dataset aliases.

    Matching strategy (tried in order):
    1. Exact-match against the uppercase-alphanumeric form of each logical name.
    2. Exact-match against the physical table name of each logical name.
    3. Substring: the prefix is fully contained in the physical name
       (e.g. ``FACT`` ⊂ ``FACT_SALES``).
    4. Substring: the physical name is fully contained in the prefix.

    Returns a dict of ``{UPPERCASE_PREFIX: correct_alias}`` for every prefix
    that could be resolved.  Unresolvable prefixes are omitted — callers
    should log a warning for those.
    """
    result: Dict[str, str] = {}
    for prefix in override_prefixes:
        p_upper = prefix.upper()
        # Pre-build lookup variants once
        candidates: List[tuple] = []
        for logical, alias in dataset_aliases.items():
            uc_alpha = "".join(c for c in logical if c.isalnum()).upper()
            physical = to_physical_name(logical)
            candidates.append((uc_alpha, physical, alias))

        matched: Optional[str] = None

        # Strategy 1 & 2: exact match
        for uc_alpha, physical, alias in candidates:
            if p_upper == uc_alpha or p_upper == physical:
                matched = alias
                break

        # Strategy 3 & 4: substring match
        if matched is None:
            for uc_alpha, physical, alias in candidates:
                if p_upper in physical or physical in p_upper:
                    matched = alias
                    break

        if matched is not None:
            result[p_upper] = matched

    return result


# ── Shared SQL expression sanitizer ──────────────────────────────────────────

def sanitize_sql_expression(
    expr: str,
    dataset_aliases: Dict[str, str],
    database: str = "",
    schema_name: str = "",
    extra_prefix_map: Optional[Dict[str, str]] = None,
    force_uppercase: bool = True,
) -> str:
    """Normalize *expr* so all identifier references match the Snowflake
    Semantic View quoting convention: ``lowercase_alias."UPPER_COL"``.

    This is the **single authoritative** implementation of the 6-step
    expression rewrite pipeline.  Both the Snowflake emitter and the OSI→SML
    override pre-sanitizer delegate here so they stay in sync.

    Pipeline:

    1. Convert TMSL bracket notation ``[Name]`` → ``"NAME"``
    2. Sanitize existing double-quoted identifiers (strip illegal chars)
    3. Build rewrite map from dataset_aliases + extra_prefix_map + db/schema
    4. Rewrite ``INVALID_PREFIX."COL"`` → ``alias."COL"``
    5. Rewrite ``INVALID_PREFIX.bareCol`` → ``alias."COL"``
    6. Safety-net: quote any remaining ``alias.bareCol`` → ``alias."COL"``
    7. Catch-all: strip 3-part qualified names (``DB.SCHEMA.col``)

    Args:
        expr: Raw SQL expression string.
        dataset_aliases: Mapping ``logical_name → alias`` from the SML model.
        database: Snowflake database name (prefix to strip).
        schema_name: Snowflake schema name (prefix to strip).
        extra_prefix_map: User-declared or auto-inferred
            ``{SHORT_PREFIX_UPPER: alias}`` entries.
        force_uppercase: If True, column names inside quotes are uppercased.

    Returns:
        Sanitized SQL expression string.
    """
    if not expr:
        return expr

    result = expr

    # ── Step 1: bracket notation → quoted identifier ──────────────────────
    def _fix_bracket(m: re.Match) -> str:
        return f'"{sanitize_column(m.group(1), force_uppercase=force_uppercase)}"'

    result = re.sub(r'\[([^\]]+)\]', _fix_bracket, result)

    # ── Step 2: sanitize existing double-quoted identifiers ───────────────
    def _fix_quoted(m: re.Match) -> str:
        return f'"{sanitize_column(m.group(1), force_uppercase=force_uppercase)}"'

    result = re.sub(r'"([^"]+)"', _fix_quoted, result)

    # ── Step 7: strip 3-part qualified names (DB.SCHEMA.col) first ───────
    # Must run before the 2-part rules to avoid partial matches.
    if database and schema_name:
        result = re.sub(
            rf'\b{re.escape(database)}\.{re.escape(schema_name)}\.("?)([A-Za-z_]\w*)\1',
            lambda m: f'"{sanitize_column(m.group(2), force_uppercase=force_uppercase)}"',
            result,
            flags=re.IGNORECASE,
        )

    if not dataset_aliases:
        return result

    # ── Steps 3-6: alias + column normalisation ───────────────────────────
    rewrite_map = build_alias_rewrite_map(
        dataset_aliases,
        database=database,
        schema_name=schema_name,
        extra_prefix_map=extra_prefix_map,
    )

    # Step 4 — rewrite INVALID_PREFIX."COL" → alias."COL"
    for bad_prefix in sorted(rewrite_map, key=len, reverse=True):
        new_alias = rewrite_map[bad_prefix]
        if not new_alias:
            result = re.sub(
                rf'\b{re.escape(bad_prefix)}\."',
                '"',
                result,
                flags=re.IGNORECASE,
            )
            continue
        result = re.sub(
            rf'\b{re.escape(bad_prefix)}\."',
            f'{new_alias}."',
            result,
            flags=re.IGNORECASE,
        )

    # Step 5 — rewrite INVALID_PREFIX.bareCol → alias."COL"
    for bad_prefix in sorted(rewrite_map, key=len, reverse=True):
        new_alias = rewrite_map[bad_prefix]
        if not new_alias:
            def _strip_prefix_bare(m: re.Match, _fuc: bool = force_uppercase) -> str:
                col = sanitize_column(m.group(1), force_uppercase=_fuc)
                return f'"{col}"'

            result = re.sub(
                rf'\b{re.escape(bad_prefix)}\.([A-Za-z_]\w*)',
                _strip_prefix_bare,
                result,
                flags=re.IGNORECASE,
            )
            continue

        def _rewrite_bare(m: re.Match, _alias: str = new_alias, _fuc: bool = force_uppercase) -> str:
            col = sanitize_column(m.group(1), force_uppercase=_fuc)
            return f'{_alias}."{col}"'

        result = re.sub(
            rf'\b{re.escape(bad_prefix)}\.([A-Za-z_]\w*)',
            _rewrite_bare,
            result,
            flags=re.IGNORECASE,
        )

    # Step 6 — safety net: quote remaining bare cols after known aliases
    for alias_val in set(dataset_aliases.values()):
        def _quote_bare(m: re.Match, _a: str = alias_val, _fuc: bool = force_uppercase) -> str:
            col = sanitize_column(m.group(1), force_uppercase=_fuc)
            return f'{_a}."{col}"'

        result = re.sub(
            rf'\b{re.escape(alias_val)}\.([A-Za-z_]\w*)',
            _quote_bare,
            result,
        )

    return result


def sanitize_column(name: str, force_uppercase: bool = True) -> str:
    """Sanitize a column name for Snowflake physical column identifiers.

    * Strips TMSL bracket notation ``[Col Name]``
    * Replaces spaces, dots, slashes → ``_``
    * Escapes embedded double-quotes
    * Optionally uppercases

    The result is meant to be placed **inside** double-quotes in DDL:
    ``"SANITIZED_NAME"``.
    """
    if not name:
        return "UNKNOWN"

    clean = name.strip()
    if clean.startswith("[") and clean.endswith("]"):
        clean = clean[1:-1]

    for ch in (" ", ".", "/", "\\"):
        clean = clean.replace(ch, "_")

    clean = clean.replace('"', '""')

    if force_uppercase:
        return clean.upper()
    return clean


def sanitize_semantic_label(name: str, force_uppercase: bool = True) -> str:
    """Sanitize a name for the *label* (AS) side of Semantic View identifiers.

    Same rules as ``sanitize_column`` — Snowflake Semantic Views do **not**
    allow spaces even in the label/alias position.
    """
    return sanitize_column(name, force_uppercase=force_uppercase)


# =====================================================================
# SQL Validation (Step 7)
# =====================================================================

@dataclass
class SQLValidationResult:
    """Outcome of pre-deployment SQL validation."""

    valid: bool
    errors: List[str]
    warnings: List[str]

    def __str__(self) -> str:
        parts = [f"valid={self.valid}"]
        if self.errors:
            parts.append(f"errors={self.errors}")
        if self.warnings:
            parts.append(f"warnings={self.warnings}")
        return f"SQLValidationResult({', '.join(parts)})"


def _extract_section_balanced(ddl: str, section_name: str) -> Optional[tuple]:
    """Return a ``(match_start, match_end, body_text)`` tuple for *section_name*
    in *ddl*, using balanced-parenthesis counting to correctly handle nested
    parens inside ``PRIMARY KEY (...)`` and similar clauses.

    Returns ``None`` if the section is not found.
    """
    header_pat = re.compile(rf"\b{re.escape(section_name)}\s*\(", re.IGNORECASE)
    m = header_pat.search(ddl)
    if not m:
        return None

    open_idx = m.end() - 1  # position of '('
    depth = 0
    i = open_idx
    while i < len(ddl):
        if ddl[i] == "(":
            depth += 1
        elif ddl[i] == ")":
            depth -= 1
            if depth == 0:
                body = ddl[open_idx + 1 : i]
                return (m.start(), i + 1, body)
        i += 1
    # Unbalanced — return what we have
    return (m.start(), len(ddl), ddl[open_idx + 1 :])


def validate_semantic_view_sql(
    ddl: str,
    dataset_aliases: Dict[str, str],
    logical_names: Optional[List[str]] = None,
    database: str = "",
    schema_name: str = "",
) -> SQLValidationResult:
    """Validate generated Semantic View DDL before deployment.

    Checks:
    1. Every alias referenced in DIMENSIONS / METRICS / RELATIONSHIPS
       exists in the TABLES clause.
    2. No raw logical model names (CamelCase originals) appear
       in the final SQL.
    3. No ``UPPER_ALIAS.col`` patterns that mix physical name with column
       (e.g. ``SALESFACT.SCORE`` instead of ``salesfact."SCORE"``).
    4. No physical table names used as column prefixes
       (e.g. ``SALES_FACT.SCORE`` instead of ``salesfact."SCORE"``).
    5. No database or schema names used as column prefixes
       (e.g. ``ANALYTICS_DB.SCORE``).

    Args:
        ddl: The full ``CREATE OR REPLACE SEMANTIC VIEW …`` string.
        dataset_aliases: Mapping ``logical_name → alias`` built during
            ``_generate_semantic_view``.
        logical_names: Optional list of original logical table names to
            verify they don't leak into the SQL body.
        database: Snowflake database name (for prefix detection).
        schema_name: Snowflake schema name (for prefix detection).

    Returns:
        ``SQLValidationResult`` — the DDL is safe to execute if
        ``result.valid`` is True.
    """
    errors: List[str] = []
    warnings: List[str] = []
    valid_aliases = set(dataset_aliases.values())

    # ── Parse aliases from TABLES clause ────────────────────────────────
    # Use balanced-paren extraction so PRIMARY KEY (…) inside TABLES
    # does not cause the lazy regex to stop too early.
    tables_section = _extract_section_balanced(ddl, "TABLES")
    declared_aliases: set = set()
    tables_end_idx: int = 0
    if tables_section:
        _ts_start, tables_end_idx, tables_body = tables_section
        # Each entry:  alias AS db.schema."TABLE" PRIMARY KEY (…)
        for line in tables_body.splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped:
                continue
            alias_token = stripped.split()[0] if stripped.split() else ""
            if alias_token:
                declared_aliases.add(alias_token)

    # ── Check 1: Alias usage in DIMENSIONS / METRICS / RELATIONSHIPS ───
    for section_name in ("DIMENSIONS", "METRICS", "RELATIONSHIPS"):
        sec = _extract_section_balanced(ddl, section_name)
        if not sec:
            continue
        _s, _e, body = sec
        # Find alias references: alias."COL" or alias (COL) REFERENCES alias
        alias_refs = re.findall(r"\b(\w+)\.", body)
        for ref in alias_refs:
            if ref.lower() in ("db", "semantic_layer") or ref.isupper():
                # Skip schema-qualified parts (db.SCHEMA."TABLE")
                continue
            if ref not in declared_aliases and ref not in valid_aliases:
                errors.append(
                    f"{section_name}: alias '{ref}' is referenced but not "
                    f"declared in TABLES clause"
                )

    # ── Check 2: Logical names leaking into SQL body ───────────────────
    # Only check inside DIMENSIONS/METRICS/RELATIONSHIPS, not the TABLES
    # clause itself (which legitimately contains the model context).
    logical_names = logical_names or list(dataset_aliases.keys())
    # Skip TABLES section for this check (it legitimately contains db/schema names)
    check_body = ddl[tables_end_idx:] if tables_end_idx else ddl

    for logical_name in logical_names:
        # Skip short / common names that would cause false positives
        if len(logical_name) < 4:
            continue
        # Look for the exact logical name (case-sensitive) as a bare word
        pattern = rf"\b{re.escape(logical_name)}\b"
        matches = list(re.finditer(pattern, check_body))
        if matches:
            warnings.append(
                f"Logical name '{logical_name}' appears in generated SQL "
                f"body — may indicate an un-resolved identifier"
            )

    # ── Check 3: UPPERCASE alias + dot pattern ─────────────────────────
    # e.g. SALESFACT.SCORE   (wrong)
    #      salesfact."SCORE"  (correct)
    uppercase_dot = re.findall(r"\b([A-Z][A-Z0-9_]+)\.(?!\")", check_body)
    # Build a set of schema-level identifiers that are allowed uppercase
    _schema_ignore = {"SEMANTIC_LAYER", "SEMABRIDGE", "PUBLIC"}
    _flagged_prefixes: set = set()
    for uc_ref in uppercase_dot:
        if uc_ref in _schema_ignore or uc_ref in _flagged_prefixes:
            continue
        _flagged_prefixes.add(uc_ref)
        errors.append(
            f"Uppercase alias pattern '{uc_ref}.<col>' found — should be "
            f"lowercase alias with quoted column (e.g. {uc_ref.lower()}.\"COL\")"
        )

    # ── Check 4: Physical table names used as column prefixes ──────────
    # e.g. SALES_FACT.SCORE  (wrong — physical name, not alias)
    # Also detect via the rewrite map (catches case-insensitive variants
    # that Check 3 might miss, e.g. mixed-case ``Sales_Fact.col``)
    rewrite_map = build_alias_rewrite_map(dataset_aliases)
    for bad_prefix in rewrite_map:
        if bad_prefix in _schema_ignore or bad_prefix in _flagged_prefixes:
            continue
        # Search for bad_prefix followed by a dot and an unquoted identifier
        pattern = rf"\b{re.escape(bad_prefix)}\.(?!\")"
        if re.search(pattern, check_body, re.IGNORECASE):
            _flagged_prefixes.add(bad_prefix)
            correct = rewrite_map[bad_prefix] or "<alias>"
            errors.append(
                f"Invalid prefix '{bad_prefix}' used as column qualifier — "
                f"should be '{correct}' with quoted column "
                f'(e.g. {correct}."COL")'
            )

    # ── Check 5: Database or schema name used as column prefix ─────────
    if database:
        db_pat = rf"\b{re.escape(database)}\.(?!{re.escape(schema_name or '')})"
        if re.search(db_pat, check_body, re.IGNORECASE):
            errors.append(
                f"Database name '{database}' used as column qualifier — "
                f"use a table alias instead"
            )
    if schema_name:
        schema_pat = rf"\b{re.escape(schema_name)}\.[A-Za-z_]\w*(?!\.)"
        if re.search(schema_pat, check_body, re.IGNORECASE):
            errors.append(
                f"Schema name '{schema_name}' used as column qualifier — "
                f"use a table alias instead"
            )

    return SQLValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def generate_semantic_view_name(
    model_name: str,
    suffix: str = "_SEMANTIC",
    force_uppercase: bool = True
) -> str:
    """
    Generate a semantic view name from a model name.
    
    Ensures:
    1. Model name is properly sanitized (spaces → underscores, uppercase)
    2. Suffix is consistently formatted (uppercase, prefixed with _ if needed)
    3. Suffix is never applied twice
    
    Args:
        model_name: The logical model name (e.g., "Sales Model", "demo Table")
        suffix: The suffix to append (default: "_SEMANTIC")
        force_uppercase: Whether to force uppercase (default: True)
    
    Returns:
        Properly formatted semantic view name (e.g., "SALES_MODEL_SEMANTIC")
    
    Examples:
        >>> generate_semantic_view_name("demo Table")
        'DEMO_TABLE_SEMANTIC'
        >>> generate_semantic_view_name("Sales Model")
        'SALES_MODEL_SEMANTIC'
        >>> generate_semantic_view_name("Customer_Analytics")
        'CUSTOMER_ANALYTICS_SEMANTIC'
        >>> generate_semantic_view_name("SalesModel_SEMANTIC")
        'SALESMODEL_SEMANTIC'
    """
    if not model_name:
        return "MODEL_SEMANTIC"
    
    # Sanitize model name: spaces, dots, slashes → underscores
    clean = model_name.strip()
    for ch in (" ", ".", "/", "\\"):
        clean = clean.replace(ch, "_")
    
    # Remove leading/trailing underscores
    clean = clean.strip("_")
    
    if force_uppercase:
        clean = clean.upper()
    
    # Normalize suffix: ensure it's uppercase and starts with underscore
    suffix_upper = (suffix or "_SEMANTIC").strip().upper()
    if not suffix_upper.startswith("_"):
        suffix_upper = "_" + suffix_upper
    
    # Prevent duplicate suffixes
    if clean.upper().endswith(suffix_upper):
        return clean
    
    return clean + suffix_upper
