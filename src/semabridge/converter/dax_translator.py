"""
DAX to SQL Translator.

Implements the "Tiered Safety" translation strategy to convert Fabric DAX expressions
into Snowflake-compatible SQL for Semantic Views.
"""

import re
from typing import Optional, Tuple, Dict, List, Any

from semabridge.utils.logger import get_logger
from semabridge.utils.naming import sanitize_column, to_alias

logger = get_logger(__name__)


class DAXTranslationResult:
    """Result of a DAX translation attempt."""
    
    def __init__(self, sql: Optional[str], tier: int, original_dax: str):
        self.sql = sql
        self.tier = tier  # 0=Override, 1=Direct, 2=Branching/Arith, 3=Opaque (Failed)
        self.original_dax = original_dax
        self.is_success = sql is not None


class DAXTranslator:
    """
    Translates DAX expressions to SQL.
    
    Tier 0: Manual Overrides & Recursion
    Tier 1: Direct Aggregations (SUM, AVG, MIN, MAX, COUNT, DISTINCTCOUNT)
    Tier 2: Arithmetic & Branching (A + B, A / B, DIVIDE)
    Tier 3: Time Intelligence (TOTALYTD, TOTALMTD, TOTALQTD) - Window functions
    Tier 4: Complex (CALCULATE with filters, iterators) - Requires manual override
    """
    
    # Regex patterns for Tier 1
    # Matches: FUNC('Table'[Column]) or FUNC([Column])
    _TIER1_PATTERN = re.compile(
        r"^\s*(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*$",
        re.IGNORECASE
    )
    
    # Simple Arithmetic Patterns
    # Matches: [Measure1] + [Measure2] 
    # Matches: [Measure1] - [Measure2]
    # Matches: [Measure1] * [Measure2]
    # Matches: [Measure1] / [Measure2]
    # Very basic parser - assumes simple structure
    _ARITHMETIC_PATTERN = re.compile(
        r"^\s*(\[.+?\])\s*([\+\-\*\/])\s*(\[.+?\])\s*$",
        re.IGNORECASE
    )
    
    # DIVIDE functionality
    _DIVIDE_PATTERN = re.compile(
        r"^\s*DIVIDE\s*\(\s*(\[.+?\])\s*,\s*(\[.+?\])\s*(?:,.+?)?\)\s*$",
        re.IGNORECASE
    )
    
    # Time Intelligence patterns that CAN be translated to Snowflake window functions
    TIME_INTEL_PATTERNS = {
        # TOTALYTD(SUM('Table'[Column]), 'Date'[Date])
        "YTD": re.compile(
            r"TOTALYTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
        "MTD": re.compile(
            r"TOTALMTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
        "QTD": re.compile(
            r"TOTALQTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
    }
    
    # Time Intelligence functions that require Date dimension context
    TIME_INTEL_FUNCTIONS = [
        "TOTALYTD", "TOTALMTD", "TOTALQTD", 
        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER",
        "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD",
        "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR"
    ]
    
    # Patterns that CANNOT be safely translated (require DAX engine evaluation)
    UNSUPPORTED_PATTERNS = [
        r"CALCULATE\s*\([^)]+,\s*FILTER\s*\(",          # CALCULATE with FILTER
        r"SUMX\s*\(\s*FILTER\s*\(",                      # SUMX over filtered table
        r"EARLIER\s*\(",                                 # Row context reference
        r"RANKX\s*\(",                                   # Ranking (requires full context)
        r"USERELATIONSHIP\s*\(",                         # Dynamic relationship
        r"CROSSFILTER\s*\(",                             # Cross filter modification
        r"ALL\s*\([^)]*\)\s*\)",                         # ALL alone is complex
        r"ALLEXCEPT\s*\(",                               # Removes filters except specified
        r"VALUES\s*\([^)]+\)\s*\)",                      # Context-dependent values
    ]
    
    def translate(self, 
                  dax: str, 
                  table_alias: str, 
                  dataset_name: str, 
                  overrides: Dict[str, str] = None,
                  metric_name: str = None,
                  metrics_context: List[Any] = None) -> DAXTranslationResult:
        """
        Translate a DAX expression to SQL.
        
        Args:
            dax: The DAX formula string
            table_alias: SQL alias for the main table (e.g. 'sales')
            dataset_name: Name of the dataset for context
            overrides: Dictionary of metric_name -> manual_sql
            metric_name: Name of the current metric being translated
            metrics_context: List of SMLMetric objects to resolve dependencies
        """
        if not dax:
            return DAXTranslationResult(None, 3, "")
        
        clean_dax = dax.strip()
        overrides = overrides or {}
        
        # Tier 0: Manual Overrides
        # Priority 1: Check if THIS metric has an override
        if metric_name and metric_name in overrides:
            logger.info(f"Using manual SQL override for metric '{metric_name}'")
            return DAXTranslationResult(overrides[metric_name], 0, clean_dax)
            
        # Priority 2: Check if exact DAX string matches an override key (rare but possible)
        if clean_dax in overrides:
            return DAXTranslationResult(overrides[clean_dax], 0, clean_dax)
        
        # Tier 1: Direct Aggregations
        tier1_sql = self._try_tier1(clean_dax, table_alias)
        if tier1_sql:
            return DAXTranslationResult(tier1_sql, 1, clean_dax)
        
        # Tier 2: Branching & Arithmetic
        # e.g. [Net Sales] = [Gross Sales] - [Discounts]
        if metrics_context:
            tier2_sql = self._try_branching(clean_dax, metrics_context, overrides)
            if tier2_sql:
                return DAXTranslationResult(tier2_sql, 2, clean_dax)
        
        # Tier 3: Time Intelligence (TOTALYTD, TOTALMTD, TOTALQTD, SAMEPERIODLASTYEAR, etc.)
        # Uses the deterministic AST parser + Snowflake window function renderer.
        is_time_intel = any(func.upper() in dax.upper() for func in self.TIME_INTEL_FUNCTIONS)
        if is_time_intel:
            from semabridge.converter.dax_ast_parser import try_ast_translate
            resolved_measures = {}
            if metrics_context:
                for m in metrics_context:
                    if m.sql_expression:
                        resolved_measures[m.unique_name] = m.sql_expression
            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                measure_sql_map=resolved_measures,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 3, clean_dax)

        # Tier 4: Complex CALCULATE / FILTER / ALL / ALLEXCEPT — attempt AST translation
        is_complex = any(
            re.search(pattern, clean_dax, re.IGNORECASE)
            for pattern in self.UNSUPPORTED_PATTERNS
        )
        if is_complex:
            from semabridge.converter.dax_ast_parser import try_ast_translate
            resolved_measures = {}
            if metrics_context:
                for m in metrics_context:
                    if m.sql_expression:
                        resolved_measures[m.unique_name] = m.sql_expression
            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                measure_sql_map=resolved_measures,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 4, clean_dax)

        # Tier 5: LLM Fallback - Use Claude for complex expressions deterministic parsing couldn't handle
        # Only attempt if LLM is available and enabled
        llm_result = self._try_llm_fallback(
            clean_dax, 
            table_alias,
            dataset_name,
            metric_name
        )
        if llm_result:
            return llm_result

        # No translation possible — return None (all tiers exhausted)
        return DAXTranslationResult(None, 4, clean_dax)
    
    def _try_llm_fallback(self,
                         dax: str,
                         table_alias: str,
                         dataset_name: str,
                         metric_name: Optional[str] = None) -> Optional[DAXTranslationResult]:
        """
        Attempt Tier 5 LLM translation for complex expressions.
        
        Uses Google Gemini as a fallback when deterministic parsing fails.
        Returns None if LLM is not available or declines to translate.
        """
        try:
            from semabridge.converter.gemini_dax_translator import get_gemini_translator
            
            translator = get_gemini_translator()
            if not translator.api_key:
                return None
            
            # Attempt LLM translation
            llm_result = translator.translate(
                dax=dax,
                table_alias=table_alias,
                dataset_name=dataset_name,
                metric_name=metric_name
            )
            
            # Only use LLM result if:
            # 1. Translation succeeded (is_valid=True)
            # 2. Confidence is acceptable (>= 0.55)
            if llm_result.is_valid and llm_result.sql and llm_result.confidence >= 0.55:
                logger.info(
                    f"LLM translation accepted for '{metric_name}' "
                    f"(confidence: {llm_result.confidence:.2f}, tier: 5)"
                )
                return DAXTranslationResult(llm_result.sql, 5, dax)
            elif llm_result.sql and llm_result.confidence > 0.4:
                # Low confidence - log but don't use
                logger.warning(
                    f"LLM translation low confidence for '{metric_name}': "
                    f"confidence={llm_result.confidence:.2f}. Falling back to None. "
                    f"SQL was: {llm_result.sql[:100]}..."
                )
                return None
            else:
                logger.debug(
                    f"LLM translation declined for '{metric_name}': "
                    f"confidence={llm_result.confidence:.2f}. Error: {llm_result.error}"
                )
                return None
                
        except ImportError:
            logger.debug("LLM translator module not available")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error in LLM fallback: {str(e)}")
            return None
    
    def _try_tier1(self, dax: str, table_alias: str) -> Optional[str]:
        """Attempt Tier 1 translation."""
        match = self._TIER1_PATTERN.match(dax)
        if not match:
            return None
        
        func = match.group(1).upper()
        # Group 2 has Table[Column] format col name, Group 3 has [Column] format
        col_name = match.group(2) or match.group(3)
        
        # Map DAX function to SQL function
        func_map = {
            "SUM": "SUM",
            "AVERAGE": "AVG",
            "MIN": "MIN",
            "MAX": "MAX",
            "COUNT": "COUNT",
            "DISTINCTCOUNT": "COUNT(DISTINCT {col})"
        }
        
        col_ref = f"{table_alias}.{self._quote(col_name)}"
        sql_template = func_map.get(func)
        
        if not sql_template:
            return None
            
        if "{col}" in sql_template:
            return sql_template.format(col=col_ref)
        else:
            return f"{sql_template}({col_ref})"
    
    def _try_branching(self, dax: str, metrics: List[Any], overrides: Dict[str, str]) -> Optional[str]:
        """
        Attempt to resolve references to other measures.
        Handles simple cases:
        1. Single measure ref: [Measure]
        2. Simple arithmetic: [A] + [B]
        3. DIVIDE: DIVIDE([A], [B])
        """
        
        # Helper to resolve a single [MeasureName]
        def resolve_measure(ref_str: str) -> Optional[str]:
            name = ref_str.strip('[]')
            
            # Check override first
            if name in overrides:
                return overrides[name]
                
            # Find metric in context
            for m in metrics:
                if m.unique_name == name:
                    # If the referenced metric has SQL, use it
                    # Note: We rely on the fact that simple metrics were processed first 
                    # or that we can get their simple translation
                    if m.sql_expression:
                        return m.sql_expression
                    elif m.expression:
                        # Try to translate it on-the-fly (limited depth recursion)
                        # We don't have table aliases here easily, so this is risky if it's Tier 1
                        pass
            return None

        # Case 1: DIVIDE([A], [B])
        div_match = self._DIVIDE_PATTERN.match(dax)
        if div_match:
            num_ref = div_match.group(1)
            den_ref = div_match.group(2)
            
            num_sql = resolve_measure(num_ref)
            den_sql = resolve_measure(den_ref)
            
            if num_sql and den_sql:
                # Safe division in Snowflake
                return f"DIV0({num_sql}, {den_sql})"
        
        # Case 2: Arithmetic [A] op [B]
        arith_match = self._ARITHMETIC_PATTERN.match(dax)
        if arith_match:
            left_ref = arith_match.group(1)
            op = arith_match.group(2)
            right_ref = arith_match.group(3)
            
            left_sql = resolve_measure(left_ref)
            right_sql = resolve_measure(right_ref)
            
            if left_sql and right_sql:
                return f"({left_sql} {op} {right_sql})"
                
        # Case 3: Recursion for simple branching [Measure]
        # Regex to find all [Measure] tokens
        # This is a general replacement strategy for expressions like [A] - [B] + [C]
        # But we need to be careful not to replace Table[Col] logic if mixed
        
        # Only attempt this if it looks like a purely measure-based expression
        # i.e., doesn't contain CALCULATE, SUM, etc.
        if "CALCULATE" not in dax.upper() and "(" not in dax:
             # Find all [Tags]
             measure_refs = re.findall(r"\[([^\]]+)\]", dax)
             if not measure_refs:
                 return None
                 
             current_sql = dax
             resolved_all = True
             
             for ref in measure_refs:
                 sql = resolve_measure(f"[{ref}]")
                 if not sql:
                     resolved_all = False
                     break
                 # Replace [Name] with (SQL)
                 current_sql = current_sql.replace(f"[{ref}]", f"({sql})")
                 
             if resolved_all:
                 return current_sql
                 
        return None
    
    def _quote(self, identifier: str) -> str:
        """Quote SQL identifier, replacing spaces and illegal chars."""
        clean_id = sanitize_column(identifier, force_uppercase=True)
        return f'"{clean_id}"'
    
    def analyze_complexity(self, dax: str) -> dict:
        """
        Analyze DAX expression complexity and return metadata for sync decisions.
        
        Returns:
            dict with keys:
                - tier: int (1-4)
                - requires_time_intel: bool
                - group_by_dimensions: list[str]
                - depends_on_measures: list[str]
                - sync_enabled: bool
                - failure_reason: Optional[str]
        """
        if not dax:
            return {
                "tier": 0,
                "requires_time_intel": False,
                "group_by_dimensions": [],
                "depends_on_measures": [],
                "sync_enabled": False,
                "failure_reason": "Empty expression"
            }
        
        clean_dax = dax.strip()
        upper_dax = clean_dax.upper()
        
        result = {
            "tier": 1,
            "requires_time_intel": False,
            "group_by_dimensions": [],
            "depends_on_measures": [],
            "sync_enabled": True,
            "failure_reason": None
        }
        
        # Extract measure dependencies [MeasureName]
        measure_refs = re.findall(r"\[([^\]]+)\]", clean_dax)
        # Filter out column refs (those from 'Table'[Column] patterns)
        table_col_pattern = re.findall(r"'[\w\s]+'\[([^\]]+)\]", clean_dax)
        pure_measure_refs = [m for m in measure_refs if m not in table_col_pattern]
        result["depends_on_measures"] = list(set(pure_measure_refs))
        
        # Check for unsupported patterns first (Tier 4)
        for pattern in self.UNSUPPORTED_PATTERNS:
            if re.search(pattern, upper_dax, re.IGNORECASE):
                result["tier"] = 4
                result["sync_enabled"] = False
                result["failure_reason"] = f"Unsupported DAX pattern detected"
                return result
        
        # Check for Time Intelligence functions (Tier 3)
        for func in self.TIME_INTEL_FUNCTIONS:
            if func in upper_dax:
                result["tier"] = 3
                result["requires_time_intel"] = True
                # Time Intelligence requires Date dimension for proper evaluation
                result["group_by_dimensions"] = ["'Calendar'[Date]"]
                
                # Check if we can translate this specific pattern
                can_translate = False
                for period_type, pattern in self.TIME_INTEL_PATTERNS.items():
                    if pattern.search(clean_dax):
                        can_translate = True
                        break
                
                if not can_translate:
                    # Complex Time Intelligence we can't translate
                    result["sync_enabled"] = False
                    result["failure_reason"] = f"Complex Time Intelligence ({func}) requires manual override"
                break
        
        # Check for simple CALCULATE without complex filters (still Tier 3)
        if "CALCULATE" in upper_dax and result["tier"] < 3:
            result["tier"] = 3
            # Simple CALCULATE might still be translatable if it's just wrapping an aggregation
            if not re.search(r"CALCULATE\s*\([^)]+,\s*\w+\s*\(", upper_dax):
                # CALCULATE with simple filter - might work
                pass
            else:
                result["sync_enabled"] = False
                result["failure_reason"] = "CALCULATE with complex filter requires manual override"
        
        # Check for arithmetic/branching (Tier 2)
        if result["tier"] == 1 and result["depends_on_measures"]:
            result["tier"] = 2
        
        return result
    
    def try_tier3_time_intel(
        self, 
        dax: str, 
        table_alias: str, 
        date_alias: str = "CALENDAR"
    ) -> Optional[str]:
        """
        Attempt Tier 3 Time Intelligence translation using Snowflake window functions.
        
        Converts DAX Time Intelligence to SQL window functions:
        - TOTALYTD -> Cumulative sum partitioned by Year
        - TOTALMTD -> Cumulative sum partitioned by Year, Month
        - TOTALQTD -> Cumulative sum partitioned by Year, Quarter
        
        Args:
            dax: The DAX expression
            table_alias: SQL alias for the measure's source table
            date_alias: SQL alias for the Date dimension table
            
        Returns:
            SQL expression or None if translation not possible
        """
        for period_type, pattern in self.TIME_INTEL_PATTERNS.items():
            match = pattern.search(dax)
            if match:
                agg_func = match.group(1).upper()
                col_name = match.group(2) or match.group(3)
                # Groups 4 and 5 are the date table and column
                
                col_ref = f"{table_alias}.{self._quote(col_name)}"
                sql_agg = {
                    "SUM": "SUM", 
                    "AVERAGE": "AVG", 
                    "COUNT": "COUNT",
                    "MIN": "MIN",
                    "MAX": "MAX"
                }.get(agg_func, "SUM")
                
                # Generate Snowflake window function for period-to-date
                if period_type == "YTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
                elif period_type == "MTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR", {date_alias}."MONTH"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
                elif period_type == "QTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR", {date_alias}."QUARTER"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
        
        return None
    
    def get_required_dimensions(self, dax: str) -> list[str]:
        """
        Extract dimension columns that should be included in GROUP BY for proper measure evaluation.
        
        Analyzes the DAX expression to determine what dimensions are needed for
        context-dependent calculations.
        
        Returns:
            List of dimension column references (e.g., ["'Date'[Year]", "'Region'[Name]"])
        """
        dimensions = []
        upper_dax = dax.upper() if dax else ""
        
        # Time Intelligence always needs date context
        for func in self.TIME_INTEL_FUNCTIONS:
            if func in upper_dax:
                dimensions.append("'Calendar'[Date]")
                break
        
        # Extract explicit table[column] references that might indicate required dimensions
        # Pattern: 'TableName'[ColumnName] 
        table_col_refs = re.findall(r"'([\w\s]+)'\[(\w+)\]", dax or "")
        for table, col in table_col_refs:
            dim_ref = f"'{table}'[{col}]"
            if dim_ref not in dimensions:
                # Only add if it looks like a dimension (not a measure column)
                col_upper = col.upper()
                if not any(m in col_upper for m in ["AMOUNT", "SALES", "REVENUE", "PRICE", "COST", "QTY"]):
                    dimensions.append(dim_ref)
        
        return dimensions

