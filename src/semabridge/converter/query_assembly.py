#!/usr/bin/env python3
"""
Query Assembly Layer - Build Full Executable SQL Queries

RESPONSIBILITY:
  1. Assemble multiple translated metrics into one query
  2. Merge and deduplicate joins
  3. Build SELECT clause with aliases
  4. Add FROM and JOIN clauses
  5. Support GROUP BY for dimension analysis
  6. Validate final SQL for Snowflake compatibility
  7. Log assembly process

FEATURES:
  - Deterministic join ordering
  - Automatic alias generation
  - Duplicate join detection
  - Column validation
  - SQL formatting
  - Comprehensive logging
"""

import logging
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
import re

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class AggregationFunction(Enum):
    """Supported SQL aggregation functions."""
    SUM = "SUM"
    AVG = "AVG"
    COUNT = "COUNT"
    MIN = "MIN"
    MAX = "MAX"
    AVERAGE = "AVG"  # Alias for AVG


@dataclass
class TranslatedMetric:
    """Result of translating a DAX metric to SQL."""
    name: str                               # Metric name
    dax_expression: str                     # Original DAX
    sql_expression: str                     # Translated SQL
    joins: List[str] = field(default_factory=list)  # Required JOIN clauses
    tables_referenced: List[str] = field(default_factory=list)  # Tables needed
    is_success: bool = True
    error_reason: Optional[str] = None
    
    @property
    def select_alias(self) -> str:
        """Generate SELECT clause alias from metric name."""
        # Sanitize name: uppercase, replace spaces/hyphens with underscore, remove special chars
        alias = re.sub(r'[^A-Za-z0-9_]', '_', self.name)
        alias = re.sub(r'_+', '_', alias)  # Collapse multiple underscores
        alias = alias.upper()
        return alias.lstrip('_').rstrip('_')  # Remove leading/trailing underscores
    
    def to_select_clause(self) -> str:
        """Generate SELECT clause item."""
        return f"{self.sql_expression} AS {self.select_alias}"


@dataclass
class DimensionField:
    """Represents a dimension field for GROUP BY."""
    table: str
    column: str
    alias: str
    
    @property
    def table_alias(self) -> str:
        """Generate table alias (first 3 letters)."""
        return self.table.lower()[:3]
    
    def to_sql(self) -> str:
        """Generate SQL for GROUP BY clause."""
        return f"{self.table_alias}.{self.column} AS {self.alias}"
    
    def to_group_by(self) -> str:
        """Generate SQL for GROUP BY clause."""
        return f"{self.table_alias}.{self.column}"


@dataclass
class AssembledQuery:
    """Complete assembled SQL query."""
    select_clause: str
    from_clause: str
    joins: List[str]
    group_by_clause: Optional[str] = None
    where_clause: Optional[str] = None
    order_by_clause: Optional[str] = None
    
    @property
    def full_sql(self) -> str:
        """Generate complete SQL statement."""
        sql = f"SELECT\n  {self.select_clause}\nFROM {self.from_clause}"
        
        # Add joins
        for join in self.joins:
            sql += f"\n{join}"
        
        # Add WHERE clause
        if self.where_clause:
            sql += f"\nWHERE {self.where_clause}"
        
        # Add GROUP BY
        if self.group_by_clause:
            sql += f"\nGROUP BY {self.group_by_clause}"
        
        # Add ORDER BY
        if self.order_by_clause:
            sql += f"\nORDER BY {self.order_by_clause}"
        
        return sql
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            'select_clause': self.select_clause,
            'from_clause': self.from_clause,
            'joins': self.joins,
            'group_by_clause': self.group_by_clause,
            'where_clause': self.where_clause,
            'order_by_clause': self.order_by_clause,
            'full_sql': self.full_sql,
        }


# ============================================================================
# JOIN DEDUPLICATION & MANAGEMENT
# ============================================================================

