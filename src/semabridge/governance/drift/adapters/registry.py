"""Adapter Registry for Drift Monitoring.

Maps project platform and execution mode (live vs. mock) to the corresponding
PlatformAdapter instance. Platform is strictly resolved from the project configuration.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Type

from semabridge.governance.drift.adapters.base import PlatformAdapter
from semabridge.governance.drift.adapters.databricks import DatabricksAdapter
from semabridge.governance.drift.adapters.snowflake import SnowflakeAdapter

logger = logging.getLogger("semabridge.governance.drift.adapters.registry")

_REGISTRY: Dict[str, Type[PlatformAdapter]] = {
    "SNOWFLAKE": SnowflakeAdapter,
    "DATABRICKS": DatabricksAdapter,
}


def is_platform_supported(platform: str) -> bool:
    """Check if the given warehouse platform is supported for drift monitoring."""
    if not platform:
        return False
    return platform.upper().strip() in _REGISTRY


def list_supported_platforms() -> List[str]:
    """Return list of supported warehouse platforms."""
    return sorted(list(_REGISTRY.keys()))


def is_mock_allowed() -> bool:
    """Return whether mock mode is enabled via configuration."""
    raw = os.getenv("SEMABRIDGE_DRIFT_MOCK_ALLOWED", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def resolve_project_platform(project_config: Optional[Dict[str, Any]]) -> str:
    """Resolve target platform from ONE field of the project record (no metadata guessing)."""
    if not project_config or not isinstance(project_config, dict):
        return "NOT_SET"
    target = project_config.get("target") or {}
    if isinstance(target, dict):
        raw = target.get("platform") or target.get("type") or target.get("target_type") or ""
        if raw:
            plat = str(raw).strip().upper()
            if plat not in ("UNKNOWN", "NONE", "NULL", ""):
                return plat
    targets = project_config.get("targets")
    if isinstance(targets, list) and targets and isinstance(targets[0], dict):
        raw = targets[0].get("platform") or targets[0].get("type") or targets[0].get("target_type") or ""
        if raw:
            plat = str(raw).strip().upper()
            if plat not in ("UNKNOWN", "NONE", "NULL", ""):
                return plat
    raw = (
        project_config.get("platform")
        or project_config.get("target_platform")
        or project_config.get("target_warehouse")
        or project_config.get("warehouse_type")
        or project_config.get("target_type")
        or ""
    )
    if raw:
        plat = str(raw).strip().upper()
        if plat not in ("UNKNOWN", "NONE", "NULL", ""):
            return plat
    return "NOT_SET"


def get_adapter(platform: str, mode: str = "live") -> PlatformAdapter:
    """Resolve and instantiate the appropriate platform adapter.

    Args:
        platform: Warehouse platform name (e.g. SNOWFLAKE, DATABRICKS).
        mode: Execution mode ('live' or 'mock').

    Returns:
        An instance conforming to PlatformAdapter.

    Raises:
        PermissionError: If mode is mock but mock mode is disabled.
        ValueError: If the platform is not supported.
    """
    plat_key = str(platform or "").upper().strip()
    mode_key = str(mode or "live").lower().strip()

    if mode_key == "mock":
        if not is_mock_allowed():
            raise PermissionError("Mock mode is disabled in this environment")
        from semabridge.governance.drift.adapters.fixture import RecordedFixtureAdapter

        return RecordedFixtureAdapter(platform=plat_key)

    if plat_key not in _REGISTRY:
        raise ValueError(f"Platform '{platform}' is not supported for drift monitoring")

    adapter_cls = _REGISTRY[plat_key]
    return adapter_cls()
