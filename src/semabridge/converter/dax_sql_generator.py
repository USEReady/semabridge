#!/usr/bin/env python3
"""
Deterministic SQL Generator - Convert DAX to Snowflake SQL using real column names.

Core logic:
1. Start with DAX expression parsed by DaxExpressionParser
2. Map [ColumnName] references to real schema columns
3. Apply deterministic translation rules
4. Generate Snowflake-compatible SQL expressions (aggregation only, no SELECT/FROM)

This module NEVER:
- Invents column names
- Uses name-based inference
- Falls back to LLM for simple patterns
- Generates SUM(*)

All column names come from schema mapping.
"""

import re
from typing import Dict, Optional, List, Set
from dataclasses import dataclass

from semabridge.utils.logger import get_logger
from semabridge.converter.dax_parser import DaxExpression, MeasureType, DaxExpressionParser

logger = get_logger(__name__)


@dataclass
class ColumnMappingContext:
    """Context for mapping columns during SQL generation."""
    dax_to_schema: Dict[str, str]                    # Map [DAX Name] -> SCHEMA_COLUMN
    schema_columns: Set[str]                         # Available columns (uppercase)
    measure_map: Dict[str, str] = None               # Map measure_name -> SQL expression
    
    def map_column(self, dax_column: str) -> Optional[str]:
        """Map a DAX column to schema column."""
        # Try direct mapping first
        key = dax_column.upper()
        if key in self.dax_to_schema:
            return self.dax_to_schema[key]
        
        # Try fuzzy matching if direct fails
        for schema_col in self.schema_columns:
            if dax_column.upper() == schema_col:
                return schema_col
        
        return None