class JoinManager:
    """Manages join clauses - combines, deduplicates, orders."""
    
    @staticmethod
    def parse_join_clause(join_sql: str) -> Optional[Dict]:
        """
        Parse JOIN clause to extract components.
        
        Example:
          "LEFT JOIN Product AS pro ON fact.PRODUCTID = pro.PRODUCTID"
          →
          {
            'type': 'LEFT JOIN',
            'table': 'Product',
            'alias': 'pro',
            'condition': 'fact.PRODUCTID = pro.PRODUCTID'
          }
        """
        # Pattern: (INNER|LEFT|RIGHT)? JOIN table AS alias ON condition
        pattern = r'(\w+\s+)?JOIN\s+(\w+)\s+AS\s+(\w+)\s+ON\s+(.+)'
        match = re.match(pattern, join_sql.strip())
        
        if not match:
            logger.warning(f"Could not parse join: {join_sql}")
            return None
        
        join_type = match.group(1).strip() if match.group(1) else "LEFT JOIN"
        table = match.group(2)
        alias = match.group(3)
        condition = match.group(4)
        
        return {
            'type': join_type,
            'table': table,
            'alias': alias,
            'condition': condition,
            'original': join_sql,
        }
    
    @staticmethod
    def deduplicate_joins(joins: List[str]) -> List[str]:
        """
        Remove duplicate joins, keeping first occurrence.
        
        Two joins are considered duplicates if:
        - Same table and alias
        - Same join condition
        """
        seen = {}
        deduped = []
        
        for join_sql in joins:
            parsed = JoinManager.parse_join_clause(join_sql)
            if not parsed:
                deduped.append(join_sql)  # Keep unparseable joins as-is
                continue
            
            # Create key for deduplication
            key = f"{parsed['table']}_{parsed['alias']}_{parsed['condition']}"
            
            if key not in seen:
                seen[key] = True
                deduped.append(join_sql)
            else:
                logger.debug(f"  → Removed duplicate join: {parsed['table']} AS {parsed['alias']}")
        
        return deduped
    
    @staticmethod
    def order_joins_deterministic(joins: List[str]) -> List[str]:
        """
        Order joins deterministically for consistent query generation.
        
        Strategy:
        1. Sort by target table name
        2. Preserve within-table order
        """
        parsed_joins = []
        for join_sql in joins:
            parsed = JoinManager.parse_join_clause(join_sql)
            if parsed:
                parsed['original'] = join_sql
                parsed_joins.append(parsed)
            else:
                parsed_joins.append({'original': join_sql, 'table': 'ZZZZZ'})
        
        # Sort by table name
        sorted_joins = sorted(parsed_joins, key=lambda x: x.get('table', 'ZZZZZ'))
        
        return [j['original'] for j in sorted_joins]


# ============================================================================
# QUERY VALIDATION
# ============================================================================

class QueryValidator:
    """Validates assembled SQL queries."""
    
    # Snowflake reserved keywords (partial list)
    RESERVED_KEYWORDS = {
        'SELECT', 'FROM', 'WHERE', 'GROUP', 'ORDER', 'HAVING',
        'JOIN', 'INNER', 'LEFT', 'RIGHT', 'FULL', 'CROSS',
        'ON', 'AS', 'AND', 'OR', 'NOT', 'IN', 'BETWEEN',
        'LIKE', 'NULL', 'IS', 'DISTINCT', 'ALL', 'ANY',
    }
    
    @staticmethod
    def validate_select_clause(select_clause: str) -> Tuple[bool, Optional[str]]:
        """Validate SELECT clause structure."""
        if not select_clause or not select_clause.strip():
            return False, "SELECT clause is empty"
        
        # Check for balanced parentheses
        if select_clause.count('(') != select_clause.count(')'):
            return False, "Unbalanced parentheses in SELECT"
        
        return True, None
    
    @staticmethod
    def validate_joins(joins: List[str]) -> Tuple[bool, Optional[str]]:
        """Validate JOIN clauses."""
        if not joins:
            return True, None  # No joins is valid
        
        for join_sql in joins:
            # Check for JOIN keyword
            if 'JOIN' not in join_sql.upper():
                return False, f"Invalid join syntax: {join_sql}"
            
            # Check for ON condition
            if ' ON ' not in join_sql.upper():
                return False, f"JOIN missing ON condition: {join_sql}"
            
            # Check for balanced parentheses
            if join_sql.count('(') != join_sql.count(')'):
                return False, f"Unbalanced parentheses in join: {join_sql}"
        
        return True, None
    
    @staticmethod
    def validate_group_by(group_by_clause: str) -> Tuple[bool, Optional[str]]:
        """Validate GROUP BY clause."""
        if not group_by_clause or not group_by_clause.strip():
            return True, None  # GROUP BY is optional
        
        # Should contain table.column references
        if '.' not in group_by_clause:
            return False, "GROUP BY should use table.column format"
        
        return True, None
    
    @staticmethod
    def validate_full_query(full_sql: str) -> Tuple[bool, Optional[str]]:
        """Validate complete SQL query."""
        if not full_sql or not full_sql.strip():
            return False, "SQL is empty"
        
        sql_upper = full_sql.upper()
        
        # Must start with SELECT
        if not sql_upper.lstrip().startswith('SELECT'):
            return False, "Query must start with SELECT"
        
        # Must have FROM
        if ' FROM ' not in sql_upper:
            return False, "Query missing FROM clause"
        
        # Check for balanced parentheses
        if full_sql.count('(') != full_sql.count(')'):
            return False, "Unbalanced parentheses in query"
        
        return True, None
    
    @staticmethod
    def validate_entire_query(
        select_clause: str,
        joins: List[str],
        group_by_clause: Optional[str] = None
    ) -> Tuple[bool, List[str]]:
        """
        Comprehensive query validation.
        
        Returns:
            (is_valid, list_of_errors)
        """
        errors = []
        
        # Validate select
        valid, error = QueryValidator.validate_select_clause(select_clause)
        if not valid:
            errors.append(f"SELECT: {error}")
        
        # Validate joins
        valid, error = QueryValidator.validate_joins(joins)
        if not valid:
            errors.append(f"JOIN: {error}")
        
        # Validate GROUP BY
        if group_by_clause:
            valid, error = QueryValidator.validate_group_by(group_by_clause)
            if not valid:
                errors.append(f"GROUP BY: {error}")
        
        return len(errors) == 0, errors


