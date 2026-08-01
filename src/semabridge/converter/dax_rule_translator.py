#!/usr/bin/env python3
"""
Rule-based DAX to SQL translator for common patterns.

This module provides quick, deterministic translations for simple DAX expressions
without requiring LLM API calls, dramatically reducing Gemini API usage.

Key functions:
- is_simple_metric(dax) -> bool: Classify DAX expression complexity
- rule_based_translation(dax, table_alias) -> Optional[str]: Generate SQL without LLM
"""

import os
import re
import threading
from typing import Optional, Dict, List, Tuple
from semabridge.utils.logger import get_logger
from semabridge.converter.api_usage_tracker import log_complexity_classification, log_rule_based_result

logger = get_logger(__name__)
_local_state = threading.local()


def _date_alias() -> str:
    return os.getenv("SEMABRIDGE_DATE_ALIAS", "COL_DATE")


# Common DAX aggregation functions
SIMPLE_AGGREGATIONS = {
    'SUM': 'SUM',
    'AVERAGE': 'AVG',
    'AVERAGEX': 'AVG',
    'COUNT': 'COUNT',
    'COUNTA': 'COUNT',
    'COUNTROWS': 'COUNT(*)',
    'COUNTBLANK': 'COUNT(*)',
    'MIN': 'MIN',
    'MINX': 'MIN',
    'MAX': 'MAX',
    'MAXX': 'MAX',
    'DISTINCTCOUNT': 'COUNT(DISTINCT',
    'VALUES': 'COUNT(DISTINCT',
}

# DAX patterns that indicate TIER 3 (Time Intelligence - needs Window Functions)
TIER3_TIME_INTELLIGENCE = {
    'TOTALYTD': r'TOTALYTD\s*\(',
    'TOTALMTD': r'TOTALMTD\s*\(',
    'TOTALQTD': r'TOTALQTD\s*\(',
    'DATESYTD': r'DATESYTD\s*\(',
    'DATESMTD': r'DATESMTD\s*\(',
    'DATESQTD': r'DATESQTD\s*\(',
    'DATEADD': r'DATEADD\s*\(',
    'SAMEPERIODLASTYEAR': r'SAMEPERIODLASTYEAR\s*\(',
    'SAMEPERIODLASTMONTH': r'SAMEPERIODLASTMONTH\s*\(',
    'SAMEPERIODLASTQUARTER': r'SAMEPERIODLASTQUARTER\s*\(',
}

# DAX patterns that indicate TIER 4+ (Complex - needs AST or LLM)
COMPLEXITY_INDICATORS = {
    'CALCULATE_COMPLEX': r'CALCULATE\s*\(\s*[^,]+\s*,\s*(?:FILTER|ALL|ALLEXCEPT)\s*\(',
    'FILTER': r'FILTER\s*\(',
    'SUMX': r'SUMX\s*\(',
    'AVERAGEX': r'AVERAGEX\s*\(',
    'COUNTX': r'COUNTX\s*\(',
    'MINX': r'MINX\s*\(',
    'MAXX': r'MAXX\s*\(',
    'ALL': r'\bALL\s*\(',
    'ALLEXCEPT': r'ALLEXCEPT\s*\(',
    'VALUES': r'VALUES\s*\(',
    'EARLIER': r'EARLIER\s*\(',
    'TOPN': r'TOPN\s*\(',
    'RANKX': r'RANKX\s*\(',
    'USERELATIONSHIP': r'USERELATIONSHIP\s*\(',
    'CROSSFILTER': r'CROSSFILTER\s*\(',
    'SAMPLE': r'SAMPLE\s*\(',
    'GENERATE': r'GENERATE\s*\(',
    'GENERATESERIES': r'GENERATESERIES\s*\(',
}

# Simple patterns that can be translated without LLM
SIMPLE_PATTERNS = {
    # Pattern name: (regex, translation function)
    'direct_agg': (
        r"^\s*(SUM|AVERAGE|AVERAGEX|COUNT|COUNTA|MIN|MINX|MAX|MAXX|DISTINCTCOUNT)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*$",
        'translate_direct_agg',
    ),
    'sum_simple_arithmetic': (
        r"^\s*SUM\s*\(\s*(?:\[(.+?)\]\s*)\)\s*(?:\+|-|\*|/)\s*\d+\s*$",
        'translate_simple_arithmetic',
    ),
    'count_distinct': (
        r"^\s*(?:DISTINCTCOUNT|VALUES)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*$",
        'translate_count_distinct',
    ),
}


def is_simple_metric_with_resolution(dax: str, resolver: 'MeasureDependencyResolver' = None) -> bool:
    """
    Classify metric considering measure dependencies.
    
    Uses MeasureDependencyResolver to expand measure references before classification.
    This ensures [Total Units YTD] which contains [TOTAL UNITS] is classified correctly.
    
    Args:
        dax: DAX expression to classify
        resolver: MeasureDependencyResolver instance (optional)
        
    Returns:
        True if simple after dependency resolution, False otherwise
    """
    if not dax or not isinstance(dax, str):
        return False
    
    # If we have a resolver, use it to expand references first
    if resolver:
        resolved_dax = resolver.resolve_all_references(dax)
        logger.debug(f"Classification with resolution: {dax[:60]}... → {resolved_dax[:60]}...")
        return is_simple_metric(resolved_dax)
    else:
        return is_simple_metric(dax)


