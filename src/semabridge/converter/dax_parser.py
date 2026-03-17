#!/usr/bin/env python3
"""
DAX Parse Parser - Extract structured information from DAX measure definitions.

Parses DAX measure definitions like:
  MEASURE 'SalesFact'[Total Units] = SUM([Units])
  MEASURE 'SalesFact'[Revenue] = CALCULATE(SUM([Amount]), ALL([Category]))

Into structured MeasureDefinition objects suitable for dependency resolution
and deterministic translation.
"""

import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class MeasureType(Enum):
    """Type of measure definition."""
    AGGREGATION = "aggregation"          # SUM, AVG, COUNT, MIN, MAX, DISTINCTCOUNT
    CALCULATED = "calculated"            # CALCULATE, IF, SWITCH, arithmetic
    TIME_BASED = "time_based"            # Time intelligence: TOTALYTD, TOTALMTD, etc.
    DIVISION = "division"                # DIVIDE function
    COMPLEX = "complex"                  # Nested, iterators, complex logic
    UNKNOWN = "unknown"


@dataclass
class DaxExpression:
    """Parsed DAX expression components."""
    raw: str                             # Original DAX text
    type: MeasureType                    # Type of expression
    function: Optional[str] = None       # Primary function (SUM, CALCULATE, etc.)
    columns: List[str] = field(default_factory=list)  # Referenced columns
    measures: List[str] = field(default_factory=list) # Referenced measures [MeasureName]
    filters: List[str] = field(default_factory=list)  # Filter expressions
    is_deterministic: bool = False       # Can be deterministically translated?
    complexity_score: int = 0            # 0-100, higher = more complex


@dataclass
class MeasureDefinition:
    """Parsed DAX measure definition."""
    measure_name: str                    # e.g., "Total Units"
    table_name: str                      # e.g., "SalesFact"
    dax_definition: str                  # Full DAX expression
    expression: DaxExpression            # Parsed expression details
    dependencies: List[str] = field(default_factory=list)  # Measures this depends on
    is_simple: bool = False              # Can be translated deterministically?
    confidence: float = 0.0              # 0-1.0, translation confidence


