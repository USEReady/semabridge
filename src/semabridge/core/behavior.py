"""
Behavioral Configuration.

Defines the YAML schema for controlling connector behavior without changing code.
This separates "what to run" (ExecutionConfig) from "how to run it" (ConnectorBehavior).
"""

from __future__ import annotations

from typing import Dict, List, Optional
from pathlib import Path
import yaml

from pydantic import BaseModel, Field

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
    metric_overrides: Dict[str, str] = Field(
        default_factory=dict,
        description="Manual SQL overrides for complex measures (Name -> SQL)"
    )
    override_alias_map: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Maps short SQL prefixes used in metric_overrides to the logical "
            "dataset name so the expression sanitizer can rewrite them to the "
            "correct lowercase alias.  Example: {'FACT': 'Fact_Sales', "
            "'PRODUCT': 'ProductDim', 'CALENDAR': 'CalendarDim'}"
        ),
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
    semantic_model: SemanticModelBehavior = Field(default_factory=SemanticModelBehavior)
    compatibility: CompatibilityBehavior = Field(default_factory=CompatibilityBehavior)
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    legacy: LegacyCleanup = Field(default_factory=LegacyCleanup)

    @classmethod
    def from_yaml(cls, path: Path) -> "ConnectorBehavior":
        """Load behavior policy from YAML file."""
        if not path or not path.exists():
             # Return defaults if no file provided
            return cls()
        
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
            
        return cls(**raw)