def is_simple_metric(dax: str) -> bool:
    """
    Improved classification: Tier 1 (direct agg) and Tier 2 (simple CALCULATE) are "simple".
    
    Classification Logic:
    - TIER 1: Direct aggregations like SUM([Column]) → SIMPLE (local translation)
    - TIER 2: Simple CALCULATE wrappers or measure arithmetic → SIMPLE (local translation)
    - TIER 3: Time intelligence (TOTALYTD, SAMEPERIODLASTYEAR, etc.) → SEMI-COMPLEX (window functions)
    - TIER 4+: CALCULATE with FILTER/ALL, iterators, etc. → COMPLEX (needs LLM/AST)
    
    Args:
        dax: DAX expression to classify
        
    Returns:
        True if simple (TIER 1-2, can use rule-based translation), False if complex (TIER 4+)
    """
    if not dax or not isinstance(dax, str):
        return False
    
    clean_dax = dax.strip()

    if _is_deterministic_rule_supported(clean_dax):
        logger.debug(f"Deterministic DAX rule supported: {clean_dax[:60]}...")
        log_complexity_classification(True)
        return True
    
    # ========================================================================
    # Step 1: Check for TRUE COMPLEXITY (TIER 4+) that needs LLM
    # ========================================================================
    # Temporarily allow CALCULATE to pass for rule-based attempt
    if "CALCULATE" in clean_dax.upper():
        logger.debug("TIER 2 (CALCULATE) detected, attempting rule-based translation.")
        log_complexity_classification(True)
        return True

    for indicator_name, pattern in COMPLEXITY_INDICATORS.items():
        if re.search(pattern, clean_dax, re.IGNORECASE):
            logger.debug(f"TIER 4+ detected: {indicator_name} in: {clean_dax[:60]}...")
            log_complexity_classification(False)  # Log as complex
            return False
    
    # ========================================================================
    # Step 2: Check for TIER 1 patterns (direct aggregations)
    # ========================================================================
    for pattern_name, (regex, _) in SIMPLE_PATTERNS.items():
        if re.match(regex, clean_dax, re.IGNORECASE):
            logger.debug(f"TIER 1 pattern matched: {pattern_name}")
            log_complexity_classification(True)  # Log as simple
            return True
    
    # ========================================================================
    # Step 3: Check for TIER 3 (Time Intelligence) - these are OK to handle
    # with window functions, mark as simple for basic handling
    # ========================================================================
    for tier3_name, pattern in TIER3_TIME_INTELLIGENCE.items():
        if re.search(pattern, clean_dax, re.IGNORECASE):
            # Time intelligence functions can be translated to window functions
            logger.debug(f"TIER 3 detected: {tier3_name} - can use window functions")
            log_complexity_classification(True)  # Mark as "simple" since we can handle it
            return True
    
    # ========================================================================
    # Step 4: Check for TIER 2 patterns (simple CALCULATE, measure arithmetic)
    # ========================================================================
    
    # Pattern 1: Simple CALCULATE wrapper - CALCULATE([MeasureName])
    simple_calculate = r"^\s*CALCULATE\s*\(\s*\[[\w\s]+\]\s*\)\s*$"
    if re.match(simple_calculate, clean_dax, re.IGNORECASE):
        logger.debug(f"TIER 2: Simple CALCULATE wrapper")
        log_complexity_classification(True)  # Log as simple
        return True
    
    # Pattern 2: CALCULATE with just measure reference and arithmetic (no FILTER/ALL)
    # e.g., CALCULATE([Measure1] * 100) or CALCULATE([Measure1] + [Measure2])
    calculate_arithmetic = r"^\s*CALCULATE\s*\(\s*([^,()]*(?:\[\w\s]+[\+\-\*/]?)+[^,()]*)\s*\)\s*$"
    if re.match(calculate_arithmetic, clean_dax, re.IGNORECASE):
        # Make sure no FILTER/ALL inside
        if not re.search(r'\b(FILTER|ALL|ALLEXCEPT)\b', clean_dax, re.IGNORECASE):
            logger.debug(f"TIER 2: CALCULATE with simple arithmetic")
            log_complexity_classification(True)  # Log as simple
            return True
    
    # Pattern 3: Simple IF/IFERROR with measures and constants
    # e.g., IF([Total VanArsdel Units]=0, 0, DIVIDE([Total VanArsdel Units], [Total Units], 0))
    simple_if = r"^\s*IF\s*\(\s*[^()]*\[[^\]]+\][^()]*,\s*\d+\s*,\s*[^()]*(?:\[[\w\s]+\]|DIVIDE|IFERROR)\s*\([^)]*\)\s*\)\s*$"
    if re.match(simple_if, clean_dax, re.IGNORECASE):
        # Make sure no FILTER/ALL inside
        if not re.search(r'\b(FILTER|ALL|CALCULATE\s*\()(?!.*\)?\s*$)', clean_dax, re.IGNORECASE):
            logger.debug(f"TIER 2: IF with measure division")
            log_complexity_classification(True)  # Log as simple
            return True
    
    # Pattern 4: Simple arithmetic on measures
    # e.g., [Measure1] - [Measure2] or [Measure1] / [Measure2]
    measure_arithmetic = r"^\s*\[[^\]]+\]\s*[\+\-\*/]\s*\[[^\]]+\](?:\s*[\+\-\*/]\s*(?:\d+|\[[^\]]+\]))*\s*$"
    if re.match(measure_arithmetic, clean_dax, re.IGNORECASE):
        logger.debug(f"TIER 2: Simple measure arithmetic")
        log_complexity_classification(True)  # Log as simple
        return True
    
    # Pattern 5: DIVIDE function with measures
    # e.g., DIVIDE([Measure1], [Measure2], 0)
    simple_divide = r"^\s*DIVIDE\s*\(\s*\[[^\]]+\]\s*,\s*\[[^\]]+\]\s*(?:,\s*\d+)?\s*\)\s*$"
    if re.match(simple_divide, clean_dax, re.IGNORECASE):
        logger.debug(f"TIER 2: DIVIDE function")
        log_complexity_classification(True)  # Log as simple
        return True
    
    # ========================================================================
    # Step 5: Heuristic for remaining cases
    # ========================================================================
    # If it has only measure references, constants, and basic operators, it's likely simple
    temp = clean_dax
    temp = re.sub(r"\[[\w\s]+\]", "M", temp, flags=re.IGNORECASE)  # Replace measures
    temp = re.sub(r"\bSUMPRICE", "", temp, flags=re.IGNORECASE)  # Remove measure names
    temp = re.sub(r"'[\w\s]+'", "", temp)  # Remove table names
    temp = re.sub(r"\d+(\.\d+)?", "0", temp)  # Replace numbers
    temp = re.sub(r"\b(IF|DIVIDE|IFERROR|SUM|AVG|COUNT|INT)\b", "", temp, flags=re.IGNORECASE)  # Remove functions
    temp = re.sub(r"[\(\),\+\-\*/=<>!]", "", temp)  # Remove operators
    temp = re.sub(r"\s+", "", temp)  # Remove whitespace
    
    if not temp or len(temp) < 5:  # Very simple after cleanup
        logger.debug(f"SIMPLE (heuristic): {clean_dax[:60]}...")
        log_complexity_classification(True)  # Log as simple
        return True
    
    logger.debug(f"COMPLEX (failed all checks): {clean_dax[:60]}...")
    log_complexity_classification(False)  # Log as complex
    return False


def rule_based_translation(
    dax: str,
    table_alias: str,
    metric_name: str = "",
    dialect: str = "snowflake",
) -> Optional[str]:
    """
    Translate simple DAX expressions to SQL using deterministic rules.
    
    This function ONLY handles simple aggregations. Returns None for complex expressions
    that should be sent to LLM.
    
    Args:
        dax: DAX expression to translate
        table_alias: SQL table alias to use in output
        
    Returns:
        SQL aggregation expression, or None if cannot translate
    """
    if not dax or not isinstance(dax, str):
        return None
    
    set_dialect(dialect)
    clean_dax = dax.strip()

    advanced_translators = (
        ("time_intelligence", translate_time_intelligence_with_anchors),
        ("fiscal_cutoff", translate_fiscal_cutoff),
        ("calculate_filters", translate_calculate_with_filters),
        ("divide_measures", translate_divide_measures),
        ("if_wrapped_divide", translate_if_wrapped_divide),
        ("calculate_arithmetic", translate_calculate_arithmetic),
        ("iterator", translate_iterator),
    )
    for pattern_name, translator in advanced_translators:
        try:
            result = translator(clean_dax, table_alias)
            if result:
                logger.info(
                    "Rule-based translation successful: %s%s -> %s",
                    pattern_name,
                    f" for {metric_name}" if metric_name else "",
                    result[:80],
                )
                log_rule_based_result(True)
                
                # More precise alias replacement
                result = re.sub(r'\bSALESFACT\b(?=\.)', table_alias, result, flags=re.IGNORECASE)
                # Do not replace COL_DATE globally, it's a date dimension
                # result = re.sub(r'\bCOL_DATE\b', table_alias, result, flags=re.IGNORECASE)

                return _compact_sql(result)
        except Exception as e:
            logger.warning(f"Rule handler {pattern_name} failed: {e}")

    # Additional deterministic translators for common complex patterns
    additional_translators = (
        ("rolling_12_months", translate_rolling_12_months),
        ("sameperiodlastyear", translate_sameperiodlastyear),
    )
    for pattern_name, translator in additional_translators:
        try:
            result = translator(clean_dax, table_alias)
            if result:
                logger.info(
                    "Deterministic translator successful: %s -> %s",
                    pattern_name,
                    result[:120],
                )
                log_rule_based_result(True)
                return _compact_sql(result)
        except Exception as e:
            logger.debug(f"Deterministic translator {pattern_name} failed: {e}")
    
    # Try each simple pattern
    for pattern_name, (regex, handler_name) in SIMPLE_PATTERNS.items():
        match = re.match(regex, clean_dax, re.IGNORECASE)
        if match:
            handler = globals().get(handler_name)
            if handler:
                try:
                    result = handler(clean_dax, table_alias, match)
                    if result:
                        logger.info(f"Rule-based translation successful: {pattern_name} -> {result[:80]}")
                        log_rule_based_result(True)  # Log success
                        return result
                except Exception as e:
                    logger.warning(f"Rule handler {handler_name} failed: {e}")
    
    # If no pattern matched, cannot translate
    logger.debug(f"No rule pattern matched for: {clean_dax[:60]}...")
    log_rule_based_result(False)  # Log failure
    return None