class DaxExpressionParser:
    """Parse DAX expressions into structured components."""
    
    # Common aggregation functions (deterministic tier 1)
    AGGREGATION_FUNCTIONS = {
        'SUM', 'AVERAGE', 'AVERAGEX', 'COUNT', 'COUNTA', 'COUNTROWS',
        'MIN', 'MINX', 'MAX', 'MAXX', 'DISTINCTCOUNT', 'VALUES'
    }
    
    # Time intelligence functions (tier 3)
    TIME_INTEL_FUNCTIONS = {
        'TOTALYTD', 'TOTALMTD', 'TOTALQTD',
        'SAMEPERIODLASTYEAR', 'PREVIOUSYEAR', 'PREVIOUSMONTH', 'PREVIOUSQUARTER',
        'OPENINGBALANCEYEAR', 'CLOSINGBALANCEYEAR',
        'DATEADD', 'DATESYTD', 'DATESMTD', 'DATESQTD',
    }
    
    # Complex functions (require LLM or special handling)
    COMPLEX_FUNCTIONS = {
        'SUMX', 'AVERAGEX', 'COUNTX', 'MINX', 'MAXX',
        'RANKX', 'EARLIER', 'EARLIEST',
        'GENERATE', 'GENERATESERIES', 'SUMMARIZECOLUMNS',
        'GROUPBY', 'ADDCOLUMNS'
    }
    
    def __init__(self):
        pass
    
    def parse(self, dax: str) -> DaxExpression:
        """
        Parse a DAX expression into components.
        
        Args:
            dax: DAX expression string
            
        Returns:
            DaxExpression with parsed components
        """
        if not dax or not isinstance(dax, str):
            return DaxExpression(raw=dax or "", type=MeasureType.UNKNOWN)
        
        clean_dax = dax.strip()
        
        # Extract components
        expression = DaxExpression(raw=clean_dax)
        expression.columns = self._extract_columns(clean_dax)
        expression.measures = self._extract_measures(clean_dax)
        expression.filters = self._extract_filters(clean_dax)
        expression.function = self._detect_function(clean_dax)
        expression.type = self._classify_type(clean_dax, expression.function)
        expression.complexity_score = self._score_complexity(clean_dax, expression.type)
        expression.is_deterministic = self._is_deterministic(expression.type, clean_dax)
        
        logger.debug(
            f"Parsed DAX: {expression.function} | "
            f"Type: {expression.type.value} | "
            f"Deterministic: {expression.is_deterministic} | "
            f"Columns: {expression.columns} | "
            f"Measures: {expression.measures}"
        )
        
        return expression
    
    def _extract_columns(self, dax: str) -> List[str]:
        """Extract column references like [ColumnName] or 'Table'[Column]."""
        columns = []
        
        # Pattern 1: [ColumnName] (measure reference, but treat as column if not in measures)
        col_refs = re.findall(r'\[([^\]]+)\]', dax)
        
        # Pattern 2: 'Table'[Column]
        table_col_refs = re.findall(r"'([^']+)'\[([^\]]+)\]", dax)
        
        # Add simple column references
        for col in col_refs:
            if col not in columns and not self._is_measure_ref(col):
                columns.append(col)
        
        # Add qualified column references
        for table, col in table_col_refs:
            if col not in columns:
                columns.append(col)
        
        return columns
    
    def _extract_measures(self, dax: str) -> List[str]:
        """Extract measure references like [MeasureName]."""
        measures = []
        
        # Find [MeasureName] references
        # These are column references that look like measure names (not lowercase, has spaces, etc.)
        refs = re.findall(r'\[([^\]]+)\]', dax)
        
        for ref in refs:
            # Consider it a measure if it matches common patterns:
            # - Has spaces (e.g., "Total Sales")
            # - Starts with capital letter (proper naming)
            # - Is not a simple column name (UNITS, AMOUNT, etc.)
            if ref not in measures:
                if ' ' in ref or ref[0].isupper():
                    measures.append(ref)
        
        return measures
    
    def _extract_filters(self, dax: str) -> List[str]:
        """Extract filter expressions."""
        filters = []
        
        # Find FILTER(...) expressions
        filter_pattern = r'FILTER\s*\(([^)]+)\)'
        for match in re.finditer(filter_pattern, dax, re.IGNORECASE):
            filters.append(match.group(1))
        
        # Find simple comparison filters in CALCULATE
        # e.g., [Region] = "West"
        condition_pattern = r'\[([^\]]+)\]\s*(=|<>|<|>|<=|>=)\s*["\']([^"\']+)["\']'
        for match in re.finditer(condition_pattern, dax):
            filters.append(match.group(0))
        
        return filters
    
    def _detect_function(self, dax: str) -> Optional[str]:
        """Detect primary DAX function in expression."""
        dax_upper = dax.upper()
        
        # Check for functions in order of priority
        for func in self.AGGREGATION_FUNCTIONS:
            if f'{func}(' in dax_upper:
                return func
        
        for func in self.TIME_INTEL_FUNCTIONS:
            if f'{func}(' in dax_upper:
                return func
        
        for func in self.COMPLEX_FUNCTIONS:
            if f'{func}(' in dax_upper:
                return func
        
        if 'CALCULATE(' in dax_upper:
            return 'CALCULATE'
        
        if 'DIVIDE(' in dax_upper:
            return 'DIVIDE'
        
        if 'IF(' in dax_upper:
            return 'IF'
        
        if 'SWITCH(' in dax_upper:
            return 'SWITCH'
        
        return None
    
    def _is_measure_ref(self, name: str) -> bool:
        """Check if a bracketed reference looks like a measure (not a column)."""
        # Measure names typically have spaces or are descriptive
        # Column names are typically single words or abbreviated
        return ' ' in name or name[0].isupper() and len(name) > 1
    
    def _classify_type(self, dax: str, function: Optional[str]) -> MeasureType:
        """Classify the type of measure."""
        dax_upper = dax.upper()
        
        if not function:
            return MeasureType.UNKNOWN
        
        # Check for time intelligence
        if any(tf in dax_upper for tf in self.TIME_INTEL_FUNCTIONS):
            return MeasureType.TIME_BASED
        
        # Check for divisions
        if 'DIVIDE(' in dax_upper:
            return MeasureType.DIVISION
        
        # Check for simple aggregations
        if function in self.AGGREGATION_FUNCTIONS:
            return MeasureType.AGGREGATION
        
        # Check for complex functions
        if function in self.COMPLEX_FUNCTIONS:
            return MeasureType.COMPLEX
        
        # Check for CALCULATE
        if 'CALCULATE(' in dax_upper:
            return MeasureType.CALCULATED
        
        # Check for arithmetic/IF/SWITCH
        if any(kw in dax_upper for kw in ['IF(', 'SWITCH(', '+', '-', '*', '/']):
            return MeasureType.CALCULATED
        
        return MeasureType.UNKNOWN
    
    def _score_complexity(self, dax: str, measure_type: MeasureType) -> int:
        """Score complexity of the DAX expression (0-100)."""
        score = 0
        
        # Base score by type
        type_scores = {
            MeasureType.AGGREGATION: 10,
            MeasureType.DIVISION: 20,
            MeasureType.CALCULATED: 30,
            MeasureType.TIME_BASED: 40,
            MeasureType.COMPLEX: 80,
            MeasureType.UNKNOWN: 50,
        }
        score = type_scores.get(measure_type, 50)
        
        # Complexity factors
        dax_upper = dax.upper()
        
        # Nested function calls
        open_parens = dax.count('(')
        if open_parens > 5:
            score += (open_parens - 5) * 5
        
        # Filters
        if 'FILTER(' in dax_upper:
            score += 10
        
        # Multiple conditions
        if dax_upper.count('=') > 2:
            score += 10
        
        # Iterators
        if any(it in dax_upper for it in ['SUMX', 'AVERAGEX', 'COUNTX']):
            score += 20
        
        # Row context
        if any(rc in dax_upper for rc in ['EARLIER(', 'EARLIEST(', 'RANKX(']):
            score += 30
        
        return min(score, 100)
    
    def _is_deterministic(self, measure_type: MeasureType, dax: str) -> bool:
        """Check if expression can be deterministically translated."""
        dax_upper = dax.upper()
        
        # Deterministic types
        if measure_type in [MeasureType.AGGREGATION, MeasureType.DIVISION]:
            return True
        
        if measure_type == MeasureType.TIME_BASED:
            # Simple time intelligence is deterministic
            return not any(it in dax_upper for it in ['SUMX', 'RANKX', 'EARLIER'])
        
        if measure_type == MeasureType.CALCULATED:
            # Simple CALCULATE with filters is often deterministic
            if 'CALCULATE(' in dax_upper:
                # Check for complex patterns
                if any(cp in dax_upper for cp in ['SUMX', 'RANKX', 'EARLIER', 'GENERATE']):
                    return False
                return True
            # Simple arithmetic and IF/SWITCH
            return not any(cp in dax_upper for cp in ['SUMX', 'RANKX', 'EARLIER'])
        
        # Complex type not deterministic
        if measure_type == MeasureType.COMPLEX:
            return False
        
        return False


