#!/usr/bin/env python3
"""
Deterministic DAX → SQL Translator - Single Source of Truth

ENFORCES:
  1. DaxTranslationPipeline as ONLY translation engine
  2. Strict schema validation (rejects invalid columns)
  3. No heuristic fallbacks (SUM(*) forbidden)
  4. Full debug tracing of translation process
  5. No legacy system interference

REPLACES:
  - All Tier-based translation logic
  - All name-based inference
  - All fallback heuristics
  
GUARANTEES:
  - All SQL uses valid schema columns
  - No SUM(*) or invalid expressions
  - Deterministic results
  - Complete audit trail
"""

import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from semabridge.converter.dax_pipeline import DaxTranslationPipeline
from semabridge.converter.semantic_layer import (
    SemanticTranslator,
    get_semantic_translator,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DeterministicTranslationResult:
    """Result from deterministic translation."""
    sql: Optional[str] = None
    is_success: bool = False
    confidence: float = 0.0
    debug_trace: Dict = field(default_factory=dict)
    error_reason: Optional[str] = None
    schema_valid: bool = False
    tier: int = -1  # For compatibility (0=success, -1=failed)
    # NEW: Multi-table support
    joins: List[str] = field(default_factory=list)  # SQL JOIN clauses
    tables_referenced: List[str] = field(default_factory=list)  # Tables used
    
    @property
    def is_valid(self) -> bool:
        """Compatibility property."""
        return self.is_success
    
    def to_semantic_dict(self) -> Dict:
        """Convert to dict with semantic information."""
        return {
            'sql': self.sql,
            'joins': self.joins,
            'tables_referenced': self.tables_referenced,
            'is_success': self.is_success,
            'error_reason': self.error_reason,
            'debug_trace': self.debug_trace,
        }


class DeterministicTranslator:
    """
    Single-source-of-truth translator using DaxTranslationPipeline.
    
    RULES:
      1. NEVER use legacy Tier-based translation
      2. ALWAYS enforce schema validation
      3. REJECT any column not in schema
      4. NEVER generate SUM(*)
      5. ONLY return DaxTranslationPipeline output
      6. LOG EVERYTHING for debugging
    """
    
    # Valid schema columns
    VALID_SCHEMA_COLUMNS = ['DATE', 'PRODUCTID', 'REVENUE', 'UNITS', 'ZIP']
    
    # Column mappings: DAX [Name] → Schema NAME
    COLUMN_MAPPINGS = {
        'Units': 'UNITS',
        'Revenue': 'REVENUE',
        'ProductID': 'PRODUCTID',
        'Date': 'DATE',
        'Zip': 'ZIP',
    }
    
    def __init__(self, schema_columns: Optional[List[str]] = None):
        """
        Initialize with schema.
        
        Args:
            schema_columns: List of valid columns in schema (if None, uses defaults)
        """
        self.schema_columns = set(schema_columns or self.VALID_SCHEMA_COLUMNS)
        
        # Initialize pipeline with schema
        self.pipeline = DaxTranslationPipeline(
            schema_columns=list(self.schema_columns)
        )
        
        # Add mappings to pipeline
        self.pipeline.add_column_mappings(self.COLUMN_MAPPINGS)
        
        # Initialize semantic translator for multi-table support
        self.semantic_translator = get_semantic_translator(
            base_table="SalesFact",
            base_alias="fact"
        )
        
        logger.info(
            f"✅ DeterministicTranslator initialized\n"
            f"   ├─ Schema: {sorted(self.schema_columns)}\n"
            f"   ├─ Mappings: {len(self.COLUMN_MAPPINGS)} defined\n"
            f"   ├─ Semantic Layer: Enabled (multi-table support)\n"
            f"   └─ Pipeline: Ready (no legacy fallback)"
        )
    
    def translate(self,
                  dax: str,
                  table_alias: str = "fact",
                  dataset_name: str = "",
                  metric_name: str = "") -> DeterministicTranslationResult:
        """
        Translate DAX to SQL using ONLY the deterministic pipeline.
        
        NEW: Automatically applies semantic analysis for multi-table expressions.
        
        Args:
            dax: DAX expression
            table_alias: SQL table alias
            dataset_name: Dataset name for context
            metric_name: Metric name for logging
            
        Returns:
            DeterministicTranslationResult (now with joins if multi-table)
        """
        result = DeterministicTranslationResult()
        
        # === STAGE 0: SEMANTIC ANALYSIS ===
        logger.debug(f"[STAGE 0] Analyzing DAX for multi-table references")
        tables, table_errors = self.semantic_translator.analyze_dax_for_tables(dax)
        
        if table_errors:
            logger.warning(f"  ⚠️  Semantic analysis: {table_errors[0]}")
        else:
            result.tables_referenced = sorted(list(tables))
            result.debug_trace['semantic_tables'] = result.tables_referenced
        
        # === STAGE 1: PARSE & VALIDATE DAX ===
        logger.debug(f"[STAGE 1] Parsing DAX for metric: {metric_name or '?'}")
        
        if not dax or not dax.strip():
            result.error_reason = "Empty DAX expression"
            logger.warning(f"  ✗ {result.error_reason}")
            return result
        
        clean_dax = dax.strip()
        result.debug_trace['original_dax'] = clean_dax
        result.debug_trace['metric_name'] = metric_name
        result.debug_trace['table_alias'] = table_alias
        
        # === STAGE 2: LOAD DAX INTO PIPELINE ===
        logger.debug(f"[STAGE 2] Loading DAX into deterministic pipeline")
        
        try:
            # Convert raw DAX expression to MEASURE format for pipeline
            # Format: MEASURE 'TableName'[MeasureName] = Expression
            measure_format = f"MEASURE '{metric_name or 'Measure'}'[{metric_name or 'expr'}] = {clean_dax}"
            
            logger.debug(f"  Converting to pipeline format: {measure_format[:80]}")
            
            # Load measure from DAX
            n_loaded = self.pipeline.load_measures_from_dax(measure_format)
            
            if n_loaded == 0:
                result.error_reason = "Could not extract measure from DAX"
                logger.debug(f"  ✗ {result.error_reason}")
                result.debug_trace['extraction_failed'] = True
                return result
            
            logger.debug(f"  ✓ Loaded {n_loaded} measure(s)")
            result.debug_trace['measures_loaded'] = n_loaded
            
        except Exception as e:
            result.error_reason = f"Failed to load DAX: {str(e)}"
            logger.error(f"  ✗ {result.error_reason}")
            result.debug_trace['load_error'] = str(e)
            return result
        
        # === STAGE 3: RESOLVE ALL MEASURES ===
        logger.debug(f"[STAGE 3] Resolving measures")
        
        try:
            resolved = self.pipeline.resolve_all()
            result.debug_trace['resolution_results'] = {
                k: v[:60] if v else None for k, v in resolved.items()
            }
            
            # Get first resolved measure (should only be one for simple DAX)
            resolved_sql = None
            for measure_name, sql in resolved.items():
                if sql:
                    resolved_sql = sql
                    logger.debug(f"  ✓ Resolved '{measure_name}': {sql[:80]}")
                    break
            
            if not resolved_sql:
                failed = self.pipeline.get_failed_measures()
                result.error_reason = f"No measures resolved. Failures: {failed}"
                logger.debug(f"  ✗ {result.error_reason}")
                result.debug_trace['resolution_failures'] = failed
                return result
            
        except Exception as e:
            result.error_reason = f"Resolution failed: {str(e)}"
            logger.error(f"  ✗ {result.error_reason}")
            result.debug_trace['resolution_error'] = str(e)
            return result
        
        # === STAGE 4: VALIDATE SQL ===
        logger.debug(f"[STAGE 4] Validating translated SQL")
        
        validation_ok, validation_errors = self._validate_sql(resolved_sql)
        result.debug_trace['sql_validation'] = validation_errors
        
        if not validation_ok:
            result.error_reason = f"SQL validation failed: {validation_errors}"
            logger.error(f"  ✗ {result.error_reason}")
            return result
        
        logger.debug(f"  ✓ SQL validation passed")
        
        # === STAGE 5: SCHEMA VALIDATION ===
        logger.debug(f"[STAGE 5] Validating schema columns")
        
        schema_ok, schema_issues = self._validate_schema_columns(resolved_sql)
        result.schema_valid = schema_ok
        result.debug_trace['schema_validation'] = schema_issues
        
        if not schema_ok:
            result.error_reason = f"Schema validation failed: {schema_issues}"
            logger.error(f"  ✗ {result.error_reason}")
            return result
        
        logger.debug(f"  ✓ Schema validation passed")
        
        # === STAGE 6: FINAL SUBSTITUTION ===
        logger.debug(f"[STAGE 6] Applying table alias")
        
        final_sql = resolved_sql.replace('fact.', f'{table_alias}.')
        result.debug_trace['final_sql'] = final_sql
        
        # === STAGE 7: SEMANTIC ENRICHMENT (Multi-table Join Planning) ===
        logger.debug(f"[STAGE 7] Planning joins for multi-table expressions")
        
        if len(result.tables_referenced) > 1:
            joins, join_error = self.semantic_translator.plan_joins_for_dax(dax)
            
            if join_error:
                logger.warning(f"  ⚠️  Join planning failed: {join_error}")
            else:
                result.joins = [j.to_sql() for j in joins]
                result.debug_trace['joins_planned'] = len(joins)
                logger.debug(f"  ✓ Planned {len(joins)} joins for tables: {', '.join(result.tables_referenced)}")
        
        # === SUCCESS ===
        result.sql = final_sql
        result.is_success = True
        result.confidence = 1.0
        result.tier = 1  # Deterministic translation
        
        log_tables = f" (tables: {', '.join(result.tables_referenced)})" if result.tables_referenced else ""
        log_joins = f"\n   ├─ Joins: {len(result.joins)}" if result.joins else ""
        
        logger.info(
            f"✅ Translation succeeded for '{metric_name or 'metric'}'{log_tables}:\n"
            f"   ├─ Original: {clean_dax[:80]}\n"
            f"   ├─ Result:   {final_sql[:80]}{log_joins}\n"
            f"   ├─ Success:  True\n"
            f"   └─ Confidence: 1.0 (deterministic)"
        )
        
        return result
    
    def _validate_sql(self, sql: str) -> Tuple[bool, List[str]]:
        """
        Validate SQL syntax and safety.
        
        REJECTS:
          - Contains "*"
          - Contains SELECT/FROM keywords
          - Empty expressions
        """
        issues = []
        
        if not sql or not sql.strip():
            issues.append("SQL is empty")
            return False, issues
        
        sql_upper = sql.upper()
        
        # Check for invalid patterns
        if '*' in sql and 'SUM(*)' in sql_upper:
            issues.append("SUM(*) is invalid - forbidden fallback")
            return False, issues
        
        if '*' in sql and not any(f'{func}(' in sql_upper for func in ['COUNT(*)', 'SUM(*)*', 'AVG(*)']):
            # Check if * is used in invalid way (not COUNT(*))
            if 'COUNT(*)' not in sql_upper:
                issues.append("Standalone * found in SQL")
                return False, issues
        
        # Check for SELECT/FROM (metric expressions should not have these)
        if 'SELECT' in sql_upper or 'FROM' in sql_upper:
            issues.append("SQL contains SELECT/FROM - must be expression only")
            return False, issues
            
        # Check for DAX keywords that shouldn't be in SQL
        dax_keywords = ['CALCULATE(', 'FILTER(', 'ALL(', 'RELATED(', 'ISBLANK(']
        if any(keyword in sql_upper for keyword in dax_keywords):
            issues.append("SQL contains un-translated DAX keywords")
            return False, issues
        
        return True, issues
    
    def _validate_schema_columns(self, sql: str) -> Tuple[bool, List[str]]:
        """
        Validate that SQL only references columns in schema.
        
        REJECTS:
          - Any column not in VALID_SCHEMA_COLUMNS
          - Derived columns (TOTAL_UNITS, AMOUNT, IS_VAN_ARSDEL, etc.)
        """
        issues = []
        
        # Extract all column references (assume format: table.COLUMN)
        import re
        col_pattern = r'(\w+)\.(\w+)'
        matches = re.findall(col_pattern, sql)
        
        referenced_columns = set()
        for table_alias, col_name in matches:
            referenced_columns.add(col_name)
        
        # Check each referenced column
        for col in referenced_columns:
            if col not in self.schema_columns:
                # Check if it's a forbidden derived column
                forbidden = ['TOTAL_UNITS', 'AMOUNT', 'IS_VAN_ARSDEL', 'TOTAL_REVENUE']
                if col in forbidden:
                    issues.append(
                        f"Column '{col}' is derived/invalid - use '{self._get_base_name(col)}' from schema"
                    )
                else:
                    issues.append(f"Column '{col}' not in schema: {sorted(self.schema_columns)}")
        
        return len(issues) == 0, issues
    
    def _get_base_name(self, derived: str) -> str:
        """Map derived column names to schema columns."""
        mapping = {
            'TOTAL_UNITS': 'UNITS',
            'TOTAL_REVENUE': 'REVENUE',
            'AMOUNT': 'REVENUE',
        }
        return mapping.get(derived, 'UNKNOWN')
    
    def get_debug_trace(self, result: DeterministicTranslationResult) -> Dict:
        """Get detailed debug information about translation."""
        return result.debug_trace
    
    def get_statistics(self) -> Dict:
        """Get pipeline statistics."""
        return self.pipeline.get_statistics()
    
    # ========================================================================
    # NEW: Semantic/Multi-table Methods
    # ========================================================================
    
    def get_table_schema(self, table_name: str) -> Optional[List[str]]:
        """Get columns available in a specific table."""
        from semabridge.converter.semantic_layer import get_table_schema
        return get_table_schema(table_name)
    
    def get_available_tables(self) -> List[str]:
        """Get all available tables in semantic model."""
        from semabridge.converter.semantic_layer import get_all_tables
        return get_all_tables()
    
    def get_relationships(self) -> List[Tuple[str, str]]:
        """Get all defined table relationships."""
        from semabridge.converter.semantic_layer import get_all_relationships
        return get_all_relationships()
    
    def analyze_dax_semantics(self, dax_expression: str) -> Dict:
        """
        Analyze DAX expression for semantic information.
        
        Returns:
            {
                'tables_referenced': [...],
                'columns_referenced': [...],
                'required_joins': [...],
                'errors': [...],
            }
        """
        from semabridge.converter.semantic_layer import SemanticResolver
        
        tables, errors = self.semantic_translator.analyze_dax_for_tables(dax_expression)
        col_refs = SemanticResolver.collect_column_references(dax_expression)
        joins, join_error = self.semantic_translator.plan_joins_for_dax(dax_expression)
        
        return {
            'tables_referenced': sorted(list(tables)),
            'columns_referenced': [str(c) for c in col_refs],
            'required_joins': [j.to_sql() for j in joins] if joins else [],
            'errors': errors + ([join_error] if join_error else []),
        }


# Singleton instance
_deterministic_translator = None


def get_deterministic_translator() -> DeterministicTranslator:
    """Get or create singleton deterministic translator."""
    global _deterministic_translator
    if not _deterministic_translator:
        _deterministic_translator = DeterministicTranslator()
    return _deterministic_translator


# Export for backward compatibility
translate = DeterministicTranslationResult