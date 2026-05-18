"""
Behavioral Configuration.

Defines the YAML schema for controlling connector behavior without changing code.
This separates "what to run" (ExecutionConfig) from "how to run it" (ConnectorBehavior).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

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
    preserve_existing_tables: bool = Field(
        default=False,
        description=(
            "When enabled, validate and preserve existing Snowflake tables "
            "during deployment instead of destructive CREATE OR REPLACE"
        )
    )

class FabricBehavior(BaseModel):
    """Fabric/PowerBI behavior controls."""
    deploy_overwrite: bool = Field(
        default=True,
        description="Overwrite existing semantic models by default"
    )
    tmdl_generation_mode: str = Field(
        default="standard",
        description="TMDL generation strategy: 'standard' or 'compatibility'"
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
    model_artifact_mode: str = Field(
        default="per_dataset",
        description=(
            "Databricks artifact granularity: 'per_dataset' (legacy behavior) or "
            "'per_model' (single metadata table + single model metric view)."
        ),
    )
    model_metadata_suffix: str = Field(
        default="metadata",
        description="Suffix for per-model metadata table naming in per_model mode"
    )
    model_metric_view_suffix: str = Field(
        default="metric_view",
        description="Suffix for per-model metric view naming in per_model mode"
    )
    model_fact_root: str = Field(
        default="",
        description=(
            "Optional dataset name to anchor model-level view generation in per_model mode. "
            "When empty, a deterministic dataset is chosen automatically."
        ),
    )
    multi_fact_split_mode: Literal["off", "opt_in", "enforced"] = Field(
        default="off",
        description=(
            "Multi-fact constellation split mode: 'off' (legacy single artifact path), "
            "'opt_in' (enable only when explicitly configured), or 'enforced' "
            "(always split by detected fact anchors)."
        ),
    )
    dummy_measure_anchor_confidence_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum confidence (0.0-1.0) required to auto-relocate a measure from "
            "a dummy measure-only dataset to a target fact dataset."
        ),
    )
    review_required_blocks_deployment: bool = Field(
        default=False,
        description=(
            "When true, measures queued as review-required block deployment instead of "
            "skipping only affected fact artifacts."
        ),
    )
    strict_cycle_fail_mode: bool = Field(
        default=False,
        description=(
            "When true, relationship cycles fail deployment for the affected artifact "
            "instead of using best-effort deterministic cycle breaking."
        ),
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
    enable_llm_dax_translation: bool = Field(
        default=False,
        description=(
            "Use the common LLM DAX translator for complex expressions after "
            "deterministic translation fails. Disabled by default to preserve "
            "legacy skip behavior unless a project opts in."
        ),
    )
    llm_dax_provider_order: list[str] = Field(
        default_factory=lambda: ["deepseek", "gemini", "groq"],
        description="Ordered LLM providers for complex DAX translation fallback.",
    )
    llm_dax_timeout_seconds: int = Field(
        default=20,
        ge=1,
        description="Per-provider timeout for LLM DAX translation attempts.",
    )
    llm_dax_cache_enabled: bool = Field(
        default=True,
        description="Cache LLM DAX translation results by DAX/schema/dialect.",
    )
    llm_dax_fallback_to_placeholder: bool = Field(
        default=True,
        description=(
            "When all LLM providers fail, return a draft placeholder expression "
            "instead of skipping the measure."
        ),
    )
    metric_view_only_sum_translation: bool = Field(
        default=False,
        description=(
            "When true, simple DAX SUM(Table[Column]) translation is allowed only "
            "for native metric-view generation and disabled for SQL view paths."
        ),
    )
    enable_cross_table_joins: bool = Field(
        default=False,
        description="Build explicit joins for measures that reference multiple datasets"
    )
    enable_metric_view_joins: bool = Field(
        default=False,
        description=(
            "Enable native Databricks metric view 'joins' property for multi-table snowflake schemas. "
            "When enabled, metric views will emit JOIN clauses for related tables. "
            "Requires enable_cross_table_joins=true. Default false for backward compatibility."
        ),
    )
    enable_cross_table_sql_fallback: bool = Field(
        default=False,
        description=(
            "When measure_view_type is metric_view, allow auto-routing cross-table "
            "models to SQL view generation. Keep disabled to enforce metric-view-first deployment."
        ),
    )
    allow_sql_fallback_on_metric_view_failure: bool = Field(
        default=False,
        description=(
            "When true, allow publish-time downgrade to SQL views if native metric-view "
            "deployment fails, even when measure_view_type is explicitly 'metric_view'."
        ),
    )
    statement_wait_timeout_seconds: int = Field(
        default=30,
        ge=1,
        description=(
            "Databricks SQL statement API wait timeout in seconds before polling."
        ),
    )
    statement_poll_interval_seconds: float = Field(
        default=3.0,
        ge=0.25,
        description=(
            "Databricks SQL statement polling interval in seconds while statements are running."
        ),
    )
    enable_low_confidence_drafts: bool = Field(
        default=False,
        description="Deploy LOW-confidence measures as draft placeholders instead of dropping them"
    )
    emit_metric_views_for_all_datasets: bool = Field(
        default=False,
        description=(
            "Emit fallback metric views for datasets without deployable measures. "
            "Project-level config can enable dimension-first coverage by default."
        )
    )
    emit_distinct_pk_metric_for_dimension_datasets: bool = Field(
        default=True,
        description=(
            "When emitting fallback metrics for measure-less datasets, also emit "
            "COUNT(DISTINCT primary_key) when a confident key can be inferred."
        )
    )
    strict_graph_coverage_validation: bool = Field(
        default=False,
        description=(
            "Fail publish when relationship graph endpoints are invalid or source "
            "coverage gaps are detected. Default false logs warnings only."
        )
    )
    enable_destructive_sync_operations: bool = Field(
        default=False,
        description=(
            "Allow destructive Databricks cleanup actions (DROP VIEW/TABLE) when resolving "
            "object-type conflicts during publish retries."
        )
    )
    enable_auto_join_key_bridge: bool = Field(
        default=False,
        description=(
            "When true, Databricks preflight automatically creates missing physical join-key "
            "columns required by model relationships and attempts conservative backfill from "
            "existing candidate columns (for example, customer -> customer_key)."
        )
    )
    preflight_validate_join_dimensions: bool = Field(
        default=True,
        description=(
            "When true, strip dimension entries that reference non-existent join-table columns "
            "from the metric-view YAML before deployment. Prevents FIELD_NOT_FOUND failures "
            "when the semantic model references columns that do not yet exist in Databricks."
        ),
    )
    join_column_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-join dimension overrides. Each key is a join alias; value is a dict with "
            "optional 'include_columns' (allowlist) or 'exclude_columns' (denylist) lists. "
            "Example: {product: {include_columns: [product_name, product_dim_ck]}}"
        ),
    )
    source_table_mapping: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Explicit mapping of Fabric dataset names to Databricks tables. "
            "Example: {'CONTINENT_1': 'analytics.raw.continent_data'}"
        ),
    )
    source_column_mapping: dict[str, Any] = Field(
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
    grant_select_to_groups: list[str] = Field(
        default_factory=list,
        description="Groups to GRANT SELECT on deployed metric views"
    )
    transfer_ownership_to: str = Field(
        default="",
        description="Group to transfer metric view ownership to (enables collaborative editing)"
    )
    semantic_router_enabled: bool = Field(
        default=False,
        description=(
            "Enable semantic router for fact-centric artifact generation. "
            "When true, per-fact metric-view artifacts are generated with isolated dimension scopes. "
            "When false, legacy single model-level view generation is used (backward compatible)."
        ),
    )
    semantic_router_override_models: list[str] = Field(
        default_factory=list,
        description=(
            "List of model names to exclude from semantic router processing, even when "
            "semantic_router_enabled is true. Useful for testing transitions. Example: ['OldModel', 'LegacyDatamart']"
        ),
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
    def from_yaml(cls, path: Path) -> ConnectorBehavior:
        """Load behavior policy from YAML file."""
        if not path or not path.exists():
             # Return defaults if no file provided
            return cls()

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        return cls(**raw)
