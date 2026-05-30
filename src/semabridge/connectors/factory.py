"""Connector factory.

Central place to instantiate connectors by type name.
Services call make_source_extractor() or make_target_emitter() instead of
importing and instantiating connector classes directly. This makes it easy
to swap implementations and mock connectors in tests.
"""
from __future__ import annotations
from typing import Any


def make_source_extractor(connector_type: str, config: Any):
    """Return a source extractor for the given connector type.

    Args:
        connector_type: One of 'snowflake', 'fabric', 'databricks'
        config: The platform-specific config object (SnowflakeConfig, FabricConfig, etc.)

    Returns:
        An instance implementing BaseExtractor

    Raises:
        ValueError: If connector_type is not recognised
    """
    ct = str(connector_type or "").strip().lower()
    if ct == "snowflake":
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        return SnowflakeExtractor(config)
    if ct in ("fabric", "powerbi", "power_bi"):
        from semabridge.connectors.fabric_extractor import FabricExtractor
        return FabricExtractor(config)
    if ct == "databricks":
        from semabridge.connectors.databricks_publisher import DatabricksPublisher
        return DatabricksPublisher(config)
    raise ValueError(f"Unknown source connector type: {connector_type!r}")


def make_target_emitter(connector_type: str, config: Any):
    """Return a target emitter for the given connector type.

    Args:
        connector_type: One of 'snowflake', 'fabric', 'databricks'
        config: The platform-specific config object

    Returns:
        An instance implementing BaseEmitter

    Raises:
        ValueError: If connector_type is not recognised
    """
    ct = str(connector_type or "").strip().lower()
    if ct == "snowflake":
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        return SnowflakeEmitter(config)
    if ct in ("fabric", "powerbi", "power_bi"):
        from semabridge.connectors.fabric_publisher import FabricPublisher
        return FabricPublisher(config)
    if ct == "databricks":
        from semabridge.connectors.databricks_publisher import DatabricksPublisher
        return DatabricksPublisher(config)
    raise ValueError(f"Unknown target connector type: {connector_type!r}")
