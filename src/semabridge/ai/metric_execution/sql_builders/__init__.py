"""Target SQL Builders for Metric Comparison."""
from semabridge.ai.metric_execution.sql_builders.base import TargetSQLBuilder
from semabridge.ai.metric_execution.sql_builders.snowflake import SnowflakeMetricSQLBuilder
from semabridge.ai.metric_execution.sql_builders.databricks import DatabricksMetricSQLBuilder


def get_target_sql_builder(platform: str) -> TargetSQLBuilder:
    p = str(platform or "").strip().upper()
    if p == "SNOWFLAKE":
        return SnowflakeMetricSQLBuilder()
    elif p in ("DATABRICKS", "SPARK"):
        return DatabricksMetricSQLBuilder()
    raise ValueError(f"Unsupported target platform SQL builder: '{platform}'. Must be 'SNOWFLAKE' or 'DATABRICKS'.")


__all__ = [
    "TargetSQLBuilder",
    "SnowflakeMetricSQLBuilder",
    "DatabricksMetricSQLBuilder",
    "get_target_sql_builder",
]
