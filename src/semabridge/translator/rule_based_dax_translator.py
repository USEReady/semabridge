"""Rule-based DAX to SQL translator - No LLM required."""

import re
from typing import Any, Dict, List, Optional, Tuple
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class RuleBasedDAXTranslator:
    """Translate common DAX patterns to Snowflake SQL without LLM."""
    
    # Pattern: (regex, replacement_template, priority)
    RULES = [
        # Simple aggregations
        (r'SUM\s*\(\s*\[([^\]]+)\]\s*\)', r'SUM({alias}."\1")', 1),
        (r'AVERAGE\s*\(\s*\[([^\]]+)\]\s*\)', r'AVG({alias}."\1")', 1),
        (r'COUNTROWS\s*\(\s*([^)]+)\s*\)', r'COUNT(*)', 1),
        (r'DISTINCTCOUNT\s*\(\s*\[([^\]]+)\]\s*\)', r'COUNT(DISTINCT {alias}."\1")', 1),
        
        # IF statements
        (r'IF\s*\(\s*\[([^\]]+)\]\s*=\s*([^,]+),?\s*"([^"]+)",?\s*"([^"]+)"\s*\)',
         r'CASE WHEN {alias}."\1" = \2 THEN "\3" ELSE "\4" END', 2),
        
        # Nested IF (simplified)
        (r'IF\s*\(\s*\[([^\]]+)\]\s*=\s*([^,]+),?\s*"([^"]+)"\s*\)',
         r'CASE WHEN {alias}."\1" = \2 THEN "\3" END', 2),
        
        # Boolean comparisons
        (r'\[([^\]]+)\]\s*=\s*"([^"]+)"', r'{alias}."\1" = \'\2\'', 3),
        
        # Basic arithmetic
        (r'\[([^\]]+)\]\s*\+\s*\[([^\]]+)\]', r'{alias}."\1" + {alias}."\2"', 3),
        (r'\[([^\]]+)\]\s*-\s*\[([^\]]+)\]', r'{alias}."\1" - {alias}."\2"', 3),
        
        # CALCULATE with simple filter
        (r'CALCULATE\s*\(\s*SUM\s*\(\s*\[([^\]]+)\]\s*\),?\s*\[([^\]]+)\]\s*=\s*"([^"]+)"\s*\)',
         r'SUM(CASE WHEN {alias}."\2" = \'\3\' THEN {alias}."\1" END)', 2),
    ]
    
    def __init__(self, alias: str = "FACT"):
        self.alias = alias
    
    def translate(self, dax_expr: str) -> Optional[str]:
        """Translate DAX to SQL using rule-based patterns."""
        if not dax_expr:
            return None
        
        sql = dax_expr
        applied_rules = []
        
        # Sort rules by priority (lower number = higher priority)
        for pattern, template, priority in sorted(self.RULES, key=lambda x: x[2]):
            matches = re.findall(pattern, sql, re.IGNORECASE)
            if matches:
                sql = re.sub(pattern, template.format(alias=self.alias), sql, flags=re.IGNORECASE)
                applied_rules.append(pattern)
        
        if applied_rules:
            # Check if there are still common DAX features left that would make it invalid SQL
            unsupported_left = [f for f in ["CALCULATE(", "FILTER(", "ALL(", "RELATED(", "DIVIDE("] if f in sql.upper()]
            if unsupported_left or "[" in sql or "]" in sql:
                logger.debug(f"Rule-based applied but unsupported DAX left: {sql}")
                return None
                
            logger.info(f"✅ Rule-based translation applied: {applied_rules}")
            return sql
        
        return None
    
    def translate_indicator(self, indicator_expr: str, thresholds: Dict = None) -> Optional[str]:
        """Translate indicator DAX patterns (e.g., @Indicator01)."""
        if not indicator_expr:
            return None
        
        # Pattern: IF([@Indicator01]=1,"Low",IF([@Indicator01]=3,"High","Medium"))
        pattern = r'IF\s*\(\s*\[@?([^\]]+)\]\s*=\s*1,?\s*"([^"]+)",?\s*IF\s*\(\s*\[@?([^\]]+)\]\s*=\s*3,?\s*"([^"]+)",?\s*"([^"]+)"\s*\)\s*\)'
        
        match = re.match(pattern, indicator_expr, re.IGNORECASE)
        if match:
            indicator_name = match.group(1)
            low_label = match.group(2)
            high_label = match.group(4)
            medium_label = match.group(5)
            
            thresholds = thresholds or {'low': 1, 'medium': 2, 'high': 3}
            
            return f"""CASE 
    WHEN {self.alias}."{indicator_name}" = {thresholds['low']} THEN '{low_label}'
    WHEN {self.alias}."{indicator_name}" = {thresholds['high']} THEN '{high_label}'
    ELSE '{medium_label}'
END"""
        
        return None


class HybridTranslator:
    """Try rule-based first, fallback to LLM, then safe fallback."""
    
    def __init__(self, llm_translator=None):
        self.rule_based = RuleBasedDAXTranslator()
        self.llm_translator = llm_translator
    
    def translate(self, dax_expr: str, metric_name: str, alias: str = "FACT", metrics_context: List[Any] = None) -> Tuple[Optional[str], str]:
        """
        Translate DAX to SQL with fallback chain.
        Returns: (translated_sql, method_used)
        """
        self.rule_based.alias = alias
        
        # Level 1: Rule-based translation
        try:
            rule_sql = self.rule_based.translate(dax_expr)
            if rule_sql:
                return rule_sql, "rule_based"
        except Exception as e:
            logger.debug(f"Rule-based translation failed: {e}")
        
        # Level 2: LLM translation (if available)
        if self.llm_translator:
            try:
                # Some translators return a translation object, some return a string.
                # Assuming llm_translator has a translate method that returns SQL string or translation object.
                # In this system, dax_translator.translate returns an object with .sql property.
                llm_result = self.llm_translator.translate(dax_expr, alias, "", metric_name=metric_name, metrics_context=metrics_context)
                llm_sql = llm_result.sql if hasattr(llm_result, 'sql') else llm_result
                if llm_sql:
                    return llm_sql, "llm"
            except Exception as e:
                logger.debug(f"LLM translation failed: {e}")
        
        # Level 3: Safe fallback - CAST NULL
        logger.warning(f"Could not translate metric '{metric_name}', using safe fallback")
        return f"CAST(NULL AS DOUBLE) /* TODO: {metric_name} */", "fallback"