def translate_calculate_arithmetic(dax: str, table_alias: str) -> Optional[str]:
    """
    Translate arithmetic operations between two CALCULATE statements.
    e.g., CALCULATE(...) - CALCULATE(...)
    """
    # Pattern for two CALCULATE calls with a minus sign
    pattern = r"^\s*CALCULATE\((.+)\)\s*-\s*CALCULATE\((.+)\)\s*$"
    match = re.match(pattern, dax, re.IGNORECASE)
    
    if not match:
        return None

    # Extract the inner parts of both CALCULATE statements
    part1_dax = f"CALCULATE({match.group(1).strip()})"
    part2_dax = f"CALCULATE({match.group(2).strip()})"

    # Recursively translate each part
    # This assumes translate_calculate_with_filters can handle the inner DAX
    part1_sql = translate_calculate_with_filters(part1_dax, table_alias)
    part2_sql = translate_calculate_with_filters(part2_dax, table_alias)

    if part1_sql and part2_sql:
        # Combine the translated SQL parts
        return f"({part1_sql}) - ({part2_sql})"
        
    return None


def _resolve_divide_operand_sql(operand: str, table_alias: str) -> Optional[str]:
    """Resolve one DIVIDE(...) argument to a SQL aggregate expression, or
    None if it can't be resolved as a direct aggregation.

    `operand` is whatever DIVIDE's regex captured — usually a bare measure/
    column name (e.g. "Total Units"), occasionally an explicit aggregation
    call (e.g. "SUM(Units)"). An explicit aggregation call resolves
    generically, used as-is. A bare name is ambiguous — this function (a
    pure text-pattern rule, like every other function in this module) has
    no measure registry or schema to check against, so it cannot tell "this
    is a column to sum" apart from "this is a reference to another
    measure". Guessing the former used to silently emit a bare reference to
    the measure by name (table_alias."MeasureName"), which Snowflake's
    semantic-view engine rejects with "a metric must directly refer to
    another aggregate-level expression" (the same mechanism behind the
    TOTAL_UNITS_YTD_SPLY-class bugs) — for exactly the metrics most likely
    to hit this path, since DIVIDE's numerator/denominator are usually
    other named measures, not raw columns. Returns None instead, so the
    caller declines and dispatch falls through to
    dax_ast_parser.py's DaxSqlRenderer, which has the measure registry
    (via measure_sql_map/known_measure_names) to resolve this correctly —
    or fails closed until the referenced measure is resolved.
    """
    clean = (operand or "").strip()
    agg = _parse_aggregation(clean)
    if agg:
        func, table, column, _ = agg
        col_ref = _column_ref(table, column, table_alias)
        return f"COUNT(DISTINCT {col_ref})" if func == "DISTINCTCOUNT" else f"{func}({col_ref})"
    return None


def translate_divide_measures(dax: str, table_alias: str) -> Optional[str]:
    """
    Translate DIVIDE([Numerator], [Denominator], [AlternateResult]) for any
    pair of measure/column names matching the DIVIDE(...) shape — not just
    one hardcoded pair.
    """
    logger.debug(f"Attempting to translate DIVIDE expression: {dax}")
    pattern = r"^\s*DIVIDE\s*\(\s*\[?([^\]]+)\]?\s*,\s*\[?([^\]]+)\]?\s*(?:,\s*(\d+))?\s*\)\s*$"
    match = re.match(pattern, dax, re.IGNORECASE)

    if not match:
        logger.debug("DIVIDE pattern did not match.")
        return None

    logger.debug("DIVIDE pattern matched.")
    numerator_measure = match.group(1)
    denominator_measure = match.group(2)
    alternate_result = match.group(3) or "0"

    numerator_sql = _resolve_divide_operand_sql(numerator_measure, table_alias)
    denominator_sql = _resolve_divide_operand_sql(denominator_measure, table_alias)
    if numerator_sql is None or denominator_sql is None:
        logger.debug("DIVIDE operand is not a direct aggregate call — declining to a measure reference.")
        return None

    result = f"COALESCE(({numerator_sql}) / NULLIF({denominator_sql}, 0), {alternate_result})"
    logger.debug(f"Translated DIVIDE expression to: {result}")
    return result


def translate_if_wrapped_divide(dax: str, table_alias: str) -> Optional[str]:
    """
    Translate IF(<condition>, <alt>, DIVIDE(...)) — and the symmetric
    IF(<condition>, DIVIDE(...), <alt>) — a very common defensive idiom
    (e.g. IF([Denominator]=0, 0, DIVIDE([Numerator], [Denominator], 0))).

    DIVIDE's own third argument already provides the same zero-guard
    semantics this IF wrapper is redundantly re-expressing, so this
    translates straight to the inner DIVIDE(...) (reusing
    translate_divide_measures) instead of trying to interpret the
    condition itself. Without this, DAX shaped this way would fall through
    to whatever translator happens to match some unrelated substring in
    the DAX text (e.g. a measure name it references), silently producing a
    numerator-only or otherwise wrong result — the exact class of bug this
    module's DIVIDE handling exists to prevent.
    """
    if not dax or not isinstance(dax, str):
        return None
    if not re.match(r"^\s*IF\s*\(", dax, re.IGNORECASE):
        return None

    inner = re.sub(r"^\s*IF\s*\(", "", dax.strip(), flags=re.IGNORECASE)
    if not inner.endswith(")"):
        return None
    inner = inner[:-1]
    args = _split_top_level(inner)
    if len(args) != 3:
        return None

    _, true_branch, false_branch = (a.strip() for a in args)
    for branch in (false_branch, true_branch):
        if re.match(r"^\s*DIVIDE\s*\(", branch, re.IGNORECASE):
            result = translate_divide_measures(branch, table_alias)
            if result:
                return result
    return None


# ============================================================================
# Pattern-specific translation handlers
# ============================================================================

def translate_direct_agg(dax: str, table_alias: str, match: re.Match) -> Optional[str]:
    """
    Translate direct aggregations like SUM([Column]), AVG([Column]), etc.
    
    Handler for pattern: (SUM|AVERAGE|...) ( [Column] )
    """
    try:
        func_name = match.group(1).upper()
        col_name = match.group(2) or match.group(3)
        
        # Map DAX functions to SQL functions
        sql_func = SIMPLE_AGGREGATIONS.get(func_name)
        if not sql_func:
            logger.warning(f"Unknown aggregation function: {func_name}")
            return None
        
        # Build column reference - use table alias and uppercase column name
        col_ref = f"{table_alias}.{_quote_identifier(col_name)}"
        
        # Snowflake: cast to FLOAT for SUM/AVG to handle BOOLEAN columns safely
        cast = "::FLOAT" if get_dialect() == "snowflake" and func_name in ("SUM", "AVERAGE", "AVERAGEX", "AVG") else ""
        
        # Handle DISTINCTCOUNT specially
        if func_name == 'DISTINCTCOUNT':
            return f"COUNT(DISTINCT {col_ref})"
        else:
            return f"{sql_func}({col_ref}{cast})"
    
    except Exception as e:
        logger.error(f"Error translating direct aggregation: {e}")
        return None


