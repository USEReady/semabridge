"""Connector factory.

Central place to instantiate connectors by type name.
Services call make_source_extractor() or make_target_emitter() instead of
importing and instantiating connector classes directly. This makes it easy
to swap implementations and mock connectors in tests.
"""
from __future__ import annotations
import os
from typing import Any


def make_source_extractor(connector_type: str, config: Any):
    """Return a source extractor for the given connector type.

    Args:
        connector_type: One of 'snowflake', 'fabric', 'databricks', 'tableau'
        config: The platform-specific config object (SnowflakeConfig, FabricConfig, etc.)

    Returns:
        An instance implementing extraction methods

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
    if ct in ("tableau", "tds", "twb", "tableau_datasource"):
        from semabridge.connectors.tableau_connector import TableauConnector
        raw = (
            getattr(config, "path", None)
            or getattr(config, "file_path", None)
            or (config if isinstance(config, (str, bytes)) else "")
        )
        return TableauConnector(raw)
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
    if ct in ("snowflake", "snowflake_semantic_view"):
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        return SnowflakeEmitter(config)
    if ct in ("fabric", "powerbi", "power_bi"):
        from semabridge.connectors.fabric_publisher import FabricPublisher
        return FabricPublisher(config)
    if ct in ("databricks", "databricks_metric_view"):
        from semabridge.connectors.databricks_publisher import DatabricksPublisher
        from semabridge.core.settings import DatabricksConfig
        if isinstance(config, DatabricksConfig):
            return DatabricksPublisher(config)

        if hasattr(config, "model_dump"):
            cfg_dict = config.model_dump()
        elif isinstance(config, dict):
            cfg_dict = config
        else:
            cfg_dict = getattr(config, "__dict__", {})

        host = cfg_dict.get("host") or os.getenv("DATABRICKS_HOST", "adb-mock.azuredatabricks.net")
        warehouse_id = cfg_dict.get("warehouse_id") or os.getenv("DATABRICKS_WAREHOUSE_ID", "default_warehouse")
        catalog = cfg_dict.get("catalog") or cfg_dict.get("database") or "main"
        schema_name = cfg_dict.get("schema_name") or cfg_dict.get("schema") or "semabridge"

        dbx_cfg = DatabricksConfig(
            host=host,
            warehouse_id=warehouse_id,
            catalog=catalog,
            schema_name=schema_name,
        )
        return DatabricksPublisher(dbx_cfg)
    raise ValueError(f"Unknown target connector type: {connector_type!r}")