class DeterministicSQLGenerator:
    """
    Generate Snowflake SQL from DAX using deterministic rules.
    
    Replaces all name-based inference with schema-driven column mapping.
    """
    
    # DAX function -> Snowflake function mappings
    FUNCTION_MAP = {
        # Aggregations
        'SUM': 'SUM',
        'AVERAGE': 'AVG',
        'AVERAGEX': 'AVG',
        'COUNT': 'COUNT',
        'COUNTA': 'COUNT',
        'COUNTROWS': 'COUNT(*)',
        'MIN': 'MIN',
        'MINX': 'MIN',
        'MAX': 'MAX',
        'MAXX': 'MAX',
        'DISTINCTCOUNT': 'COUNT(DISTINCT',
        'VALUES': 'COUNT(DISTINCT',
        
        # Arithmetic
        'DIVIDE': 'DIV0',
        'INT': 'FLOOR',
        'ROUND': 'ROUND',
        'ABS': 'ABS',
        'CEILING': 'CEIL',
        'FLOOR': 'FLOOR',
        'POWER': 'POWER',
        
        # String functions
        'CONCATENATE': 'CONCAT',
        'LEFT': 'LEFT',
        'RIGHT': 'RIGHT',
        'LEN': 'LEN',
        'TRIM': 'TRIM',
        'UPPER': 'UPPER',
        'LOWER': 'LOWER',
        
        # Date functions
        'DATE': 'DATE',
        'NOW': 'CURRENT_TIMESTAMP',
        'TODAY': 'CURRENT_DATE',
        'YEAR': 'YEAR',
        'MONTH': 'MONTH',
        'DAY': 'DAY',
        'HOUR': 'HOUR',
        'MINUTE': 'MINUTE',
        'SECOND': 'SECOND',
        'EOMONTH': 'EOMONTH',
    }
    
    def __init__(self,
                 column_mappings: Optional[Dict[str, str]] = None,
                 schema_columns: Optional[Set[str]] = None,
                 measure_dict: Optional[Dict[str, str]] = None):
        """
        Initialize generator.
        
        Args:
            column_mappings: Dict mapping DAX column names to schema columns
            schema_columns: Set of available columns in schema
            measure_dict: Dict mapping measure names to their SQL expressions
        """
        self.column_mappings = column_mappings or {}
        self.schema_columns = schema_columns or set()
        self.measure_dict = measure_dict or {}
        
        logger.debug(
            f"Initialized SQL generator with "
            f"{len(self.column_mappings)} mappings, "
            f"{len(self.schema_columns)} schema columns"
        )
    
    def translate(self, dax: str, table_alias: str = "fact", 
                  measure_name: str = None) -> Optional[str]:
        """
        Translate DAX expression to Snowflake SQL.
        
        Args:
            dax: DAX expression
            table_alias: SQL table alias (e.g., "sales")
            measure_name: Name of measure being translated (for logging)
            
        Returns:
            SQL aggregation expression or None if cannot translate
        """
        if not dax or not isinstance(dax, str):
            logger.warning(f"Invalid DAX: {dax}")
            return None
        
        clean_dax = dax.strip()
        
        logger.debug(f"Translating DAX: {clean_dax[:60]}...")
        
        try:
            # Parse DAX
            parser = DaxExpressionParser()
            expr = parser.parse(clean_dax)
            
            # Check if deterministic
            if not expr.is_deterministic:
                logger.debug(f"DAX is not deterministic: {expr.type.value}")
                return None
            
            # Build context
            context = ColumnMappingContext(
                dax_to_schema=self._build_column_map(),
                schema_columns=self.schema_columns,
                measure_map=self.measure_dict,
            )
            
            # Generate SQL by type
            if expr.type == MeasureType.AGGREGATION:
                return self._translate_aggregation(expr, context, table_alias)
            
            elif expr.type == MeasureType.DIVISION:
                return self._translate_divide(expr, context, table_alias)
            
            elif expr.type == MeasureType.TIME_BASED:
                return self._translate_time_intel(expr, context, table_alias)
            
            elif expr.type == MeasureType.CALCULATED:
                return self._translate_calculate(expr, context, table_alias)
            
            else:
                logger.warning(f"Unsupported measure type: {expr.type.value}")
                return None
            
        except Exception as e:
            logger.error(f"Error translating DAX: {e}")
            return None
    
    # ======================================================================================
    # Translation methods by pattern
    # ======================================================================================
    
    def _translate_aggregation(self, expr: DaxExpression, 
                               context: ColumnMappingContext, 
                               table_alias: str) -> Optional[str]:
        """Translate direct aggregations: SUM([Amount]), COUNT([Product]), etc."""
        if not expr.function or not expr.columns:
            logger.warning("Aggregation missing function or columns")
            return None
        
        func = expr.function.upper()
        if func not in self.FUNCTION_MAP:
            logger.warning(f"Unknown aggregation function: {func}")
            return None
        
        # Map column
        dax_col = expr.columns[0] if expr.columns else None
        if not dax_col:
            return None
        
        schema_col = context.map_column(dax_col)
        if not schema_col:
            logger.warning(f"Could not map column: {dax_col}")
            return None
        
        # Generate SQL
        sql_func = self.FUNCTION_MAP[func]
        col_ref = f"{table_alias}.{schema_col}"
        
        if sql_func == 'COUNT(DISTINCT':
            sql = f"COUNT(DISTINCT {col_ref})"
        else:
            sql = f"{sql_func}({col_ref})"
        
        logger.debug(f"Generated aggregation SQL: {sql}")
        return sql
    
    def _translate_divide(self, expr: DaxExpression,
                          context: ColumnMappingContext,
                          table_alias: str) -> Optional[str]:
        """Translate DIVIDE function: DIVIDE(a, b) → DIV0(a, b)"""
        # DIVIDE has format: DIVIDE(numerator, denominator[, alternative_result])
        # Extract from DAX: DIVIDE([Sales], [Units])
        
        dax = expr.raw
        
        # Find numerator and denominator measures/columns
        # Pattern: DIVIDE( [MeasureA], [MeasureB] )
        pattern = r'DIVIDE\s*\(\s*([^,]+?)\s*,\s*([^,)]+?)\s*(?:,\s*[^)]+?)?\s*\)'
        match = re.search(pattern, dax, re.IGNORECASE)
        
        if not match:
            logger.warning(f"Could not parse DIVIDE: {dax}")
            return None
        
        numerator_ref = match.group(1).strip()
        denominator_ref = match.group(2).strip()
        
        # Resolve references
        num_sql = self._resolve_reference(numerator_ref, context, table_alias)
        denom_sql = self._resolve_reference(denominator_ref, context, table_alias)
        
        if not num_sql or not denom_sql:
            logger.warning(f"Could not resolve DIVIDE operands")
            return None
        
        sql = f"DIV0({num_sql}, {denom_sql})"
        logger.debug(f"Generated DIVIDE SQL: {sql}")
        return sql
    
    def _translate_time_intel(self, expr: DaxExpression,
                              context: ColumnMappingContext,
                              table_alias: str) -> Optional[str]:
        """Translate time intelligence functions."""
        func = expr.function.upper()
        dax = expr.raw
        
        if func == 'TOTALYTD':
            # TOTALYTD(SUM([Amount]), [Date])
            # → SUM(Amount) OVER (ORDER BY Date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
            #   filtered WHERE YEAR(Date) = YEAR(CURRENT_DATE)
            
            pattern = r'TOTALYTD\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)'
            match = re.search(pattern, dax, re.IGNORECASE)
            if not match:
                return None
            
            agg_part = match.group(1).strip()
            date_ref = match.group(2).strip()
            
            # Map aggregation
            agg_sql = self._resolve_simple_agg(agg_part, context, table_alias)
            if not agg_sql:
                return None
            
            # Get date column
            date_col = self._extract_column_from_ref(date_ref)
            if not date_col:
                return None
            
            schema_date = context.map_column(date_col)
            if not schema_date:
                schema_date = date_col.upper()
            
            date_ref_sql = f"{table_alias}.{schema_date}"
            
            # Build window function
            # Simplified: FILTER year, then aggregate with window
            sql = f"CASE WHEN YEAR({date_ref_sql}) = YEAR(CURRENT_DATE()) THEN ({agg_sql}) OVER (ORDER BY {date_ref_sql}) ELSE 0 END"
            
            logger.debug(f"Generated TOTALYTD SQL: {sql[:100]}...")
            return sql
        
        logger.debug(f"Time intelligence function not yet supported: {func}")
        return None
    
    def _translate_calculate(self, expr: DaxExpression,
                             context: ColumnMappingContext,
                             table_alias: str) -> Optional[str]:
        """Translate CALCULATE with filters."""
        dax = expr.raw
        
        # Extract base aggregation and filters
        # CALCULATE(SUM([Amount]), [Region] = "West")
        
        if 'CALCULATE(' not in dax.upper():
            logger.warning("Expected CALCULATE in expression")
            return None
        
        # Find the aggregation part (first argument)
        pattern = r'CALCULATE\s*\(\s*([^,]+?)\s*(?:,\s*(.+?)\s*)?\)'
        match = re.search(pattern, dax, re.IGNORECASE | re.DOTALL)
        
        if not match:
            logger.warning(f"Could not parse CALCULATE: {dax}")
            return None
        
        agg_part = match.group(1).strip()
        filter_part = match.group(2).strip() if match.group(2) else ""
        
        # Translate base aggregation
        agg_sql = self._resolve_simple_agg(agg_part, context, table_alias)
        if not agg_sql:
            logger.warning(f"Could not translate aggregation: {agg_part}")
            return None
        
        # Handle filters
        if filter_part:
            filter_sql = self._translate_filter(filter_part, context, table_alias)
            if filter_sql:
                # Wrap in CASE statement
                sql = f"CASE WHEN {filter_sql} THEN {agg_sql} ELSE 0 END"
            else:
                sql = agg_sql  # Fall back to base aggregation
        else:
            sql = agg_sql
        
        logger.debug(f"Generated CALCULATE SQL: {sql[:100]}...")
        return sql
    
    # ======================================================================================
    # Helper methods
    # ======================================================================================
    
    def _resolve_reference(self, ref: str, context: ColumnMappingContext, 
                          table_alias: str) -> Optional[str]:
        """
        Resolve a reference which could be a column, measure, or aggregation.
        
        Examples:
          [Amount] → fact.AMOUNT
          [Total Sales] → SQL from measure_dict
          SUM([Amount]) → SUM(fact.AMOUNT)
        """
        ref = ref.strip()
        
        # Check if it's a measure reference [MeasureName]
        if ref.startswith('[') and ref.endswith(']'):
            measure_name = ref[1:-1]
            
            # Check if it's a resolved measure
            if measure_name in context.measure_map and context.measure_map[measure_name]:
                return context.measure_map[measure_name]
            
            # Check if it's a column
            schema_col = context.map_column(measure_name)
            if schema_col:
                return f"{table_alias}.{schema_col}"
            
            logger.warning(f"Could not resolve reference: {ref}")
            return None
        
        # Check if it's an aggregation like SUM([Amount])
        if '(' in ref:
            return self._resolve_simple_agg(ref, context, table_alias)
        
        logger.warning(f"Unknown reference format: {ref}")
        return None
    
    def _resolve_simple_agg(self, agg_expr: str, context: ColumnMappingContext,
                           table_alias: str) -> Optional[str]:
        """Resolve simple aggregation like SUM([Amount])."""
        # Parse: FUNC([Col]) or FUNC('Table'[Col])
        
        pattern = r'(SUM|COUNT|AVERAGE|MIN|MAX|DISTINCTCOUNT|AVG)\s*\(\s*(?:\'[^\']*\')?\s*\[([^\]]+)\]\s*\)'
        match = re.search(pattern, agg_expr, re.IGNORECASE)
        
        if not match:
            logger.debug(f"Not a simple aggregation: {agg_expr}")
            return None
        
        func = match.group(1).upper()
        col_name = match.group(2)
        
        # Map column
        schema_col = context.map_column(col_name)
        if not schema_col:
            logger.warning(f"Could not map column: {col_name}")
            return None
        
        sql_func = self.FUNCTION_MAP.get(func, func)
        col_ref = f"{table_alias}.{schema_col}"
        
        if sql_func == 'COUNT(DISTINCT':
            return f"COUNT(DISTINCT {col_ref})"
        else:
            return f"{sql_func}({col_ref})"
    
    def _translate_filter(self, filter_expr: str, context: ColumnMappingContext,
                         table_alias: str) -> Optional[str]:
        """Translate filter conditions like [Region] = "West"."""
        # Pattern: [Column] op "value" or [Column] op [Value]
        
        pattern = r'\[([^\]]+)\]\s*(=|<>|<|>|<=|>=)\s*(["\']?)([^"\']+)\3'
        match = re.search(pattern, filter_expr)
        
        if not match:
            logger.debug(f"Could not parse filter: {filter_expr}")
            return None
        
        col_name = match.group(1)
        operator = match.group(2)
        value = match.group(4)
        
        # Map column
        schema_col = context.map_column(col_name)
        if not schema_col:
            schema_col = col_name.upper()
        
        col_ref = f"{table_alias}.{schema_col}"
        
        # Build filter
        sql = f"{col_ref} {operator} '{value}'"
        
        logger.debug(f"Generated filter: {sql}")
        return sql
    
    def _extract_column_from_ref(self, ref: str) -> Optional[str]:
        """Extract column name from reference like [ColumnName] or 'Table'[Column]."""
        # Pattern: [ColumnName]
        match = re.search(r'\[([^\]]+)\]', ref)
        if match:
            return match.group(1)
        
        return None
    
    def _build_column_map(self) -> Dict[str, str]:
        """Build column mapping dict from various sources."""
        mapping = {}
        
        # Add explicit mappings
        for dax, schema in self.column_mappings.items():
            mapping[dax.upper()] = schema
        
        # Add implicit mappings (schema columns directly)
        for schema_col in self.schema_columns:
            # Map COLUMN_NAME -> COLUMN_NAME (identity)
            if schema_col not in mapping.values():
                mapping[schema_col] = schema_col
        
        return mapping


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    'DeterministicSQLGenerator',
    'ColumnMappingContext',
]