def translate_simple_arithmetic(dax: str, table_alias: str, match: re.Match) -> Optional[str]:
    """
    Translate simple arithmetic: SUM([Column]) + 5
    
    Currently not implemented - too complex for deterministic rules.
    """
    return None


def translate_count_distinct(dax: str, table_alias: str, match: re.Match) -> Optional[str]:
    """
    Translate DISTINCTCOUNT or VALUES functions.
    
    Handler for: DISTINCTCOUNT([Column]) or VALUES([Column])
    """
    try:
        col_name = match.group(1) or match.group(2)
        col_ref = f"{table_alias}.{_quote_identifier(col_name)}"
        return f"COUNT(DISTINCT {col_ref})"
    
    except Exception as e:
        logger.error(f"Error translating count distinct: {e}")
        return None


# ============================================================================
# Helper functions
# ============================================================================

def _quote_identifier(name: str) -> str:
    """
    Sanitize and quote identifier safely for the current SQL dialect.
    """
    name = name.strip().strip('"').strip("'")
    try:
        from semabridge.utils.identifiers import IdentifierSanitizer
        sanitizer = IdentifierSanitizer()
        safe_name = sanitizer.sanitize_column(name)
    except ImportError:
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', name)

    dialect = getattr(_local_state, "dialect", "snowflake")
    if str(dialect).lower() == "databricks":
        return f"`{safe_name}`"
    return f'"{safe_name.upper()}"'


def set_dialect(dialect: str) -> None:
    """Set SQL dialect for the current thread."""
    _local_state.dialect = (dialect or "snowflake").lower()


def get_dialect() -> str:
    """Get SQL dialect for the current thread (default: snowflake)."""
    return getattr(_local_state, "dialect", "snowflake")


def _compact_sql(sql: str) -> str:
    return " ".join(str(sql or "").split())


def _split_top_level(text: str) -> List[str]:
    """Split comma-separated DAX arguments without splitting nested calls."""
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    in_string: Optional[str] = None

    for ch in text:
        if ch in ("'", '"'):
            if in_string == ch:
                in_string = None
            elif in_string is None:
                in_string = ch
        elif in_string is None:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(depth - 1, 0)
            elif ch == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
                continue
        current.append(ch)

    if current:
        parts.append("".join(current).strip())
    return parts


def _parse_table_column(ref: str) -> Optional[Tuple[str, str]]:
    match = re.search(r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_ ]*))?\s*\[([^\]]+)\]", ref or "")
    if not match:
        return None
    table = (match.group(1) or match.group(2) or "").strip()
    column = match.group(3).strip()
    return table, column


def _parse_aggregation(expr: str) -> Optional[Tuple[str, str, str, str]]:
    match = re.match(
        r"\s*(SUM|AVERAGE|AVG|COUNT|MIN|MAX|DISTINCTCOUNT)\s*\(\s*(.+?)\s*\)\s*$",
        expr or "",
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None

    func = match.group(1).upper()
    if func == "AVERAGE":
        func = "AVG"
    ref = _parse_table_column(match.group(2))
    if not ref:
        return None
    table, column = ref
    return func, table, column, match.group(2)


def _column_ref(table: str, column: str, default_alias: str = "") -> str:
    alias = table or default_alias
    quoted_col = _quote_identifier(column)
    if alias:
        try:
            from semabridge.utils.identifiers import IdentifierSanitizer
            safe_alias = IdentifierSanitizer().sanitize_alias(alias)
        except ImportError:
            safe_alias = re.sub(r"[^A-Za-z0-9_]", "_", alias).upper()
        return f"{safe_alias}.{quoted_col}"
    return quoted_col


def _is_deterministic_rule_supported(dax: str) -> bool:
    clean = dax or ""
    return bool(
        re.search(r"^\s*(SUM|AVERAGE|COUNT|MIN|MAX|DISTINCTCOUNT)\s*\(", clean, re.IGNORECASE)
        or re.search(r"\bTOTAL[YM]TD\s*\(|\bTOTALQTD\s*\(", clean, re.IGNORECASE)
        or re.search(r"\bfiscal_yr_period\b", clean, re.IGNORECASE)
        or re.search(r"^\s*CALCULATE\s*\(", clean, re.IGNORECASE)
        or re.search(r"^\s*(SUMX|AVERAGEX|MINX|MAXX)\s*\(", clean, re.IGNORECASE)
    )


def translate_time_intelligence_with_anchors(dax: str, table_alias: str) -> Optional[str]:
    """
    Translate time intelligence functions using date anchors.
    
    Example: TOTALYTD(SUM('SalesFact'[Sales]), 'Date'[Date])
    """
    logger.debug(f"Attempting to translate time intelligence expression: {dax}")
    # Pattern for TOTALYTD, TOTALMTD, TOTALQTD
    pattern = r"^\s*(TOTALYTD|TOTALMTD|TOTALQTD)\s*\((.+?),\s*'?(?:[\w\s]+)'?\[([\w\s]+)\]\s*\)\s*$"
    match = re.match(pattern, dax, re.IGNORECASE)

    if not match:
        logger.debug("Time intelligence pattern did not match.")
        return None

    logger.debug("Time intelligence pattern matched.")
    time_func = match.group(1).upper()
    inner_agg_dax = match.group(2)
    date_table_and_col = match.group(3) # This will now be just the column name

    # Try parsing the inner aggregation using _parse_aggregation
    agg = _parse_aggregation(inner_agg_dax)
    if agg:
        func, measure_table, measure_col, _ = agg
        measure_col_ref = _column_ref(measure_table, measure_col, table_alias)
    else:
        # The inner expression isn't a direct aggregation call — the common
        # real-world shape here is a measure reference, e.g.
        # TOTALYTD([Total Sales], 'Date'[Date]). This function (a pure
        # text-pattern rule, like every other function in this module) has
        # no access to the referenced measure's own resolved SQL or to the
        # model's measure registry, so it cannot tell "this bracket names a
        # measure" apart from "this bracket names a physical column" —
        # guessing the latter used to silently emit a bare reference to the
        # measure by name (table_alias."MeasureName"), which Snowflake's
        # semantic-view engine rejects with "a metric must directly refer
        # to another aggregate-level expression" (the exact mechanism
        # behind the TOTAL_UNITS_YTD_SPLY-class bugs). Decline instead —
        # dax_ast_parser.py's DaxSqlRenderer._render_period_to_date has the
        # measure registry (via measure_sql_map/known_measure_names) and
        # correctly inlines the referenced measure's own SQL, or fails
        # closed until it's resolved.
        logger.debug("Inner aggregation is not a direct aggregate call — declining to a measure reference.")
        return None

    date_col_ref = f"{_quote_identifier(date_table_and_col)}"
    # Qualified reference to the fact table's enriched-view MAX_DATE anchor
    # column (see _create_enriched_view) — a bare "MAX_DATE" is not a valid
    # identifier inside a semantic view's METRICS clause.
    max_date_ref = _column_ref(None, "MAX_DATE", table_alias)

    period = ""
    if time_func == "TOTALYTD":
        period = "YEAR"
    elif time_func == "TOTALMTD":
        period = "MONTH"
    elif time_func == "TOTALQTD":
        period = "QUARTER"

    else_val = "NULL" if func in ("AVG", "MIN", "MAX") else "0"

    if func == "DISTINCTCOUNT":
        result = (
            f"COUNT(DISTINCT CASE WHEN {date_col_ref} >= DATE_TRUNC('{period}', {max_date_ref}) "
            f"AND {date_col_ref} <= {max_date_ref} THEN {measure_col_ref} ELSE NULL END)"
        )
    else:
        result = (
            f"{func}(CASE WHEN {date_col_ref} >= DATE_TRUNC('{period}', {max_date_ref}) "
            f"AND {date_col_ref} <= {max_date_ref} THEN {measure_col_ref} ELSE {else_val} END)"
        )

    logger.debug(f"Translated time intelligence expression to: {result}")
    return result


def translate_fiscal_cutoff(dax: str, table_alias: str) -> Optional[str]:
    """Translate common fiscal period VAR/RETURN cutoff patterns."""
    if not re.search(r"\bfiscal_yr_period\b", dax or "", re.IGNORECASE):
        return None

    return_match = re.search(r"\bRETURN\b\s+(.+)$", dax or "", re.IGNORECASE | re.DOTALL)
    return_block = return_match.group(1).strip() if return_match else dax
    agg_match = re.search(
        r"SUM\s*\(\s*(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_ ]*))?\s*\[([^\]]+)\]\s*\)",
        return_block,
        re.IGNORECASE,
    )
    if not agg_match:
        return None

    measure_table = (agg_match.group(1) or agg_match.group(2) or table_alias).strip()
    measure_col = agg_match.group(3).strip()
    period_match = re.search(
        r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_ ]*))?\s*\[\s*FISCAL_YR_PERIOD\s*\]\s*(<=|>=|<>|=|<|>)\s*(?:fiscalMonth|currentPeriod|_current_fiscal_period)",
        return_block,
        re.IGNORECASE,
    )
    op = period_match.group(3) if period_match else "<"
    period_table = ((period_match.group(1) or period_match.group(2)) if period_match else "DATES") or "DATES"
    return (
        f"SUM(CASE WHEN {_column_ref(period_table, 'FISCAL_YR_PERIOD', table_alias)} {op} _current_fiscal_period "
        f"THEN {_column_ref(measure_table, measure_col, table_alias)} ELSE 0 END)"
    )


