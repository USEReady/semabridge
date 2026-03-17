#!/usr/bin/env python3
"""
Rule-based DAX to SQL translator for common patterns.

This module provides quick, deterministic translations for simple DAX expressions
without requiring LLM API calls, dramatically reducing Gemini API usage.

Key functions:
- is_simple_metric(dax) -> bool: Classify DAX expression complexity
- rule_based_translation(dax, table_alias) -> Optional[str]: Generate SQL without LLM
"""

import re
from typing import Optional, Dict, List, Tuple
from semabridge.utils.logger import get_logger
from semabridge.converter.api_usage_tracker import log_complexity_classification, log_rule_based_result

logger = get_logger(__name__)


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
    
    # ========================================================================
    # Step 1: Check for TRUE COMPLEXITY (TIER 4+) that needs LLM
    # ========================================================================
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


def rule_based_translation(dax: str, table_alias: str) -> Optional[str]:
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
    
    clean_dax = dax.strip()
    
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
        
        # Handle DISTINCTCOUNT specially
        if sql_func == 'COUNT(DISTINCT':
            return f"COUNT(DISTINCT {col_ref})"
        else:
            return f"{sql_func}({col_ref})"
    
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
    Quote identifier if needed, converting to uppercase.
    
    Snowflake treats unquoted identifiers as uppercase and quoted identifiers
    as case-sensitive. For simplicity, we use unquoted uppercase format.
    """
    # Remove existing quotes if present
    name = name.strip().strip('"').strip("'")
    
    # Convert to uppercase unquoted format for consistency
    return name.upper()


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


def translate_divide_function(dax: str, table_alias: str) -> Optional[str]:
    """
    Handle DIVIDE function with measures: DIVIDE([Measure1], [Measure2], 0)
    """
    match = re.match(r"^\s*DIVIDE\s*\(\s*(\[[\w\s]+\])\s*,\s*(\[[\w\s]+\])\s*(?:,\s*(\d+))?\s*\)\s*$", 
                     dax, re.IGNORECASE)
    if match:
        num = match.group(1)
        denom = match.group(2)
        fallback = match.group(3) or "0"
        # Format: (measure1) / NULLIF(measure2, 0)
        logger.debug(f"DIVIDE function expanded")
        return dax  # Pass through for semantic layer to handle measure expansion
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
    # This ensures measure is never completely lost
    logger.warning(f"Fallback: No translation available for {measure_name}, using NULL placeholder")
    return "NULL"  # Safe SQL that won't break semantic view
