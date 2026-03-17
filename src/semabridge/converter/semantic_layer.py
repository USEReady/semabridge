#!/usr/bin/env python3
"""
Semantic Layer for Multi-table DAX → SQL Translation

FEATURES:
  1. Table schema definitions with relationships
  2. Column resolution with table context
  3. Join planning engine
  4. Multi-table expression generation
  5. Maintains deterministic guarantees while supporting cross-table logic

STRUCTURE:
  - TABLE_SCHEMAS: Central registry of all table columns
  - RELATIONSHIPS: Foreign key definitions for joins
  - SemanticResolver: Resolves table.column references
  - JoinPlanner: Plans optimal joins necessary
  - SemanticTranslator: Enhanced translator with join support
"""

import logging
from typing import Optional, Dict, List, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
import re

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# CENTRAL SEMANTIC MODEL
# ============================================================================

class JoinType(Enum):
    """SQL join types."""
    INNER = "INNER JOIN"
    LEFT = "LEFT JOIN"
    RIGHT = "RIGHT JOIN"


# Table schemas: maps table name to list of valid columns
TABLE_SCHEMAS = {
    "SalesFact": [
        "DATE",           # FK to Date table
        "PRODUCTID",      # FK to Product table
        "REVENUE",        # Measure
        "UNITS",          # Measure
        "ZIP",            # Dimension
        "QUANTITY",       # Measure
        "COST",           # Measure
    ],
    "Product": [
        "PRODUCTID",      # PK
        "ISVANARSDEL",    # Attribute (Y/N)
        "PRODUCTNAME",    # Attribute
        "CATEGORY",       # Attribute
        "MANUFACTURER",   # FK to Manufacturer
    ],
    "Date": [
        "DATE",           # PK
        "MONTHINDEX",     # Attribute
        "YEAR",           # Attribute
        "MONTH",          # Attribute
        "DAYOFWEEK",      # Attribute
        "QUARTER",        # Attribute
    ],
    "Sentiment": [
        "PRODUCTID",      # FK to Product
        "SCORE",          # Measure
        "SENTIMENT_TEXT", # Attribute
    ],
    "Manufacturer": [
        "MFGISVANARSDEL", # Attribute (Y/N)
        "MFGNAME",        # Attribute
    ],
}

# Relationship definitions: (source_table.column, target_table.column)
RELATIONSHIPS = [
    ("SalesFact.PRODUCTID", "Product.PRODUCTID"),
    ("SalesFact.DATE", "Date.DATE"),
    ("SalesFact.PRODUCTID", "Sentiment.PRODUCTID"),
    ("Product.MANUFACTURER", "Manufacturer.MFGNAME"),  # Note: Different join key
]


# ============================================================================
# SEMANTIC STRUCTURES
# ============================================================================

@dataclass
class ColumnReference:
    """Represents a column with table context."""
    table_name: str
    column_name: str
    qualified_name: Optional[str] = None  # e.g., "Product.ISVANARSDEL"
    
    def __post_init__(self):
        if not self.qualified_name:
            self.qualified_name = f"{self.table_name}.{self.column_name}"
    
    def __str__(self) -> str:
        return self.qualified_name
    
    def with_alias(self, alias: str) -> str:
        """Return column reference with table alias."""
        return f"{alias}.{self.column_name}"


@dataclass
class JoinDefinition:
    """SQL join clause."""
    join_type: JoinType
    target_table: str
    target_alias: str
    source_table: str
    source_alias: str
    on_condition: str
    
    def to_sql(self) -> str:
        """Generate SQL join clause."""
        return (
            f"{self.join_type.value} {self.target_table} AS {self.target_alias} "
            f"ON {self.on_condition}"
        )


@dataclass
class SemanticTranslationResult:
    """Result with SQL expression and required joins."""
    sql: Optional[str] = None
    joins: List[JoinDefinition] = field(default_factory=list)
    tables_referenced: Set[str] = field(default_factory=set)
    is_success: bool = False
    error_reason: Optional[str] = None
    debug_trace: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """Convert to dict for serialization."""
        return {
            "sql": self.sql,
            "joins": [j.to_sql() for j in self.joins],
            "tables_referenced": sorted(list(self.tables_referenced)),
            "is_success": self.is_success,
            "error_reason": self.error_reason,
        }


# ============================================================================
# SEMANTIC RESOLVER
# ============================================================================

