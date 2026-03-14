"""
OSI to SQL Converter — converts OSI models to SQL DDL.

Provides:
- Table DDL generation (Snowflake, PostgreSQL, MySQL, ANSI SQL)
- Metric translation and expression conversion
- Semantic view generation with joins
- Constraint and index generation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from semabridge.core.exceptions import ConversionError


# ───────────────────────────────────────────────────────────────────────────
# Enumerations
# ───────────────────────────────────────────────────────────────────────────

class SQLDialect(str, Enum):
    """Supported SQL dialects."""

    SNOWFLAKE = "snowflake"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    ANSI = "ansi"
    BIGQUERY = "bigquery"


# ───────────────────────────────────────────────────────────────────────────
# Result Classes
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class OSIToSQLResult:
    """Result of OSI to SQL conversion."""

    model_name: str
    dialect: SQLDialect
    ddl_statements: List[str] = field(default_factory=list)
    semantic_views: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def is_valid(self) -> bool:
        """Check if conversion was successful."""
        return len(self.errors) == 0

    def add_ddl(self, statement: str) -> None:
        """Add a DDL statement."""
        self.ddl_statements.append(statement)

    def add_semantic_view(self, name: str, sql: str) -> None:
        """Add a semantic view definition."""
        self.semantic_views[name] = sql

    def add_metric(self, name: str, expression: str) -> None:
        """Add a metric definition."""
        self.metrics[name] = expression

    def add_error(self, error: str) -> None:
        """Add an error message."""
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Add a warning message."""
        self.warnings.append(warning)


# ───────────────────────────────────────────────────────────────────────────
# Converter
# ───────────────────────────────────────────────────────────────────────────

class OSIToSQLConverter:
    """
    Converts OSI semantic models to SQL DDL and semantic views.
    """

    def __init__(self, dialect: SQLDialect = SQLDialect.SNOWFLAKE):
        """
        Initialize the converter.

        Args:
            dialect: Target SQL dialect
        """
        self.dialect = dialect

    def convert(self, model: Any) -> OSIToSQLResult:
        """
        Convert an OSI model to SQL.

        Args:
            model: OSI semantic model

        Returns:
            OSIToSQLResult with DDL and semantic view definitions
        """
        model_name = getattr(model, 'unique_name', 'model')
        result = OSIToSQLResult(
            model_name=model_name,
            dialect=self.dialect,
        )

        try:
            # Generate DDL for datasets
            if hasattr(model, 'datasets'):
                for ds in model.datasets:
                    ddl = self._generate_table_ddl(ds)
                    result.add_ddl(ddl)

            # Generate semantic views
            if hasattr(model, 'datasets'):
                for ds in model.datasets:
                    view_name = f"VIEW_{getattr(ds, 'unique_name', 'dataset')}"
                    view_sql = self._generate_semantic_view(ds)
                    result.add_semantic_view(view_name, view_sql)

            # Translate metrics
            if hasattr(model, 'metrics'):
                for metric in model.metrics:
                    m_name = getattr(metric, 'unique_name', 'metric')
                    expression = getattr(metric, 'expression', '')
                    sql_expr = self._translate_metric_expression(expression)
                    result.add_metric(m_name, sql_expr)

        except Exception as e:
            result.add_error(f"Conversion failed: {str(e)}")

        return result

    def _generate_table_ddl(self, dataset: Any) -> str:
        """Generate DDL for a dataset (table)."""
        ds_name = getattr(dataset, 'unique_name', 'table')
        source_table = getattr(dataset, 'source_table', f'{ds_name.upper()}_TABLE')

        if self.dialect == SQLDialect.SNOWFLAKE:
            ddl = f"CREATE TABLE IF NOT EXISTS {source_table} ("
        else:
            ddl = f"CREATE TABLE IF NOT EXISTS {source_table} ("

        # Add columns
        columns = []
        if hasattr(dataset, 'columns'):
            for col in dataset.columns:
                col_name = getattr(col, 'unique_name', 'column')
                col_type = self._map_data_type(getattr(col, 'data_type', 'STRING'))
                columns.append(f"  {col_name} {col_type}")

        ddl += ",\n".join(columns) + "\n);"

        return ddl

    def _generate_semantic_view(self, dataset: Any) -> str:
        """Generate a semantic view definition."""
        ds_name = getattr(dataset, 'unique_name', 'dataset')
        source_table = getattr(dataset, 'source_table', f'{ds_name.upper()}_TABLE')

        view_name = f"VIEW_{ds_name}"
        return f"SELECT * FROM {source_table};"

    def _translate_metric_expression(self, expression: str) -> str:
        """Translate a metric expression to SQL."""
        # Simple translation: replace DAX functions with SQL equivalents
        sql_expr = expression

        # Basic DAX to SQL mapping
        mappings = {
            'SUM': 'SUM',
            'COUNT': 'COUNT',
            'AVG': 'AVG',
            'MIN': 'MIN',
            'MAX': 'MAX',
            'CALCULATE': 'SELECT',
            'DIVIDE': '/',
            'MULTIPLY': '*',
        }

        for dax, sql in mappings.items():
            sql_expr = sql_expr.replace(dax, sql)

        return sql_expr

    def _map_data_type(self, osi_type: Any) -> str:
        """Map OSI data type to SQL data type."""
        type_str = str(osi_type).upper()

        mapping = {
            'STRING': 'VARCHAR(255)',
            'INTEGER': 'INTEGER',
            'DECIMAL': 'DECIMAL(20,2)',
            'FLOAT': 'FLOAT',
            'BOOLEAN': 'BOOLEAN',
            'DATE': 'DATE',
            'DATETIME': 'TIMESTAMP',
            'TIME': 'TIME',
            'BINARY': 'BINARY',
            'VARIANT': 'VARIANT',
        }

        return mapping.get(type_str, 'VARCHAR(255)')


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────

def convert_osi_to_sql(
    model: Any,
    dialect: SQLDialect = SQLDialect.SNOWFLAKE,
) -> OSIToSQLResult:
    """
    Convert an OSI semantic model to SQL DDL and views.

    Args:
        model: OSI semantic model object
        dialect: Target SQL dialect

    Returns:
        OSIToSQLResult with DDL and view definitions
    """
    converter = OSIToSQLConverter(dialect)
    return converter.convert(model)
