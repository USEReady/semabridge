"""Base class and factory for BI Truth Window Builders."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple


class WindowBuilder(ABC):
    platform: str = "UNKNOWN"

    @abstractmethod
    def supports_windows(self) -> Tuple[bool, str]:
        """Return (supported, reason) indicating whether this platform supports window filtering."""
        raise NotImplementedError

    @abstractmethod
    def build(self, sql: Optional[str], window: Any) -> Optional[str]:
        """Return the SQL narrowed to a window, or None when it cannot be narrowed."""
        raise NotImplementedError


# Backward-compatible alias
TruthWindowBuilder = WindowBuilder


def get_window_builder(platform: Optional[str] = None) -> Optional[WindowBuilder]:
    """Return the WindowBuilder for the target platform, or None for unknown platforms."""
    plat = (platform or "").upper().strip()
    if plat == "DATABRICKS":
        from semabridge.ai.window_builders.databricks_windows import DatabricksWindowBuilder
        return DatabricksWindowBuilder()
    if plat == "SNOWFLAKE":
        from semabridge.ai.window_builders.snowflake_windows import SnowflakeWindowBuilder
        return SnowflakeWindowBuilder()
    return None