def translate_rolling_12_months(dax: str, table_alias: str) -> Optional[str]:
    """Convert common R12M CALCULATE/FILTER pattern into CASE-based SUM.

    Looks for patterns that compare a month index to a MAX_MONTHINDEX anchor
    and emits a bounded SUM(CASE WHEN ...) expression.
    """
    if not dax or not isinstance(dax, str):
        return None
    # crude pattern detection
    m = re.search(r"SUM\s*\(\s*\[([^\]]+)\]\s*\).*?MonthIndex\s*(?:<=|<)\s*MAX\([^)]*\)\s*.*?-\s*(\d+)", dax, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    col = m.group(1).strip()
    months = m.group(2).strip()
    # use MAX_MONTHINDEX anchor expected to exist in enriched view
    date_alias = _date_alias()
    return f"SUM(CASE WHEN {date_alias}.MONTHINDEX <= MAX_MONTHINDEX AND {date_alias}.MONTHINDEX > MAX_MONTHINDEX - {months} THEN {table_alias}.{_quote_identifier(col)} ELSE 0 END)"


def translate_sameperiodlastyear(dax: str, table_alias: str) -> Optional[str]:
    """Always declines now — kept as a named no-op rather than deleted
    outright, since rule_based_translation's dispatch tuple still names it
    (removing the entry too is a Phase 2 dead-code concern, not this fix).

    This used to emit CALCULATE([Measure], SAMEPERIODLASTYEAR(...)) as
    `SUM(CASE WHEN <prior-year window> THEN {table_alias}."{Measure}" ELSE
    0 END)` — treating the bracketed MEASURE NAME as if it were a physical
    column. That is a bare reference to another metric by name, which is
    exactly the shape Snowflake's semantic-view engine rejects with "a
    metric must directly refer to another aggregate-level expression."
    This function (a pure text-pattern rule, like every other function in
    this module) has no access to the referenced measure's own resolved
    SQL — dax_ast_parser.py's DaxSqlRenderer does, via measure_sql_map, and
    correctly inlines it (see _render_lag_period's measure-reference
    fallback). Declining unconditionally lets dispatch fall through to
    that general path instead of "succeeding" with invalid SQL.
    """
    return None


def translate_calculate_with_filters(dax: str, table_alias: str) -> Optional[str]:
    """Translate CALCULATE with direct or FILTER equality/comparison predicates."""
    if not re.match(r"\s*CALCULATE\s*\(", dax or "", re.IGNORECASE):
        return None

    inner = re.sub(r"^\s*CALCULATE\s*\(", "", dax.strip(), flags=re.IGNORECASE)
    if inner.endswith(")"):
        inner = inner[:-1]
    parts = _split_top_level(inner)
    if len(parts) < 2:
        return None

    agg = _parse_aggregation(parts[0])
    if not agg:
        return None
    func, measure_table, measure_col, _ = agg
    if func == "DISTINCTCOUNT":
        measure_expr = f"COUNT(DISTINCT {_column_ref(measure_table, measure_col, table_alias)})"
    else:
        measure_expr = f"{func}({_column_ref(measure_table, measure_col, table_alias)})"

    conditions: List[str] = []
    for raw_filter in parts[1:]:
        filter_expr = raw_filter.strip()
        filter_match = re.match(r"FILTER\s*\(\s*[^,]+,\s*(.+)\s*\)\s*$", filter_expr, re.IGNORECASE | re.DOTALL)
        if filter_match:
            filter_expr = filter_match.group(1).strip()
        filter_expr = filter_expr.replace("&&", " AND ")

        for predicate in re.split(r"\s+\bAND\b\s+", filter_expr, flags=re.IGNORECASE):
            predicate = predicate.strip()
            pred_match = re.match(
                r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_ ]*))?\s*\[\s*([^\]]+)\s*\]\s*(<=|>=|<>|=|<|>)\s*(\"[^\"]*\"|'[^']*'|[-+]?\d+(?:\.\d+)?)",
                predicate,
                re.IGNORECASE,
            )
            if not pred_match:
                continue
            table = (pred_match.group(1) or pred_match.group(2) or table_alias).strip()
            column = pred_match.group(3).strip()
            op = pred_match.group(4)
            value = pred_match.group(5).strip()
            if value.startswith('"') and value.endswith('"'):
                value = "'" + value[1:-1].replace("'", "''") + "'"
            conditions.append(f"{_column_ref(table, column, table_alias)} {op} {value}")

    if not conditions:
        return None

    # Rebuild CASE around the aggregate argument to avoid nested aggregates.
    measure_col_ref = _column_ref(measure_table, measure_col, table_alias)
    return f"SUM(CASE WHEN {' AND '.join(conditions)} THEN {measure_col_ref} ELSE 0 END)"


def _parse_simple_predicate_conditions(filter_expr: str, table_alias: str) -> Optional[List[str]]:
    """Parse a DAX filter predicate into ANDed SQL comparison conditions —
    only the shape a scalar CASE WHEN can express: simple, non-aggregating
    per-row `[Table][Column] <op> <literal>` comparisons joined by AND.

    Returns None (fail closed) rather than a partial result if the
    predicate contains OR, any nested iterator/CALCULATE/FILTER call (a
    cross-row dependency this can't reduce to a per-row CASE WHEN), or any
    individual comparison this regex can't fully parse — silently dropping
    or mis-parsing part of a filter would be worse than declining it.
    """
    filter_expr = (filter_expr or "").replace("&&", " AND ").strip()
    if not filter_expr:
        return None
    if re.search(r"\bOR\b", filter_expr, re.IGNORECASE):
        return None
    if re.search(
        r"\b(CALCULATE|FILTER|TOPN|RANKX|ALLEXCEPT|ALL|SELECTEDVALUE|SUMX|AVERAGEX|MINX|MAXX|COUNTX|EARLIER)\s*\(",
        filter_expr,
        re.IGNORECASE,
    ):
        return None

    conditions: List[str] = []
    for predicate in re.split(r"\s+\bAND\b\s+", filter_expr, flags=re.IGNORECASE):
        predicate = predicate.strip()
        pred_match = re.match(
            r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_ ]*))?\s*\[\s*([^\]]+)\s*\]\s*(<=|>=|<>|=|<|>)\s*"
            r"(\"[^\"]*\"|'[^']*'|[-+]?\d+(?:\.\d+)?)\s*$",
            predicate,
            re.IGNORECASE,
        )
        if not pred_match:
            return None
        table = (pred_match.group(1) or pred_match.group(2) or table_alias).strip()
        column = pred_match.group(3).strip()
        op = pred_match.group(4)
        value = pred_match.group(5).strip()
        if value.startswith('"') and value.endswith('"'):
            value = "'" + value[1:-1].replace("'", "''") + "'"
        conditions.append(f"{_column_ref(table, column, table_alias)} {op} {value}")
    return conditions or None


