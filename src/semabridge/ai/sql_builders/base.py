"""Truth SQL Builder Base Interface and Factory."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Union


class TruthSQLBuilder(ABC):
    """Abstract base class for platform-specific truth SQL builders."""

    platform: str = "UNKNOWN"

    @abstractmethod
    def quote_identifier(self, name: str) -> str:
        """Quote a single identifier or dotted identifier according to platform syntax."""
        raise NotImplementedError

    @abstractmethod
    def build_total(self, view: str, metric: str) -> str:
        """Build scalar aggregate query across all rows."""
        raise NotImplementedError

    @abstractmethod
    def build_sliced(self, view: str, metric: str, dimensions: Union[List[str], str]) -> str:
        """Build query sliced by one or more dimensions."""
        raise NotImplementedError

    @abstractmethod
    def build_top_n(self, view: str, metric: str, dimensions: Union[List[str], str], n: int = 5) -> str:
        """Build top-N query sliced by dimensions, ordered descending by metric, limited to N."""
        raise NotImplementedError


def get_builder(platform: str) -> Optional[TruthSQLBuilder]:
    """Return platform-specific TruthSQLBuilder instance, or None for unknown platforms."""
    plat = (platform or "").upper().strip()
    if plat == "DATABRICKS":
        from semabridge.ai.sql_builders.databricks_builder import DatabricksTruthSQLBuilder
        return DatabricksTruthSQLBuilder()
    if plat == "SNOWFLAKE":
        from semabridge.ai.sql_builders.snowflake_builder import SnowflakeTruthSQLBuilder
        return SnowflakeTruthSQLBuilder()
    return None