class MeasureDefinitionExtractor:
    """Extract measure definitions from DAX MEASURE statements."""
    
    def extract(self, measure_statement: str) -> Optional[MeasureDefinition]:
        """
        Extract a measure definition from a MEASURE statement.
        
        Format:
          MEASURE 'TableName'[MeasureName] = DAX_EXPRESSION
        
        Args:
            measure_statement: Full measure definition text
            
        Returns:
            MeasureDefinition or None if parsing fails
        """
        if not measure_statement or not isinstance(measure_statement, str):
            return None
        
        clean = measure_statement.strip()
        
        # Parse: MEASURE 'TableName'[MeasureName] = Expression
        pattern = r"MEASURE\s+'([^']+)'\[([^\]]+)\]\s*=\s*(.+)"
        match = re.match(pattern, clean, re.IGNORECASE | re.DOTALL)
        
        if not match:
            logger.warning(f"Failed to parse measure statement: {clean[:60]}...")
            return None
        
        table_name = match.group(1)
        measure_name = match.group(2)
        dax_expr = match.group(3).strip()
        
        # Parse the DAX expression
        parser = DaxExpressionParser()
        parsed_expr = parser.parse(dax_expr)
        
        # Create measure definition
        measure_def = MeasureDefinition(
            measure_name=measure_name,
            table_name=table_name,
            dax_definition=clean,
            expression=parsed_expr,
            dependencies=parsed_expr.measures,
            is_simple=parsed_expr.is_deterministic,
            confidence=0.95 if parsed_expr.is_deterministic else 0.6,
        )
        
        logger.info(
            f"✓ Extracted measure: {measure_name} "
            f"({parsed_expr.type.value}, simple={measure_def.is_simple})"
        )
        
        return measure_def
    
    def extract_all(self, text: str) -> List[MeasureDefinition]:
        """
        Extract all measure definitions from text.
        
        Args:
            text: Text containing multiple MEASURE statements
            
        Returns:
            List of MeasureDefinition objects
        """
        measures = []
        
        # Split by MEASURE keyword (case-insensitive)
        # Find all "MEASURE 'Table'[Name] = ..." patterns
        pattern = r"MEASURE\s+'[^']+'\[[^\]]+\]\s*=\s*[^;]*(?:;|(?=MEASURE))"
        
        for match in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL):
            measure_text = match.group(0)
            measure_def = self.extract(measure_text)
            if measure_def:
                measures.append(measure_def)
        
        logger.info(f"Extracted {len(measures)} measures from text")
        return measures


class SimpleMeasureValidator:
    """Validate that simple measures can be safely translated."""
    
    def validate(self, measure: MeasureDefinition, schema_columns: List[str]) -> Tuple[bool, str]:
        """
        Validate a measure definition.
        
        Args:
            measure: MeasureDefinition to validate
            schema_columns: Available columns in schema
            
        Returns:
            (is_valid: bool, reason: str)
        """
        if not measure.is_simple:
            return True, "Complex measure - will use LLM if needed"
        
        # For simple measures, check that columns exist
        missing = []
        for col in measure.expression.columns:
            if col not in schema_columns:
                missing.append(col)
        
        if missing:
            return False, f"Missing columns: {missing}"
        
        # Check for unresolved measure dependencies
        unresolved_measures = measure.expression.measures
        if unresolved_measures:
            # This is OK - will be resolved during dependency resolution
            logger.debug(f"Measure {measure.measure_name} has dependencies: {unresolved_measures}")
        
        return True, "Valid"


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    'DaxExpressionParser',
    'MeasureDefinitionExtractor',
    'SimpleMeasureValidator',
    'MeasureDefinition',
    'DaxExpression',
    'MeasureType',
]
