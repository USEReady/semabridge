"""Window builders for BI Truth Suite across platforms."""
from __future__ import annotations

from semabridge.ai.window_builders.base import TruthWindowBuilder, WindowBuilder, get_window_builder
from semabridge.ai.window_builders.snowflake_windows import SnowflakeTruthWindowBuilder, SnowflakeWindowBuilder
from semabridge.ai.window_builders.databricks_windows import DatabricksTruthWindowBuilder, DatabricksWindowBuilder

__all__ = [
    "WindowBuilder",
    "TruthWindowBuilder",
    "SnowflakeWindowBuilder",
    "SnowflakeTruthWindowBuilder",
    "DatabricksWindowBuilder",
    "DatabricksTruthWindowBuilder",
    "get_window_builder",
]