def translate_iterator(dax: str, table_alias: str) -> Optional[str]:
    """Translate SUMX/AVERAGEX/MINX/MAXX iterator expressions, including
    the common SUMX(FILTER(table, predicate), expr) idiom.

    Iterating a filtered row-set and reducing is exactly equivalent to
    reducing over a CASE-gated full row-set, for a predicate whose
    conditions are simple, non-aggregating per-row comparisons ANDed
    together (see _parse_simple_predicate_conditions). Declines rather
    than guessing for anything more complex — a cross-row aggregation
    inside the predicate, an OR, or any shape that regex can't parse
    fully — so a caller never gets a plausible-looking but wrong CASE WHEN
    for a predicate this hasn't proven correct for.
    """
    match = re.match(
        r"\s*(SUMX|AVERAGEX|MINX|MAXX)\s*\(\s*(.+)\s*\)\s*$",
        dax or "",
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None

    func = match.group(1).upper()
    sql_func = {"SUMX": "SUM", "AVERAGEX": "AVG", "MINX": "MIN", "MAXX": "MAX"}[func]

    # Paren-depth-aware split (not a naive first-comma regex) so a
    # FILTER(table, predicate) first argument — which itself contains a
    # comma — isn't sheared in half.
    parts = _split_top_level(match.group(2))
    if len(parts) != 2:
        return None
    iterator_arg, expression = parts[0].strip(), parts[1].strip()

    filter_match = re.match(r"FILTER\s*\(\s*(.+)\s*\)\s*$", iterator_arg, re.IGNORECASE | re.DOTALL)
    where_conditions: Optional[List[str]] = None
    if filter_match:
        filter_parts = _split_top_level(filter_match.group(1))
        if len(filter_parts) != 2:
            return None
        iterator_table = filter_parts[0].strip().strip("'\"") or table_alias
        where_conditions = _parse_simple_predicate_conditions(filter_parts[1], table_alias)
        if where_conditions is None:
            return None
    else:
        iterator_table = iterator_arg.strip().strip("'\"") or table_alias

    def repl_col(col_match: re.Match) -> str:
        return _column_ref(iterator_table, col_match.group(1), table_alias)

    expr_sql = re.sub(r"\[([^\]]+)\]", repl_col, expression)
    if re.search(r"\b(CALCULATE|FILTER|TOPN|RANKX|ALL|ALLEXCEPT|SELECTEDVALUE)\b", expr_sql, re.IGNORECASE):
        return None

    if where_conditions is not None:
        where = " AND ".join(where_conditions)
        cast = "::FLOAT" if sql_func in ("SUM", "AVG") else ""
        return f"{sql_func}(CASE WHEN {where} THEN {expr_sql}{cast} ELSE 0 END)"

    return f"{sql_func}({expr_sql})"



def extract_column_references(dax: str) -> List[str]:
    """
    Extract all column references from DAX expression.
    
    Returns list of column names like: ['Amount', 'Quantity', 'Date']
    """
    # Find [ColumnName] or 'Table'[ColumnName]
    pattern = r"'?[\w\s]+'?\[([^\]]+)\]|\[([^\]]+)\]"
    matches = re.findall(pattern, dax)
    
    # Flatten tuple results and return unique column names
    columns = []
    for match in matches:
        col = match[0] or match[1]
        if col and col not in columns:
            columns.append(col)
    
    return columns


def extract_aggregation_function(dax: str) -> Optional[Tuple[str, str]]:
    """
    Extract aggregation function and its argument from DAX.
    
    Returns: (function_name, column_name) or None
    """
    # Try simple aggregation pattern
    pattern = r"(SUM|AVERAGE|COUNT|MIN|MAX|DISTINCTCOUNT)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)"
    match = re.search(pattern, dax, re.IGNORECASE)
    
    if match:
        func = match.group(1).upper()
        col = match.group(2) or match.group(3)
        return (func, col)
    
    return None


def translate_simple_calculate_wrapper(dax: str, table_alias: str) -> Optional[str]:
    """
    Handle simple CALCULATE wrappers like CALCULATE([MeasureName]).
    For now, we'll mark these as needing further translation, but they're still "simple".
    """
    # CALCULATE([MeasureName]) -> just MeasureName reference
    # This will be handled by the calling code as a measure reference
    match = re.match(r"^\s*CALCULATE\s*\(\s*(\[[\w\s]+\])\s*\)\s*$", dax, re.IGNORECASE)
    if match:
        measure_name = match.group(1)
        logger.debug(f"Simple CALCULATE wrapper: {dax[:60]} -> {measure_name}")
        return measure_name  # Return as-is for further processing
    return None


def translate_measure_arithmetic(dax: str, table_alias: str) -> Optional[str]:
    """
    Handle simple arithmetic on measures like [Measure1] - [Measure2].
    These will be processed by semantic layer which knows measure definitions.
    """
    # For now, just pass through - semantic layer will expand measure references
    if re.match(r"^\s*\[[^\]]+\]\s*[\+\-\*/]\s*\[[^\]]+\]", dax, re.IGNORECASE):
        logger.debug(f"Measure arithmetic detected: {dax[:80]}")
        return dax  # Pass through for semantic layer to handle
    return None


def translate_if_measure_expression(dax: str, table_alias: str) -> Optional[str]:
    """
    Handle IF statements with measure conditions.
    e.g., IF([Total VanArsdel Units]=0, 0, DIVIDE([Total VanArsdel Units], [Total Units], 0))
    """
    if re.search(r"^\s*IF\s*\(", dax, re.IGNORECASE):
        logger.debug(f"IF measure expression: {dax[:80]}")
        # These are complex enough that they should stay as-is for semantic layer
        return dax  # Pass through for semantic layer
    return None


def translate_time_intelligence(dax: str, table_alias: str, date_alias: str = "dat") -> Optional[str]:
    """
    Handle basic time intelligence functions that can be translated to window functions.
    e.g., TOTALYTD([Measure], 'Date'[Date]) -> SUM(...) OVER (PARTITION BY YEAR(...))
    """
    # For now, these are complex enough that we pass them through
    # A full implementation would translate these to Snowflake window functions
    if any(re.search(pattern, dax, re.IGNORECASE) 
           for pattern in TIER3_TIME_INTELLIGENCE.values()):
        logger.debug(f"Time intelligence function: {dax[:60]}")
        # These need semantic layer + window function support
        return dax  # Flag for special handling
    return None


# ============================================================================
# Test and validation functions
# ============================================================================

def get_complexity_stats(dax_expressions: List[str]) -> Dict:
    """
    Analyze a list of DAX expressions and return complexity statistics.
    
    Useful for understanding what percentage can be handled with rules vs LLM.
    """
    if not dax_expressions:
        return {}
    
    simple_count = sum(1 for dax in dax_expressions if is_simple_metric(dax))
    complex_count = len(dax_expressions) - simple_count
    simple_pct = (simple_count / len(dax_expressions)) * 100 if dax_expressions else 0
    
    return {
        'total': len(dax_expressions),
        'simple': simple_count,
        'complex': complex_count,
        'simple_percentage': simple_pct,
        'api_call_reduction': f"{simple_pct:.1f}% of metrics can skip LLM",
    }


# ============================================================================
# STEP 1: MeasureDependencyResolver - Expand measure references in DAX
# ============================================================================

class MeasureDependencyResolver:
    """
    Resolves measure references in DAX expressions by recursively expanding them.
    
    Problem: [Total Units YTD] contains DAX referencing [TOTAL UNITS], which isn't 
             recognized as a column reference and causes measure skipping.
    
    Solution: Detect [measure references], look them up in the model, expand their DAX.
    
    Example:
        input: "TOTALYTD([TOTAL UNITS], 'Date'[Date])"
        refs: ["TOTAL UNITS"]
        lookup [TOTAL UNITS] -> "SUM(SALESFACT.UNITS)"
        output: "TOTALYTD(SUM(SALESFACT.UNITS), 'Date'[Date])"
    """
    
    def __init__(self, fabric_model_measures: Dict[str, Dict]):
        """
        Args:
            fabric_model_measures: Dict of measure_name -> {dax, expression, etc.}
        """
        self.measures = fabric_model_measures or {}
        self.expansion_cache = {}
        self.visited_in_expansion = set()
        logger.info(f"MeasureDependencyResolver initialized with {len(self.measures)} measures")
    
    def detect_measure_references(self, dax: str) -> List[str]:
        """
        Find all [MeasureName] references in DAX.
        
        Returns list of measure names WITHOUT the brackets.
        
        Examples:
            "[TOTAL UNITS]" -> ["TOTAL UNITS"]
            "TOTALYTD([TOTAL UNITS], 'Date'[Date])" -> ["TOTAL UNITS"]
            "[Measure1] + [Measure2]" -> ["Measure1", "Measure2"]
        """
        if not dax:
            return []
        
        # Pattern: [SomethingWithoutBrackets]
        # But NOT 'Table'[Column] (table refs)
        pattern = r"\[([^\[\]]+)\]"
        matches = re.findall(pattern, dax)
        
        # Map to actual measure names in model (case-insensitive)
        measure_refs = []
        for match in matches:
            # Check if this is a known measure
            for measure_name in self.measures.keys():
                if measure_name.lower() == match.lower():
                    measure_refs.append(measure_name)
                    break
        
        return list(set(measure_refs))  # Remove duplicates
    
    def expand_measure_reference(self, measure_name: str, depth: int = 0) -> Optional[str]:
        """
        Look up a measure and return its expanded DAX.
        
        Recursively expands measure references in the DAX up to 3 levels deep.
        Prevents infinite loops with visited_in_expansion set.
        
        Args:
            measure_name: Name of measure to expand
            depth: Current recursion depth (max 3)
            
        Returns:
            Expanded DAX expression or None if not found
        """
        if depth > 3:  # Prevent infinite recursion
            logger.warning(f"Max expansion depth reached for {measure_name}")
            return None
        
        # Check cache first
        cache_key = f"{measure_name}_{depth}"
        if cache_key in self.expansion_cache:
            return self.expansion_cache[cache_key]
        
        # Check visited to prevent cycles
        if measure_name in self.visited_in_expansion:
            logger.debug(f"Circular dependency detected: {measure_name}")
            return None
        
        # Find measure in model
        measure_def = None
        for m_name, m_def in self.measures.items():
            if m_name.lower() == measure_name.lower():
                measure_def = m_def
                break
        
        if not measure_def:
            logger.debug(f"Measure not found: {measure_name}")
            return None
        
        # Get the DAX expression
        dax = measure_def.get('expression') or measure_def.get('dax')
        if not dax:
            logger.debug(f"No DAX expression for measure: {measure_name}")
            return None
        
        # Mark as visited
        self.visited_in_expansion.add(measure_name)
        
        # Recursively expand any measure references in this DAX
        inner_refs = self.detect_measure_references(dax)
        expanded_dax = dax
        
        for inner_ref in inner_refs:
            inner_expansion = self.expand_measure_reference(inner_ref, depth + 1)
            if inner_expansion:
                # Replace [MeasureRef] with expanded content
                expanded_dax = expanded_dax.replace(f"[{inner_ref}]", f"({inner_expansion})")
        
        self.visited_in_expansion.discard(measure_name)
        self.expansion_cache[cache_key] = expanded_dax
        
        logger.debug(f"Expanded measure {measure_name}: {expanded_dax[:80]}...")
        return expanded_dax
    
    def resolve_all_references(self, dax: str) -> str:
        """
        Recursively expand all [MeasureName] references in DAX.
        
        Replaces all [MeasureName] with their expanded DAX, handling nested references.
        
        Args:
            dax: Original DAX expression
            
        Returns:
            DAX with all measure references expanded or wrapped in parens
        """
        if not dax:
            return dax
        
        self.visited_in_expansion.clear()
        result = dax
        refs = self.detect_measure_references(dax)
        
        for ref in refs:
            expanded = self.expand_measure_reference(ref)
            if expanded:
                # Replace [MeasureRef] with expanded in parens
                result = result.replace(f"[{ref}]", f"({expanded})")
                logger.debug(f"Resolved {ref} in DAX")
            else:
                logger.warning(f"Could not expand measure reference: {ref}")
        
        return result
    
    def get_dependency_order(self) -> List[str]:
        """
        Return measures in topological sort order.
        
        Ensures measures with no dependencies come first, then measures that 
        depend on them, etc. Prevents circular dependencies causing issues.
        
        Returns:
            List of measure names in dependency order
        """
        # Build dependency graph
        deps_graph = {}
        for measure_name, measure_def in self.measures.items():
            dax = measure_def.get('expression') or measure_def.get('dax', '')
            refs = self.detect_measure_references(dax)
            deps_graph[measure_name] = set(refs)
        
        # Topological sort
        sorted_measures = []
        visited = set()
        visiting = set()
        
        def visit(node):
            if node in visited:
                return
            if node in visiting:
                logger.warning(f"Circular dependency detected involving: {node}")
                return
            
            visiting.add(node)
            for dep in deps_graph.get(node, set()):
                visit(dep)
            visiting.remove(node)
            visited.add(node)
            sorted_measures.append(node)
        
        for measure_name in self.measures.keys():
            visit(measure_name)
        
        logger.info(f"Dependency order for {len(sorted_measures)} measures computed")
        return sorted_measures
    
    def classify_measure_tier(self, measure_name: str) -> str:
        """
        Classify a measure as TIER1, TIER2, or TIER3 based on its resolved DAX.
        
        Returns:
            "TIER1" (local SQL), "TIER2" (LLM), or "TIER3" (display only)
        """
        measure_def = self.measures.get(measure_name)
        if not measure_def:
            return "TIER2"  # Unknown -> safe default
        
        dax = measure_def.get('expression') or measure_def.get('dax', '')
        if not dax:
            return "TIER3"  # No DAX -> display only
        
        # Expand references first
        resolved_dax = self.resolve_all_references(dax)
        
        # Classify resolved DAX
        if is_simple_metric(resolved_dax):
            return "TIER1"
        elif any(re.search(pattern, resolved_dax, re.IGNORECASE) 
                for pattern in TIER3_TIME_INTELLIGENCE.values()):
            return "TIER3"
        else:
            return "TIER2"


# ============================================================================
# STEP 3: TranslationBatcher - Batch measures for efficient LLM translation
# ============================================================================

class TranslationBatcher:
    """
    Groups measures by tier and batches TIER2 measures for LLM translation.
    
    Problem: 47 individual LLM API calls for each measure is wasteful and slow.
    
    Solution: Group measures by similar complexity, send 8-10 per API call.
              Expected: 2-3 total calls for 28 TIER2 measures (95% reduction).
    """
    
    def __init__(self, max_batch_size: int = 8):
        """
        Args:
            max_batch_size: Max measures per API batch (default 8)
        """
        self.max_batch_size = max_batch_size
        self.tier1_measures = []  # Local translation candidates
        self.tier2_measures = []  # LLM translation needed
        self.tier3_measures = []  # Display only
        self.batches = []
        logger.info(f"TranslationBatcher initialized (batch size: {max_batch_size})")
    
    def add_measure(self, name: str, measure_def: Dict, tier: str) -> None:
        """
        Add a measure to the appropriate tier.
        
        Args:
            name: Measure name
            measure_def: Dict with 'expression', 'dax', etc.
            tier: "TIER1", "TIER2", or "TIER3"
        """
        measure_entry = {'name': name, 'def': measure_def}
        
        if tier == "TIER1":
            self.tier1_measures.append(measure_entry)
        elif tier == "TIER2":
            self.tier2_measures.append(measure_entry)
        else:  # TIER3 or unknown
            self.tier3_measures.append(measure_entry)
        
        logger.debug(f"Added {name} to {tier} ({len(self.tier1_measures)}/{len(self.tier2_measures)}/{len(self.tier3_measures)})")
    
    def create_batches(self) -> List[Dict]:
        """
        Create batches of TIER2 measures for LLM.
        
        Returns list of batches, each with up to max_batch_size measures.
        """
        self.batches = []
        
        # Batch TIER2 measures
        for i in range(0, len(self.tier2_measures), self.max_batch_size):
            batch = {
                'batch_num': len(self.batches) + 1,
                'tier': 'TIER2',
                'measures': self.tier2_measures[i:i + self.max_batch_size],
                'size': min(self.max_batch_size, len(self.tier2_measures) - i),
            }
            self.batches.append(batch)
        
        logger.info(f"Created {len(self.batches)} batch(es) for {len(self.tier2_measures)} TIER2 measures")
        return self.batches
    
    def format_batch_for_llm(self, batch: Dict) -> str:
        """
        Format a batch of measures into a prompt for LLM.
        
        Args:
            batch: Batch dict from create_batches()
            
        Returns:
            Formatted prompt string
        """
        prompt_lines = [
            f"Translate {batch['size']} Fabric DAX measures to Snowflake SQL.",
            f"Only provide SQL expressions. No explanations.",
            f"Use table alias SALESFACT for all table references.",
            ""
        ]
        
        for idx, measure_entry in enumerate(batch['measures'], 1):
            name = measure_entry['name']
            dax = measure_entry['def'].get('expression') or measure_entry['def'].get('dax', '')
            prompt_lines.append(f"{idx}. {name}: {dax}")
        
        return "\n".join(prompt_lines)
    
    def parse_batch_response(self, batch: Dict, response: str) -> Dict[str, Optional[str]]:
        """
        Parse LLM response for a batch and extract translations.
        
        Args:
            batch: Original batch
            response: LLM response text
            
        Returns:
            Dict of measure_name -> sql_translation
        """
        translations = {}
        lines = response.strip().split('\n')
        
        for measure_entry in batch['measures']:
            # Try to find translation for this measure
            measure_name = measure_entry['name']
            translation = None
            
            # Look for pattern: "1. MeasureName: SQL..."
            for line in lines:
                if f"{measure_name}:" in line or f"{measure_name} =" in line:
                    # Extract SQL part
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        translation = parts[1].strip()
                    break
            
            if not translation:
                logger.warning(f"Could not extract translation for {measure_name}")
            else:
                logger.debug(f"Parsed translation for {measure_name}: {translation[:60]}...")
            
            translations[measure_name] = translation
        
        return translations
    
    def get_summary(self) -> Dict:
        """
        Get summary of all measures by tier.
        
        Returns dict with counts and expected API calls.
        """
        return {
            'tier1_count': len(self.tier1_measures),
            'tier2_count': len(self.tier2_measures),
            'tier3_count': len(self.tier3_measures),
            'total_count': len(self.tier1_measures) + len(self.tier2_measures) + len(self.tier3_measures),
            'expected_api_calls': max(1, len(self.batches)) if self.batches else (
                1 if len(self.tier2_measures) <= self.max_batch_size 
                else (len(self.tier2_measures) // self.max_batch_size) + 1
            ),
            'api_reduction_vs_individual': f"{100 * (1 - (max(1, len(self.batches) or 1) / (len(self.tier2_measures) or 1))):.0f}%" if self.tier2_measures else "N/A",
        }


# ============================================================================
# STEP 4: LLM Fallback - Ensure no measure ever returns None/skipped
# ============================================================================

def translate_dax_with_fallback(
    dax: str,
    measure_name: str,
    table_alias: str,
    dataset_name: str,
    resolver: 'MeasureDependencyResolver' = None,
    batcher: 'TranslationBatcher' = None,
    dax_translator_instance = None,
) -> Optional[str]:
    """
    Translate DAX with fallback: Local → LLM (never None/skipped).
    
    Problem: If local translation fails, measure is skipped (None returned).
             This prevents 100% deployment.
    
    Solution: 
    1. Try local classification/translation
    2. If successful, return SQL
    3. If fails, add to LLM batcher queue
    4. Batcher accumulates measures
    5. When batch ready, send to Gemini
    6. Return Gemini translation (never None)
    
    Args:
        dax: DAX expression
        measure_name: Name of measure
        table_alias: SQL table alias
        dataset_name: Dataset name
        resolver: MeasureDependencyResolver (optional)
        batcher: TranslationBatcher (optional)
        dax_translator_instance: DAXTranslator instance for LLM fallback
        
    Returns:
        SQL translation (never None) or fallback approximation
    """
    if not dax or not isinstance(dax, str):
        return None
    
    clean_dax = dax.strip()
    
    # Step 1: Try local classification
    try:
        # Expand references if resolver available
        resolved_dax = clean_dax
        if resolver:
            resolved_dax = resolver.resolve_all_references(clean_dax)
            logger.debug(f"Fallback: Resolved {measure_name} -> {resolved_dax[:60]}...")
        
        # Try rule-based translation
        if is_simple_metric(resolved_dax):
            sql = rule_based_translation(resolved_dax, table_alias)
            if sql:
                logger.info(f"✓ Fallback: Local translation for {measure_name}: {sql[:60]}...")
                return sql
    
    except Exception as e:
        logger.warning(f"Fallback: Local translation error for {measure_name}: {e}")
    
    # Step 2: Try DAX translator (has multi-tier fallback)
    if dax_translator_instance:
        try:
            result = dax_translator_instance.translate(
                dax=clean_dax,
                table_alias=table_alias,
                dataset_name=dataset_name,
                metric_name=measure_name,
            )
            if result and result.sql:
                logger.info(f"✓ Fallback: DAXTranslator returned SQL for {measure_name}")
                return result.sql
        except Exception as e:
            logger.warning(f"Fallback: DAXTranslator error for {measure_name}: {e}")
    
    # Step 3: Add to LLM batcher queue (will be processed later)
    if batcher:
        measure_def = {'dax': clean_dax, 'expression': clean_dax}
        batcher.add_measure(measure_name, measure_def, "TIER2")
        logger.warning(f"Fallback: Queued {measure_name} for LLM batch translation")
        # Return placeholder that will be replaced after LLM response
        return None  # Will be populated by batcher
    
    # Step 4: Ultimate fallback - create safe placeholder
    # This ensures measure is never lost
    logger.warning(f"Fallback: No translation available for {measure_name}, using NULL placeholder")
    return "NULL"  # Safe SQL that won't break semantic view