# ============================================================================
# QUERY BUILDER
# ============================================================================

class QueryBuilder:
    """Builds complete SQL queries from metrics."""
    
    def __init__(self, base_table: str = "SalesFact", base_alias: str = "fact"):
        """
        Initialize query builder.
        
        Args:
            base_table: Base table for FROM clause
            base_alias: Alias for base table
        """
        self.base_table = base_table
        self.base_alias = base_alias
        self.join_manager = JoinManager()
        self.validator = QueryValidator()
    
    def build_select_clause(self, metrics: List[TranslatedMetric]) -> str:
        """
        Build SELECT clause from multiple metrics.
        
        Args:
            metrics: List of translated metrics
            
        Returns:
            SELECT clause (without SELECT keyword)
        """
        if not metrics:
            raise ValueError("Must provide at least one metric")
        
        select_items = []
        for metric in metrics:
            if not metric.is_success:
                logger.warning(f"Skipping failed metric: {metric.name}")
                continue
            
            select_items.append(metric.to_select_clause())
        
        if not select_items:
            raise ValueError("All metrics failed translation")
        
        # Format with nice indentation
        return ",\n  ".join(select_items)
    
    def merge_joins(self, metrics: List[TranslatedMetric]) -> List[str]:
        """
        Merge joins from all metrics.
        
        Args:
            metrics: List of translated metrics
            
        Returns:
            Deduplicated and ordered list of JOIN clauses
        """
        all_joins = []
        
        for metric in metrics:
            if metric.joins:
                all_joins.extend(metric.joins)
        
        if not all_joins:
            return []
        
        logger.debug(f"Merging {len(all_joins)} joins from metrics...")
        
        # Deduplicate
        deduped = self.join_manager.deduplicate_joins(all_joins)
        logger.debug(f"  After deduplication: {len(deduped)} joins")
        
        # Order deterministically
        ordered = self.join_manager.order_joins_deterministic(deduped)
        
        return ordered
    
    def build_from_clause(self) -> str:
        """Build FROM clause using base table."""
        return f"{self.base_table} AS {self.base_alias}"
    
    def build_group_by_clause(
        self,
        dimensions: Optional[List[DimensionField]] = None
    ) -> Optional[str]:
        """
        Build GROUP BY clause if dimensions provided.
        
        Args:
            dimensions: List of dimension fields for grouping
            
        Returns:
            GROUP BY clause or None
        """
        if not dimensions:
            return None
        
        group_by_items = [d.to_group_by() for d in dimensions]
        return ",\n  ".join(group_by_items)
    
    def assemble_query(
        self,
        metrics: List[TranslatedMetric],
        dimensions: Optional[List[DimensionField]] = None,
        where_clause: Optional[str] = None,
        order_by_clause: Optional[str] = None,
    ) -> AssembledQuery:
        """
        Assemble complete query from components.
        
        Args:
            metrics: Translated metrics to include
            dimensions: Optional dimension fields for GROUP BY
            where_clause: Optional WHERE clause
            order_by_clause: Optional ORDER BY clause
            
        Returns:
            AssembledQuery with complete SQL
            
        Raises:
            ValueError: If metrics list is empty or invalid
        """
        # Build components
        select_clause = self.build_select_clause(metrics)
        from_clause = self.build_from_clause()
        joins = self.merge_joins(metrics)
        group_by_clause = self.build_group_by_clause(dimensions)
        
        # Create assembled query
        query = AssembledQuery(
            select_clause=select_clause,
            from_clause=from_clause,
            joins=joins,
            group_by_clause=group_by_clause,
            where_clause=where_clause,
            order_by_clause=order_by_clause,
        )
        
        # Validate
        is_valid, errors = self.validator.validate_entire_query(
            select_clause,
            joins,
            group_by_clause
        )
        
        if not is_valid:
            error_msg = "; ".join(errors)
            raise ValueError(f"Query validation failed: {error_msg}")
        
        logger.info(f"✓ Query assembled successfully")
        logger.info(f"  ├─ Metrics: {len([m for m in metrics if m.is_success])}")
        logger.info(f"  ├─ Joins: {len(joins)}")
        logger.info(f"  ├─ Dimensions: {len(dimensions) if dimensions else 0}")
        logger.info(f"  └─ Valid: Yes")
        
        return query