class SemanticResolver:
    """Resolves table/column references and validates against schema."""
    
    @staticmethod
    def parse_column_reference(reference: str) -> Optional[ColumnReference]:
        """
        Parse column reference from DAX syntax:
        - "Table[Column]" → ColumnReference("Table", "Column")
        - "[Column]" → ColumnReference("SalesFact", "Column") [default table]
        
        Args:
            reference: Column reference string
            
        Returns:
            ColumnReference or None if invalid
        """
        # Pattern: Table[ColumnName]
        match = re.match(r"'?(\w+)'?\[(\w+)\]", reference)
        if match:
            table_name = match.group(1)
            column_name = match.group(2)
            
            # Validate table exists
            if table_name not in TABLE_SCHEMAS:
                logger.debug(f"Unknown table: {table_name}")
                return None
            
            # Validate column exists in table
            schema = TABLE_SCHEMAS[table_name]
            if column_name.upper() not in schema:
                logger.debug(f"Column {column_name} not in {table_name}")
                return None
            
            return ColumnReference(
                table_name=table_name,
                column_name=column_name.upper()
            )
        
        # Pattern: [ColumnName] without table → assume SalesFact
        match = re.match(r"\[(\w+)\]", reference)
        if match:
            column_name = match.group(1)
            if column_name.upper() in TABLE_SCHEMAS["SalesFact"]:
                return ColumnReference(
                    table_name="SalesFact",
                    column_name=column_name.upper()
                )
        
        return None
    
    @staticmethod
    def validate_column_exists(table: str, column: str) -> bool:
        """Check if column exists in table schema."""
        if table not in TABLE_SCHEMAS:
            return False
        return column.upper() in TABLE_SCHEMAS[table]
    
    @staticmethod
    def validate_table_exists(table: str) -> bool:
        """Check if table exists in schema."""
        return table in TABLE_SCHEMAS
    
    @staticmethod
    def collect_column_references(dax_expression: str) -> List[ColumnReference]:
        """Extract all column references from DAX expression."""
        references = []
        
        # Find patterns: Table[Column]
        table_col_pattern = r"'?(\w+)'?\[(\w+)\]"
        matches = re.finditer(table_col_pattern, dax_expression)
        
        seen = set()
        for match in matches:
            table_name = match.group(1)
            column_name = match.group(2)
            
            # Skip if already processed
            key = f"{table_name}.{column_name}"
            if key in seen:
                continue
            seen.add(key)
            
            # Validate and add
            col_ref = SemanticResolver.parse_column_reference(match.group(0))
            if col_ref:
                references.append(col_ref)
        
        return references


# ============================================================================
# JOIN PLANNER
# ============================================================================

class JoinPlanner:
    """Plans optimal joins based on referenced tables."""
    
    @staticmethod
    def _find_join_path(from_table: str, to_table: str) -> Optional[str]:
        """Find relationship between two tables."""
        for source_fk, target_pk in RELATIONSHIPS:
            source_table, source_col = source_fk.split('.')
            target_table, target_col = target_pk.split('.')
            
            # Direction 1: from_table → to_table
            if source_table == from_table and target_table == to_table:
                return f"{source_fk} = {target_pk}"
            
            # Direction 2: to_table → from_table (reverse join)
            if source_table == to_table and target_table == from_table:
                return f"{source_col} = {source_fk}"
        
        return None
    
    @staticmethod
    def plan_joins(
        base_table: str,
        required_tables: Set[str],
        base_alias: str = "fact",
    ) -> Tuple[List[JoinDefinition], Optional[str]]:
        """
        Plan join sequence from base table to all required tables.
        
        Args:
            base_table: Starting table (usually "SalesFact")
            required_tables: Set of tables that need to be joined
            base_alias: Alias for base table
            
        Returns:
            (List of JoinDefinition, error_reason if any)
        """
        joins = []
        processed = {base_table}
        required_tables = required_tables - {base_table}
        
        # Validate all tables exist
        for table in required_tables:
            if not SemanticResolver.validate_table_exists(table):
                return [], f"Unknown table: {table}"
        
        # Plan joins
        for target_table in sorted(required_tables):
            # Find join path from base → target
            join_condition = JoinPlanner._find_join_path(base_table, target_table)
            
            if not join_condition:
                return [], f"No join path found from {base_table} to {target_table}"
            
            # Create join definition
            target_alias = target_table.lower()[:3]  # Use 3-letter alias
            join_def = JoinDefinition(
                join_type=JoinType.LEFT,
                target_table=target_table,
                target_alias=target_alias,
                source_table=base_table,
                source_alias=base_alias,
                on_condition=join_condition.replace(
                    f"{base_table}.", f"{base_alias}."
                ).replace(
                    f"{target_table}.", f"{target_alias}."
                )
            )
            joins.append(join_def)
            processed.add(target_table)
        
        return joins, None


