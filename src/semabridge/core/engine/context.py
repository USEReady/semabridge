from __future__ import annotations

from dataclasses import dataclass, field as _dataclass_field
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import Settings
from semabridge.core.source_format import SourceFormat
from semabridge.core.drop_ledger import DropLedger
from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel

@dataclass
class RunContext:
    """
    Execution context propagated through all steps.
    
    Generated at Step 2 and used throughout execution.
    """
    project_id: str
    run_id: str
    config: Settings
    start_time: float
    source_type: Literal["snowflake", "fabric", "pbix"]
    target_type: Optional[Literal["snowflake", "fabric", "databricks"]] = None
    behavior: ConnectorBehavior = _dataclass_field(default_factory=ConnectorBehavior)
    account_id: Optional[str] = None  # Linked Account for multi-user credential scoping

    # Pre-resolved access token for an account_id-scoped Fabric connection
    # (see engine.py Step 3). FabricConfig has no token field of its own, so
    # this carries it alongside the config rather than injecting
    # FABRIC_ACCESS_TOKEN into os.environ, which would be unsafe for
    # concurrently-running syncs. Consumed by extraction/fabric.py.
    fabric_access_token: Optional[str] = None

    # Optional override for the Snowflake semantic view name.
    # Set from model_name / project_name in the project config YAML.
    # Kept separate from config.model.name to avoid mutating the shared
    # lru_cache Settings singleton.
    semantic_view_name_override: Optional[str] = None

    # Clean, batch-disambiguated display name for this job's PBIX file (see
    # sync_execution_service._build_sync_jobs's resolve_pbix_deployment_base_names()
    # call). Consumed by conversion/pbix.py's _convert_pbix_to_sml() in place
    # of deriving the name from the file's own (UUID-prefixed) on-disk stem,
    # so uploaded PBIX files deploy under a clean name (e.g. "SALES_SEMANTIC")
    # instead of "F2556718...91E0DB143CDF6FB7_SALES_SEMANTIC" -- with a
    # short "_2"/"_3" suffix only when two files in the same batch would
    # otherwise collide on the identical clean name.
    resolved_pbix_display_name: Optional[str] = None

    # Multi-model batch isolation (see sync_execution_service's
    # _assign_project_mode_naming). physical_namespace is a readable,
    # deterministic per-model prefix set ONLY for models of a multi-mode
    # project (source.mode: multiple); it namespaces every physical object
    # the deploy creates in the shared target schema and the per-model
    # output/ subdirectory. None for single-mode projects, which keep their
    # exact previous object names and output paths.
    physical_namespace: Optional[str] = None
    batch_model_count: int = 1
    # Thread-scoped BatchViewNameRegistry (utils/name_translator.py) shared
    # by the Fabric jobs of one batch, so two Fabric datasets with the same
    # display name get "_2"-suffixed view names instead of colliding.
    batch_name_registry: Optional[Any] = None
    # The PBIX file THIS job was extracted from (multi-file batches carry
    # one per job; config.source.pbix_path is project-wide).
    pbix_path: Optional[str] = None
    # Final deployed view name (target-specific, e.g. "SALES_SEMANTIC"),
    # recorded at Step 8 for per-model run results.
    deployed_view_name: Optional[str] = None

    # Artifacts accumulated during execution
    source_format: Optional[SourceFormat] = None
    osi_model: Optional[OSIModel] = None  # OSI intermediate — populated after Step 6
    sml_model: Optional[SMLModel] = None
    source_artifact_id: Optional[str] = None
    sml_snapshot_id: Optional[str] = None
    target_artifact_path: Optional[str] = None
    routing_summary: Optional[dict[str, Any]] = None
    sync_mode: str = "copy"
    # UPSERT delta sync (see core/upsert_delta.py): the per-kind delta
    # against the last deployed snapshot of this model, set in Step 6a.
    # None for COPY runs. upsert_delta["has_changes"] False skips Step 9.
    upsert_delta: Optional[Dict[str, Any]] = None
    # Dry run only (Snowflake target): {"tables": [{dataset, action
    # "bind"|"create", table, matched_by, tie}], "error"} — see
    # SnowflakeSchemaManager.plan_source_tables.
    table_binding_plan: Optional[Dict[str, Any]] = None
    # Dry run: Step 8 builds the target DDL (against the live schema) for
    # the preview but writes no target artifacts.
    dry_run: bool = False

    # Threaded through so Step 6's OSI→SML conversion can apply
    # metric-name mapping overrides to the OSI model's DAX bracket
    # references BEFORE translation runs, not just to the final SML
    # model afterward. See _apply_mapping_overrides_from_config.
    config_path: Optional[Path] = None
    config_payload: Optional[Dict[str, Any]] = None

    # Set True once a source-specific Step 6 conversion has already applied
    # mapping overrides at the OSI stage (fabric/pbix/snowflake-semantic-view).
    # Lets Step 6's caller skip a second, guaranteed-no-op pass over the same
    # overrides on the final SML model — the plain-metadata Snowflake path
    # never sets this, since it's the only path that still needs that call.
    mapping_overrides_applied: bool = False

    # Uniform "what got dropped and why" collector for this run — shared by
    # every DDL-building/extraction component so all drop reasons (DAX
    # translation, schema mismatch, DDL-emission skips, DDL-deployment
    # rejections) land in one place regardless of stage or cause.
    drop_ledger: DropLedger = _dataclass_field(default_factory=DropLedger)

    # The DDL of the semantic view actually deployed to the target, as read
    # back live via GET_DDL at the end of Step 9's real deploy (see
    # engine/deployment/snowflake.py) — using the same connection Step 9
    # already has open, no separate fetch. None whenever no live deploy
    # happened this run (dry run, no target, or the deploy itself failed).
    # Step 10 (finalize.py) uses this to reconcile drop_ledger's records
    # against what's actually live before populating RunSummary.dropped_entities,
    # so an entity that failed in an earlier, incomplete pass (e.g. Step 8's
    # schema-less preview emitter) but succeeded in this real deploy isn't
    # reported as dropped just because nothing else ever retracted it.
    deployed_ddl_text: Optional[str] = None

    # Paths for the Cortex/DDL artifacts Step 8 wrote to a STAGING location
    # rather than the canonical output/reverse/<project>/ directory directly.
    # Step 10 (finalize.py) promotes staging -> canonical only when the run's
    # final status is SUCCESS, so a run that fails after Step 8 (e.g. Step 9
    # deploy failure) can never leave the last known-good deployed artifact
    # corrupted with content describing a model that was never actually
    # deployed. None whenever Step 8 didn't run (dry run, no target).
    staged_target_artifact_paths: Optional[Dict[str, Any]] = None
    # Per-table outcome of the opt-in data-backfill step (see
    # connectors/schema_manager.py's _maybe_backfill_source_data, run from
    # snowflake_emitter.py's deploy pipeline only when the project config
    # sets options.load_source_data: true). Mirrors drop_ledger's own
    # emitter-instance -> context merge pattern (see
    # engine/deployment/snowflake.py's _do_snowflake_deploy): the emitter
    # accumulates entries on its own instance during deploy(), and the
    # caller copies them here once deploy() returns. Deliberately NOT part
    # of drop_ledger -- drop_ledger means "excluded," and a successful load
    # isn't an exclusion; keeping this separate avoids overloading
    # DropStage/DropRecord's existing semantics and consumers (the Dropped
    # Fields UI). Each entry: {table, dataset, status: "loaded"|"skipped"|
    # "failed", row_count, reason}.
    data_backfill_results: list[Dict[str, Any]] = _dataclass_field(default_factory=list)
    cortex_validation_results: list[Dict[str, Any]] = _dataclass_field(default_factory=list)
