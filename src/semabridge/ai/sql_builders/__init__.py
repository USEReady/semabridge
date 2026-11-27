"""Truth SQL Builders Package.

Two SQL surfaces exist because Snowflake and Databricks expose governed semantic models
through incompatible query syntaxes:
- ``SNOWFLAKE`` (``SnowflakeTruthSQLBuilder``): emits ``SELECT * FROM SEMANTIC_VIEW(view METRICS m DIMENSIONS d)``
  with double-quoted identifiers.
- ``DATABRICKS`` (``DatabricksTruthSQLBuilder``): emits ``SELECT MEASURE(`m`) FROM `cat`.`sch`.`view` ``
  and ``SELECT `d`, MEASURE(`m`) FROM `cat`.`sch`.`view` GROUP BY `d` `` with backticked identifiers
  and deployed projection-prefixed dimension names.

Callers resolve ``get_builder(platform)`` once per project; unknown platforms return ``None``.
"""
from __future__ import annotations

from semabridge.ai.sql_builders.base import TruthSQLBuilder, get_builder
from semabridge.ai.sql_builders.snowflake_builder import SnowflakeTruthSQLBuilder
from semabridge.ai.sql_builders.databricks_builder import DatabricksTruthSQLBuilder

__all__ = [
    "TruthSQLBuilder",
    "SnowflakeTruthSQLBuilder",
    "DatabricksTruthSQLBuilder",
    "get_builder",
]
