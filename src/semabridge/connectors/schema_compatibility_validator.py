"""
Schema compatibility validator for Snowflake tables.

Ensures that existing tables match the schema expectations of the semantic model,
including join columns, measure columns, and data types. This is the foundation
for safe table lifecycle management — check before create/replace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Set, Any
import logging

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SchemaCompatibilityResult:
    """Result of schema compatibility validation."""
    is_compatible: bool
    table_exists: bool
    missing_join_columns: List[str] = field(default_factory=list)
    missing_measure_columns: List[str] = field(default_factory=list)
    type_mismatches: List[str] = field(default_factory=list)
    extra_columns: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def error_message(self) -> str:
        """Generate a human-readable error message."""
        lines = ["Schema compatibility issues found:"]
        
        if self.missing_join_columns:
            lines.append(f"  Missing join columns: {', '.join(self.missing_join_columns)}")
        
        if self.missing_measure_columns:
            lines.append(f"  Missing measure columns: {', '.join(self.missing_measure_columns)}")
        
        if self.type_mismatches:
            for mismatch in self.type_mismatches:
                lines.append(f"  Type mismatch: {mismatch}")
        
        if self.extra_columns:
            lines.append(f"  Extra columns in table: {', '.join(self.extra_columns)}")
        
        if self.warnings:
            for warning in self.warnings:
                lines.append(f"  ⚠️  {warning}")
        
        return "\n".join(lines)


class SchemaCompatibilityValidator:
    """
    Validates Snowflake table schemas against semantic model expectations.
    
    This validator checks:
    1. Table existence
    2. Required join columns (from relationships)
    3. Required measure columns (from metrics)
    4. Data type compatibility for join operations
    5. Warns on structural differences (extra columns)
    
    Usage:
        validator = SchemaCompatibilityValidator(cursor, config)
        result = validator.validate_table(
            table_name="CUSTOMERS",
            join_columns={"CUSTOMER_ID", "REGION"},
            measure_columns={"LIFETIME_VALUE", "PURCHASE_COUNT"},
            join_column_types={"CUSTOMER_ID": "INTEGER", "REGION": "VARCHAR"}
        )
        if not result.is_compatible:
            raise ConnectorError(result.error_message())
    """
    
    def __init__(self, cursor, config):
        """
        Initialize validator.
        
        Args:
            cursor: Snowflake cursor for metadata queries
            config: Semabridge configuration with database/schema
        """
        self.cursor = cursor
        self.config = config
    
    def validate_table(
        self,
        table_name: str,
        join_columns: Optional[Set[str]] = None,
        measure_columns: Optional[Set[str]] = None,
        join_column_types: Optional[Dict[str, str]] = None,
        schema: Optional[str] = None
    ) -> SchemaCompatibilityResult:
        """
        Validate that an existing table is compatible with model expectations.
        
        Args:
            table_name: Name of the table to validate
            join_columns: Set of column names used in relationships
            measure_columns: Set of column names used in measures
            join_column_types: Dict mapping join columns to expected Snowflake types
            schema: Schema name (defaults to config.schema_name)
        
        Returns:
            SchemaCompatibilityResult with compatibility status and details
        """
        schema = schema or self.config.schema_name
        join_columns = join_columns or set()
        measure_columns = measure_columns or set()
        join_column_types = join_column_types or {}
        
        # Check table existence
        table_exists = self._table_exists(table_name, schema)
        if not table_exists:
            return SchemaCompatibilityResult(
                is_compatible=False,
                table_exists=False
            )
        
        # Get actual table columns and types
        actual_columns = self._get_table_columns(table_name, schema)
        
        # Normalize names (Snowflake is case-insensitive)
        actual_columns_upper = {name.upper(): dtype for name, dtype in actual_columns.items()}
        join_columns_upper = {col.upper() for col in join_columns}
        measure_columns_upper = {col.upper() for col in measure_columns}
        
        # Check for missing columns
        missing_joins = join_columns_upper - set(actual_columns_upper.keys())
        missing_measures = measure_columns_upper - set(actual_columns_upper.keys())
        
        # Check for type mismatches on join columns
        type_mismatches = []
        for join_col, expected_type in join_column_types.items():
            join_col_upper = join_col.upper()
            if join_col_upper in actual_columns_upper:
                actual_type = actual_columns_upper[join_col_upper]
                if not self._types_compatible(expected_type, actual_type):
                    type_mismatches.append(
                        f"{join_col}: expected {expected_type}, got {actual_type}"
                    )
        
        # Warn on extra columns
        expected_columns = join_columns_upper | measure_columns_upper
        extra_columns = set(actual_columns_upper.keys()) - expected_columns
        
        # Determine overall compatibility
        is_compatible = not (missing_joins or missing_measures or type_mismatches)
        
        result = SchemaCompatibilityResult(
            is_compatible=is_compatible,
            table_exists=True,
            missing_join_columns=sorted(missing_joins),
            missing_measure_columns=sorted(missing_measures),
            type_mismatches=type_mismatches,
            extra_columns=sorted(extra_columns)
        )
        
        # Add warnings for extra columns (not blocking)
        if extra_columns:
            result.warnings.append(
                f"Table has {len(extra_columns)} extra columns not in the model. "
                "They will be ignored."
            )
        
        return result
    
    def _table_exists(self, table_name: str, schema: str) -> bool:
        """Check if a table exists in the given schema."""
        try:
            query = f"""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = '{schema.upper()}'
              AND TABLE_NAME = '{table_name.upper()}'
              AND TABLE_TYPE = 'BASE TABLE'
            """
            self.cursor.execute(query)
            result = self.cursor.fetchone()
            return result[0] > 0 if result else False
        except Exception as e:
            logger.warning(f"Error checking if table {schema}.{table_name} exists: {e}")
            return False
    
    def _get_table_columns(self, table_name: str, schema: str) -> Dict[str, str]:
        """
        Get all columns and their types from a table.
        
        Returns:
            Dict mapping column name to Snowflake data type
        """
        try:
            query = f"""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{schema.upper()}'
              AND TABLE_NAME = '{table_name.upper()}'
            ORDER BY ORDINAL_POSITION
            """
            self.cursor.execute(query)
            return {row[0]: row[1] for row in self.cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting columns for {schema}.{table_name}: {e}")
            raise
    
    @staticmethod
    def _types_compatible(expected: str, actual: str) -> bool:
        """
        Check if actual Snowflake type is compatible with expected type.
        
        Handles type aliases and variations (e.g., INTEGER ↔ INT, VARCHAR ↔ TEXT).
        
        Args:
            expected: Expected Snowflake type from model
            actual: Actual Snowflake type in table
        
        Returns:
            True if types are compatible
        """
        # Normalize type names
        def normalize(t: str) -> str:
            t = t.upper().strip()
            # Remove size specifiers (VARCHAR(500) -> VARCHAR, NUMERIC(10,2) -> NUMERIC)
            if '(' in t:
                t = t.split('(')[0]
            # Handle common aliases
            aliases = {
                'INT': 'INTEGER',
                'NUMERIC': 'DECIMAL',
                'REAL': 'FLOAT',
                'DOUBLE': 'FLOAT',
                'TEXT': 'VARCHAR',
                'BOOL': 'BOOLEAN',
            }
            return aliases.get(t, t)
        
        expected_norm = normalize(expected)
        actual_norm = normalize(actual)
        
        # Exact match
        if expected_norm == actual_norm:
            return True
        
        # Compatible numeric types
        numeric_types = {'INTEGER', 'DECIMAL', 'FLOAT', 'NUMBER'}
        if expected_norm in numeric_types and actual_norm in numeric_types:
            return True
        
        # Compatible string types
        string_types = {'VARCHAR', 'TEXT', 'STRING'}
        if expected_norm in string_types and actual_norm in string_types:
            return True
        
        # Compatible datetime types
        datetime_types = {'DATE', 'TIMESTAMP', 'TIMESTAMP_NTZ', 'TIMESTAMP_LTZ', 'TIMESTAMP_TZ', 'TIME'}
        if expected_norm in datetime_types and actual_norm in datetime_types:
            return True
        
        return False


def validate_table_exists_and_compatible(
    cursor,
    table_name: str,
    schema: str,
    config,
    join_columns: Optional[Set[str]] = None,
    measure_columns: Optional[Set[str]] = None,
    join_column_types: Optional[Dict[str, str]] = None
) -> SchemaCompatibilityResult:
    """
    Convenience function to validate table compatibility.
    
    Args:
        cursor: Snowflake cursor
        table_name: Table to validate
        schema: Schema name
        config: Semabridge config
        join_columns: Join column names from relationships
        measure_columns: Measure column names from metrics
        join_column_types: Expected types for join columns
    
    Returns:
        SchemaCompatibilityResult
    """
    validator = SchemaCompatibilityValidator(cursor, config)
    return validator.validate_table(
        table_name=table_name,
        join_columns=join_columns,
        measure_columns=measure_columns,
        join_column_types=join_column_types,
        schema=schema
    )