# ============================================================================
# SEMANTIC TRANSLATOR EXTENSION
# ============================================================================

class SemanticTranslator:
    """
    Enhanced translator that supports multi-table DAX expressions.
    
    Capabilities:
      - Resolves table/column references
      - Plans required joins
      - Generates join-aware SQL
      - Maintains deterministic guarantees
    """
    
    def __init__(self, base_table: str = "SalesFact", base_alias: str = "fact"):
        """
        Initialize semantic translator.
        
        Args:
            base_table: Primary table for aggregations
            base_alias: SQL alias for base table
        """
        self.base_table = base_table
        self.base_alias = base_alias
        self.resolver = SemanticResolver()
        self.planner = JoinPlanner()
    
    def analyze_dax_for_tables(self, dax_expression: str) -> Tuple[Set[str], List[str]]:
        """
        Analyze DAX to find referenced tables.
        
        Args:
            dax_expression: DAX formula
            
        Returns:
            (set of table names, list of errors if any)
        """
        col_refs = self.resolver.collect_column_references(dax_expression)
        errors = []
        
        tables = {self.base_table}  # Always include base table
        for ref in col_refs:
            tables.add(ref.table_name)
        
        # Validate all tables
        for table in tables:
            if not self.resolver.validate_table_exists(table):
                errors.append(f"Unknown table: {table}")
        
        return tables, errors
    
    def plan_joins_for_dax(self, dax_expression: str) -> Tuple[List[JoinDefinition], Optional[str]]:
        """
        Plan joins needed for a DAX expression.
        
        Args:
            dax_expression: DAX formula
            
        Returns:
            (List of JoinDefinition, error_reason if any)
        """
        tables, errors = self.analyze_dax_for_tables(dax_expression)
        
        if errors:
            return [], errors[0]
        
        joins, error = self.planner.plan_joins(
            self.base_table,
            tables,
            self.base_alias
        )
        
        return joins, error
    
    def translate_to_semantic_sql(
        self,
        sql_expression: str,
        dax_expression: str,
    ) -> SemanticTranslationResult:
        """
        Translate SQL expression and plan joins for DAX.
        
        Args:
            sql_expression: Base SQL expression (single-table)
            dax_expression: Original DAX for context
            
        Returns:
            SemanticTranslationResult with SQL + joins
        """
        result = SemanticTranslationResult()
        
        # Analyze DAX for multi-table references
        tables, table_errors = self.analyze_dax_for_tables(dax_expression)
        
        if table_errors:
            result.error_reason = f"Table analysis failed: {table_errors[0]}"
            logger.error(f"  ✗ {result.error_reason}")
            return result
        
        result.tables_referenced = tables
        result.debug_trace['tables_referenced'] = sorted(list(tables))
        
        # Plan joins
        if len(tables) > 1:  # Multi-table case
            joins, join_error = self.plan_joins_for_dax(dax_expression)
            
            if join_error:
                result.error_reason = f"Join planning failed: {join_error}"
                logger.error(f"  ✗ {result.error_reason}")
                return result
            
            result.joins = joins
            result.debug_trace['joins_planned'] = len(joins)
            result.debug_trace['join_sql'] = [j.to_sql() for j in joins]
        
        # SQL expression stays the same (already valid)
        result.sql = sql_expression
        result.is_success = True
        
        logger.info(
            f"✅ Semantic translation successful:\n"
            f"   ├─ Tables: {', '.join(sorted(tables))}\n"
            f"   ├─ Joins needed: {len(result.joins)}\n"
            f"   ├─ SQL: {sql_expression[:60]}...\n"
            f"   └─ Deterministic: Yes"
        )
        
        return result


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_table_schema(table_name: str) -> Optional[List[str]]:
    """Get columns for a table."""
    return TABLE_SCHEMAS.get(table_name)


def get_all_tables() -> List[str]:
    """Get all available tables."""
    return sorted(list(TABLE_SCHEMAS.keys()))


def get_all_relationships() -> List[Tuple[str, str]]:
    """Get all defined relationships."""
    return RELATIONSHIPS


# Singleton instance
_semantic_translator = None


def get_semantic_translator(
    base_table: str = "SalesFact",
    base_alias: str = "fact"
) -> SemanticTranslator:
    """Get or create semantic translator."""
    global _semantic_translator
    if _semantic_translator is None:
        _semantic_translator = SemanticTranslator(base_table, base_alias)
    return _semantic_translator
