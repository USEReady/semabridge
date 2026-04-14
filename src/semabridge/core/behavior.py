"""
Behavioral Configuration.

Defines the YAML schema for controlling connector behavior without changing code.
This separates "what to run" (ExecutionConfig) from "how to run it" (ConnectorBehavior).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pathlib import Path
import yaml

from pydantic import BaseModel, Field, model_validator

class SnowflakeBehavior(BaseModel):
    """Snowflake-specific behavior controls."""
    query_tag: str = Field(
        default="Semabridge_Connector",
        description="Query tag to set for all sessions"
    )
    quote_identifiers: bool = Field(
        default=True,
        description="Whether to quote all identifiers in DDL"
    )
    create_missing_tables: bool = Field(
        default=True,
        description="Auto-create source tables if missing"
    )
    validate_column_schema: bool = Field(
        default=True,
        description="Verify snowflake columns match semantic model"
    )
    use_transient_tables: bool = Field(
        default=False,
        description="Create transient tables (no fail-safe) for staging"
    )
    apply_inferred_types: bool = Field(
        default=True,
        description=(
            "Apply inferred datatypes to physical source tables via CTAS+SWAP "
            "during deploy"
        )
    )

class FabricBehavior(BaseModel):
    """Fabric/PowerBI behavior controls."""
    deploy_overwrite: bool = Field(
        default=True,
        description="Overwrite existing semantic models by default"
    )
    tmsl_generation_mode: str = Field(
        default="standard",
        description="TMSL generation strategy: 'standard' or 'compatibility'"
    )

class SemanticModelBehavior(BaseModel):
    """Semantic modeling rules."""
    view_suffix: str = Field(
        default="_SEMANTIC",
        description="Suffix for generated semantic views (automatically uppercased and prefixed with _ if needed)"
    )
    enable_date_dimension: bool = Field(
        default=True,
        description="Auto-generate date dimension if needed"
    )
    fact_detection_threshold: int = Field(
        default=1,
        description="Minimum cardinality setting, currently unused but reserved"
    )
    sync_all_attributes: bool = Field(
        default=True,
        description="Whether to include all attributes (including measure candidates and hidden columns) in Snowflake Semantic Views"
    )

class CompatibilityBehavior(BaseModel):
    """SQL compatibility fixes."""
    suppress_reserved_words: bool = Field(
        default=True,
        description="Prefix reserved words (e.g. TABLE -> L_TABLE)"
    )
    force_uppercase: bool = Field(
        default=True,
        description="Force all identifiers to uppercase"
    )

class DatabricksBehavior(BaseModel):
    """Databricks-specific deployment behavior controls."""
    create_metadata_table: bool = Field(
        default=True,
        description="Create Semabridge metadata Delta table (set false for metric-view-only deployment)"
    )
    create_measure_views: bool = Field(
        default=True,
        description="Generate measure views for deployed measures"
    )
    measure_view_type: str = Field(
        default="metric_view",
        description=(
            "View technology to use: "
            "'auto' (probe runtime, prefer metric_view), "
            "'metric_view' (force native WITH METRICS YAML), "
            "'materialized_view' (CREATE OR REPLACE MATERIALIZED VIEW with semantic TBLPROPERTIES), "
            "'sql_view' (plain CREATE OR REPLACE VIEW), "
            "'none' (metadata table only)"
        ),
    )
    measure_view_mode: str = Field(
        default="per_measure",
        description=(
            "View granularity: 'per_measure' (separate views) or "
            "'combined' (single model-level view with relationship joins when supported)"
        )
    )
    view_prefix: str = Field(
        default="mv",
        description="Prefix for generated measure view names"
    )
    enable_simple_dax_translation: bool = Field(
        default=True,
        description="Translate simple DAX patterns (SUM, COUNT, etc.) to SQL for view creation"
    )
    enable_cross_table_joins: bool = Field(
        default=False,
        description="Build explicit joins for measures that reference multiple datasets"
    )
    enable_low_confidence_drafts: bool = Field(
        default=False,
        description="Deploy LOW-confidence measures as draft placeholders instead of dropping them"
    )
    emit_metric_views_for_all_datasets: bool = Field(
        default=False,
        description="Emit a fallback metric view for datasets without deployable measures"
    )
    source_table_mapping: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Explicit mapping of Fabric dataset names to Databricks tables. "
            "Example: {'CONTINENT_1': 'analytics.raw.continent_data'}"
        ),
    )
    source_column_mapping: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional mapping of semantic columns to physical Databricks columns. "
            "Supports nested format {'Fact': {'Customer Key': 'customer_key'}} "
            "or flat format {'Fact.Customer Key': 'customer_key'}."
        ),
    )
    source_catalog: str = Field(
        default="",
        description="Default Databricks catalog for source data tables (if not mapped)"
    )
    source_schema: str = Field(
        default="",
        description="Default Databricks schema for source data tables (if not mapped)"
    )
    on_missing_source: str = Field(
        default="plan",
        description=(
            "Action when source table doesn't exist: "
            "'plan' (generate SQL, save to output/, warn), "
            "'skip' (silently skip view creation), "
            "'fail' (raise error, halt deployment)"
        ),
    )
    grant_select_to_groups: List[str] = Field(
        default_factory=list,
        description="Groups to GRANT SELECT on deployed metric views"
    )
    transfer_ownership_to: str = Field(
        default="",
        description="Group to transfer metric view ownership to (enables collaborative editing)"
    )

class FeatureFlags(BaseModel):
    """Safe toggles for new/experimental features."""
    enable_cortex_analyst: bool = Field(
        default=True,
        description="Generate Cortex Analyst YAML artifacts"
    )
    enable_parallel_execution: bool = Field(
        default=False,
        description="Experimental: Parallel execution of unrelated tasks"
    )
    skip_validation_on_dry_run: bool = Field(
        default=True,
        description="Skip deep validation during dry runs"
    )
    offline_mode: bool = Field(
        default=False,
        description="Run fabric source flows without Fabric API calls using local raw model JSON"
    )
    offline_fabric_model_path: str = Field(
        default="output/debug/raw_fabric_model.json",
        description="Path to local Fabric model JSON used when offline_mode is enabled"
    )

class LegacyCleanup(BaseModel):
    """Cleanup options for old features."""
    drop_deprecated_views: bool = Field(
        default=False,
        description="Drop old _SV views if detected"
    )

class ConnectorBehavior(BaseModel):
    """
    Root configuration object for Connector Policy.
    Controls behavior, feature flags, and compatibility settings.
    """
    snowflake: SnowflakeBehavior = Field(default_factory=SnowflakeBehavior)
    fabric: FabricBehavior = Field(default_factory=FabricBehavior)
    databricks: DatabricksBehavior = Field(default_factory=DatabricksBehavior)
    semantic_model: SemanticModelBehavior = Field(default_factory=SemanticModelBehavior)
    compatibility: CompatibilityBehavior = Field(default_factory=CompatibilityBehavior)
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    legacy: LegacyCleanup = Field(default_factory=LegacyCleanup)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_offline_fields(cls, data):
        """Allow top-level offline_* keys by mapping them into features."""
        if not isinstance(data, dict):
            return data

        has_offline_alias = (
            "offline_mode" in data or "offline_fabric_model_path" in data
        )
        if not has_offline_alias:
            return data

        features = data.get("features")
        if not isinstance(features, dict):
            features = {}

        if "offline_mode" in data and "offline_mode" not in features:
            features["offline_mode"] = data["offline_mode"]
        if (
            "offline_fabric_model_path" in data
            and "offline_fabric_model_path" not in features
        ):
            features["offline_fabric_model_path"] = data["offline_fabric_model_path"]

        data["features"] = features
        return data

    @classmethod
    def from_yaml(cls, path: Path) -> "ConnectorBehavior":
        """Load behavior policy from YAML file."""
        if not path or not path.exists():
             # Return defaults if no file provided
            return cls()
        
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
            
        return cls(**raw)