# ============================================================================
# QUERY ASSEMBLER (HIGH-LEVEL INTERFACE)
# ============================================================================

class QueryAssembler:
    """High-level interface for query assembly."""
    
    def __init__(self, base_table: str = "SalesFact", base_alias: str = "fact"):
        """Initialize query assembler."""
        self.builder = QueryBuilder(base_table, base_alias)
        self.validator = QueryValidator()
    
    def assemble_metrics_query(
        self,
        metrics: List[TranslatedMetric],
        dimensions: Optional[List[DimensionField]] = None,
        where_clause: Optional[str] = None,
        order_by_clause: Optional[str] = None,
    ) -> AssembledQuery:
        """
        Assemble SQL query from metrics (main entry point).
        
        Args:
            metrics: List of translated metrics
            dimensions: Optional dimension fields
            where_clause: Optional WHERE clause
            order_by_clause: Optional ORDER BY clause
            
        Returns:
            Complete AssembledQuery
        """
        logger.info(f"Assembling query with {len(metrics)} metric(s)...")
        
        try:
            query = self.builder.assemble_query(
                metrics=metrics,
                dimensions=dimensions,
                where_clause=where_clause,
                order_by_clause=order_by_clause,
            )
            
            logger.debug(f"\nFinal SQL:\n{query.full_sql}")
            
            return query
            
        except Exception as e:
            logger.error(f"✗ Query assembly failed: {str(e)}")
            raise
    
    def assemble_from_dict(
        self,
        metrics_dict: Dict,
        dimensions: Optional[List[Dict]] = None,
        **kwargs
    ) -> AssembledQuery:
        """
        Assemble query from dictionary representation.
        
        Args:
            metrics_dict: Dictionary of metric_name → TranslatedMetric
            dimensions: Optional list of dimension dictionaries
            
        Returns:
            Complete AssembledQuery
        """
        # Convert to TranslatedMetric objects if needed
        metrics = []
        for name, metric in metrics_dict.items():
            if isinstance(metric, dict):
                metrics.append(TranslatedMetric(
                    name=metric.get('name', name),
                    dax_expression=metric.get('dax_expression', ''),
                    sql_expression=metric.get('sql_expression', ''),
                    joins=metric.get('joins', []),
                    tables_referenced=metric.get('tables_referenced', []),
                ))
            else:
                metrics.append(metric)
        
        # Convert dimensions if provided
        dim_objects = []
        if dimensions:
            for dim in dimensions:
                if isinstance(dim, dict):
                    dim_objects.append(DimensionField(
                        table=dim['table'],
                        column=dim['column'],
                        alias=dim.get('alias', f"{dim['table']}_{dim['column']}")
                    ))
                else:
                    dim_objects.append(dim)
        
        return self.assemble_metrics_query(
            metrics=metrics,
            dimensions=dim_objects or None,
            **kwargs
        )
    
    def log_query_summary(self, query: AssembledQuery) -> None:
        """Log detailed query summary."""
        logger.info("\n" + "="*80)
        logger.info("QUERY ASSEMBLY SUMMARY")
        logger.info("="*80)
        logger.info(f"\nSELECT Components:")
        for item in query.select_clause.split(",\n"):
            logger.info(f"  + {item.strip()}")
        logger.info(f"\nFROM: {query.from_clause}")
        if query.joins:
            logger.info(f"\nJOINS ({len(query.joins)}):")
            for join in query.joins:
                logger.info(f"  + {join[:70]}...")
        if query.group_by_clause:
            logger.info(f"\nGROUP BY:")
            for item in query.group_by_clause.split(",\n"):
                logger.info(f"  + {item.strip()}")
        if query.where_clause:
            logger.info(f"\nWHERE: {query.where_clause}")
        if query.order_by_clause:
            logger.info(f"\nORDER BY: {query.order_by_clause}")
        logger.info("\n" + "="*80)
