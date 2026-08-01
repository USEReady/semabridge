"""
Snowflake Emitter.
Generates and executes Snowflake Semantic View DDL and Cortex Analyst YAML from SML models.
"""

from __future__ import annotations

import time
import re
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior
from semabridge.formats.sml.models import SMLModel
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.logger import get_logger
from semabridge.core.interfaces import BaseEmitter
from semabridge.core.exceptions import ConnectorError
from semabridge.repository.duplicate_name_mapping_repository import DuplicateNameMappingRepository

# Import domain managers
from semabridge.connectors.connection_manager import SnowflakeConnectionManager
from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.connectors.measure_sync import MeasureSynchronizer
from semabridge.connectors.ddl_builder import SemanticViewBuilder
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.connectors.snowflake_emitter_parts import renderers as _renderers

if TYPE_CHECKING:
    from semabridge.intermediate.models import OSIModel

logger = get_logger(__name__)


class MissingSourceTableWarning(UserWarning):
    """
    Warning raised when a source table referenced in an SML model
    cannot be found in the Snowflake schema.
    
    This is typically non-fatal and allows the deployment to continue
    while flagging potentially broken views.
    """
    pass


class SnowflakeEmitter(BaseEmitter):
    """
    Orchestrates the emission of SML models to Snowflake artifacts.
    Acts as a Facade delegating to specialized domain managers.
    """
    
    def __init__(self, config: SnowflakeConfig, behavior: Optional[ConnectorBehavior] = None):
        self.config = config
        self.behavior = behavior or ConnectorBehavior()
        self.sf_behavior = self.behavior.snowflake
        self.last_deployment_error: Optional[str] = None
        
        # Unified identifier sanitizer
        self._id = IdentifierSanitizer(
            force_uppercase=self.behavior.compatibility.force_uppercase,
            always_quote=self.sf_behavior.quote_identifiers,
            suppress_reserved=self.behavior.compatibility.suppress_reserved_words,
            additional_reserved=set(getattr(self.behavior.compatibility, 'additional_reserved_words', []) or []),
        )

        self._live_schema_metadata: Dict[str, set[str]] = {}
        self._verified_tables: set[str] = set()
        # Local mapping for enriched views when behavior config lacks an explicit mapping
        self._enriched_view_mapping: Dict[str, str] = {}

        # ---------------------------------------------------------
        # Initialize Domain Managers
        # ---------------------------------------------------------
        self.connection_manager = SnowflakeConnectionManager(
            config=self.config,
            behavior=self.behavior,
        )
        try:
            self._dup_name_repo = DuplicateNameMappingRepository()
        except Exception as exc:
            self._dup_name_repo = None
            logger.warning(f"Duplicate-name mapping repository unavailable: {exc}")

        self.schema_manager = SnowflakeSchemaManager(
            config=self.config,
            behavior=self.behavior,
            identifier_sanitizer=self._id,
            connection_manager=self.connection_manager,
            dup_name_repo=self._dup_name_repo,
        )
        self.translator = MetricExpressionTranslator(
            identifier_sanitizer=self._id
        )
        self.measure_synchronizer = MeasureSynchronizer(
            config=self.config,
            behavior=self.behavior,
            identifier_sanitizer=self._id,
            schema_manager=self.schema_manager,
            connection_manager=self.connection_manager,
            translator=self.translator,
        )
        # Shared with SemanticViewBuilder and every DDL sub-builder so all
        # drop reasons (schema mismatch, DDL-emission skips, DDL-deployment
        # rejections) land in one place. Cleared in place (not reassigned)
        # at the start of each deploy so this object identity — and every
        # sub-builder's reference to it — stays valid across reused emitter
        # instances.
        self.drop_ledger = DropLedger()
        self.semantic_view_builder = SemanticViewBuilder(
            config=self.config,
            behavior=self.behavior,
            identifier_sanitizer=self._id,
            live_schema_metadata=self._live_schema_metadata,
            schema_manager=self.schema_manager,
            dup_name_repo=self._dup_name_repo,
            translator=self.translator,
            drop_ledger=self.drop_ledger,
        )

    # =========================================================================
    # ORCHESTRATION: DEPLOYMENT PIPELINE
    # =========================================================================

    def deploy(
        self,
        sml: SMLModel,
        parallel: bool = False,
        max_workers: int = 4,
        sync_mode: str = "copy",
    ) -> bool:
        """Deploy the SML model to Snowflake."""
        return self._execute_deployment_pipeline(sml, is_osi=False, sync_mode=sync_mode)

    def deploy_from_osi(
        self,
        osi: OSIModel,
        parallel: bool = False,
        max_workers: int = 4,
        sync_mode: str = "copy",
    ) -> bool:
        """Deploy an OSI model directly to Snowflake."""
        return self._execute_deployment_pipeline(osi, is_osi=True, sync_mode=sync_mode)

    def _execute_deployment_pipeline(
        self,
        model: Any,
        is_osi: bool = False,
        sync_mode: str = "copy",
    ) -> bool:
        """Common orchestration for deploying SML or OSI models to Snowflake."""
        try:
            self.last_deployment_error = None
            # Reset per-run tracking so a reused emitter instance doesn't carry
            # stale dropped-metric/smoke-test warnings over from a prior deploy.
            self._dropped_metrics = []
            self._smoke_test_warnings = []
            # Same reason as the two resets above — a reused emitter instance
            # must not carry a prior deploy's live-schema snapshot into this
            # one (e.g. a MAX_DATE anchor a previous run found, on a table
            # this run's Step 1 might recreate from scratch). Cleared in
            # place, not reassigned — self.semantic_view_builder holds this
            # same dict by reference (passed at construction, see __init__),
            # exactly like drop_ledger below.
            self._live_schema_metadata.clear()
            # Cleared in place (see __init__) so SemanticViewBuilder's shared
            # reference to this same ledger stays valid.
            self.drop_ledger.clear()
            deploy_started_at = time.perf_counter()
            model_name = getattr(model, "unique_name", None) or getattr(model, "label", None) or "<unnamed_model>"
            path_type = "OSI" if is_osi else "SML"
            effective_sync_mode = str(sync_mode or "copy").lower()
            if effective_sync_mode not in {"copy", "upsert"}:
                effective_sync_mode = "copy"
            
            logger.info("[%s] start model=%s", path_type, model_name)

            conn, owns_conn = self.connection_manager.get_connection()
            logger.info("Starting deployment with STRICT sanitization rules")
            
            try:
                cur = conn.cursor()
                # Store model for helper methods
                self._model = model
                # Let TablesClauseBuilder precompute anchor literals (e.g.
                # _CURRENT_FISCAL_PERIOD) via a live lookup instead of
                # embedding a subquery in the TABLES clause — see
                # TablesClauseBuilder._fetch_fiscal_anchor_literal.
                self.semantic_view_builder.cursor = cur

                # Step -1: Pre-compute duplicate name mappings
                if is_osi:
                    self.semantic_view_builder._precompute_duplicate_mappings(model, is_osi=True)
                else:
                    self.semantic_view_builder._precompute_duplicate_mappings(model)

                # Step 0: Legacy Cleanup
                if self.behavior.legacy.drop_deprecated_views:
                    self._drop_deprecated_views(cur, model)

                # Step 1: Auto-create and Type-fix tables
                if self.sf_behavior.create_missing_tables:
                    self.schema_manager._ensure_source_tables_exist(cur, model)

                if self.sf_behavior.apply_inferred_types:
                    if is_osi:
                        self.schema_manager._apply_inferred_types_ctas_osi(cur, model)
                    else:
                        self.schema_manager._apply_inferred_types_ctas_sml(cur, model)

                # Step 1.5: Validation Gate & Metadata Refresh
                # Bust the module-level schema cache first — Step 1 may have
                # just created/altered tables (_ensure_source_tables_exist /
                # _apply_inferred_types_ctas_*), and this step's whole job is
                # to confirm what actually exists in Snowflake *right now*.
                # Without this, a stale cache entry from an earlier deploy
                # attempt against the same database.schema (still within its
                # TTL) gets served instead of a fresh snapshot, silently
                # excluding columns that do exist (or including ones that no
                # longer do).
                self.schema_manager.refresh_schema_cache()
                sf_meta = self.schema_manager._fetch_schema_metadata(cur)
                if not sf_meta:
                    datasets = list(getattr(model, "datasets", []) or [])
                    sf_meta = self.schema_manager._fetch_model_table_metadata(cur, datasets)
                self._live_schema_metadata.update(sf_meta or {})

                # Auto-enrichment — moved to run here (after Step 1/1.5, once
                # the underlying fact table is confirmed to actually exist),
                # not before Step 1 as originally written. Previously this
                # ran first, against a table that might not exist yet on a
                # cold deploy — _create_enriched_view's own anchor-column
                # fetch (e.g. MAX_DATE, or any other computed anchor for any
                # table) swallows that failure silently and just omits the
                # column, so a metric depending on it would flip between
                # "live" and "dropped" across runs purely based on whether a
                # *previous* run happened to have already created the table
                # — the same code, same model, different outcome. Running
                # this after the table is guaranteed to exist removes that
                # timing dependency entirely, for any anchor column, any
                # table, any model — not just MAX_DATE.
                if getattr(self.sf_behavior, 'auto_execute_precompute', False):
                    logger.info("🔧 Auto-enrichment enabled - executing pre-compute suggestions...")
                    self._auto_execute_precompute_suggestions(model, cur)

                    if getattr(self.sf_behavior, 'auto_create_enriched_view', False):
                        for fact_table in self._get_fact_tables_needing_enrichment(model):
                            enriched_view = self._create_enriched_view(model, cur, fact_table=fact_table)
                            if enriched_view and getattr(self.sf_behavior, 'use_enriched_view_for_metrics', False):
                                mapping = getattr(self.sf_behavior, 'source_table_mapping', None)
                                if mapping is None:
                                    # Keep mapping local to emitter instance
                                    self._enriched_view_mapping[fact_table] = enriched_view
                                else:
                                    mapping[fact_table] = enriched_view
                                logger.info(f"✅ Using enriched view {enriched_view} as source for {fact_table}")

                if is_osi:
                    self.schema_manager._preflight_check_osi(cur, model)

                # Step 2a: UPSERT bootstrap/preserve decision
                from semabridge.utils.name_translator import get_target_deployment_name
                view_name_raw = getattr(model, "label", None) or getattr(model, "unique_name", None) or "model"
                safe_view_name = get_target_deployment_name(view_name_raw, "snowflake")
                full_view_name = f'"{self.config.database}"."{self.config.schema_name}"."{safe_view_name}"'

                existing_tables: dict[str, dict[str, Any]] = {}
                preserve_existing = False

                if effective_sync_mode == "upsert":
                    view_exists = self._view_exists(cur, full_view_name)
                    if not view_exists:
                        logger.info(
                            "UPSERT bootstrap: semantic view %s does not exist. "
                            "Creating full Snowflake semantic view.",
                            full_view_name,
                        )
                    else:
                        preserve_existing = True
                        existing_tables = self._get_existing_base_tables(cur, model)
                        logger.info(
                            "UPSERT preserve: existing base tables found: %s",
                            list(existing_tables.keys()) if existing_tables else "none",
                        )

                        missing_tables = sorted(
                            dataset_name
                            for dataset_name, table_info in existing_tables.items()
                            if not bool(table_info.get("exists"))
                        )
                        if missing_tables and len(missing_tables) == len(existing_tables):
                            preserve_existing = False
                            logger.info(
                                "UPSERT bootstrap: semantic view %s exists, but none of "
                                "the required base tables were found in %s.%s. "
                                "Recreating the full Snowflake semantic view/table structure.",
                                full_view_name,
                                self.config.database,
                                self.config.schema_name,
                            )
                        elif missing_tables:
                            logger.warning(
                                "UPSERT partial bootstrap: semantic view %s exists, but "
                                "some required base tables are missing: %s. "
                                "Proceeding to create missing tables while preserving existing ones.",
                                full_view_name,
                                missing_tables,
                            )
                            preserve_existing = True

                    # Preserve validation runs only after all required base
                    # tables are confirmed present. First UPSERT runs use the
                    # full create path, matching COPY bootstrap behavior.
                    # _validate_model_on_existing_tables remains available as
                    # the compatibility helper for older callers/tests.
                    if preserve_existing and existing_tables:
                        logger.info("Validating relationships and measures against existing tables")
                        validation_errors, incompatible_tables = self._validate_relationships_measures_on_existing_tables(
                            cur, model, existing_tables, is_osi
                        )
                        if validation_errors:
                            logger.warning(
                                "Found %d validation error(s) across %d table(s). Incompatible tables will be recreated: %s",
                                len(validation_errors),
                                len(incompatible_tables),
                                incompatible_tables
                            )
                            for table_name in incompatible_tables:
                                if table_name in existing_tables:
                                    # Mark as non-existent to force re-creation
                                    existing_tables[table_name]['exists'] = False
                                    logger.info("Forced re-creation for incompatible table: %s", table_name)

                        logger.info("All validation checks passed for existing tables")
                elif getattr(self.sf_behavior, "preserve_existing_tables", False):
                    logger.info("COPY mode selected; preserve_existing_tables is ignored so COPY keeps full-replace behavior")

                # Step 2: Generate DDLs
                if is_osi:
                    ddls = self.semantic_view_builder.generate_ddls_from_osi(model)
                else:
                    ddls = self.semantic_view_builder.generate_ddls(model)
                
                # Step 2b: Surface columns present in the source model but absent from the
                # Snowflake physical schema (excluded from DIMENSIONS to prevent DDL errors).
                # Offer to add them via ALTER TABLE ADD COLUMN when auto_add_missing_dims=True.
                _missing_dims = getattr(self.semantic_view_builder, "missing_dims", {})
                if _missing_dims:
                    for _ds_name, _cols in _missing_dims.items():
                        _col_list = ", ".join(_cols)
                        logger.warning(
                            "⚠️  Missing dimension columns: table '%s' has columns [%s] in the "
                            "source model but they were NOT found in the Snowflake physical schema. "
                            "These columns are excluded from DIMENSIONS in the semantic view. "
                            "To include them, run: ALTER TABLE \"<schema>\".\"%s\" ADD COLUMN <col> <type>  "
                            "for each of: [%s]",
                            _ds_name, _col_list, _ds_name, _col_list,
                        )
                    _auto_add = getattr(getattr(self, "sf_behavior", None), "auto_add_missing_dims", False)
                    if _auto_add:
                        logger.info("auto_add_missing_dims=True — attempting ALTER TABLE ADD COLUMN for missing dimensions")
                        _added_any = False
                        for _ds_name, _cols in list(_missing_dims.items()):
                            _src_tbl = _ds_name  # use dataset name as table name fallback
                            for _ds_obj in (model.datasets if is_osi else getattr(model, "datasets", [])):
                                if getattr(_ds_obj, "unique_name", None) == _ds_name:
                                    _src_tbl = getattr(_ds_obj, "source_table", None) or _ds_name
                                    break
                            _safe_tbl = _src_tbl.rsplit(".", 1)[-1] if "." in _src_tbl else _src_tbl
                            for _col in _cols:
                                _alter_sql = (
                                    f'ALTER TABLE "{self.connection_manager.config.database}"'
                                    f'."{self.connection_manager.config.schema_name}"'
                                    f'."{_safe_tbl}" ADD COLUMN IF NOT EXISTS "{_col}" VARCHAR'
                                )
                                try:
                                    self.connection_manager._execute_sql(cur, _alter_sql, context="add-missing-dim")
                                    logger.info("Added missing dimension column '%s' to table '%s'", _col, _safe_tbl)
                                    _added_any = True
                                except Exception as _alter_exc:
                                    logger.warning("Could not add missing dim column '%s.%s': %s", _safe_tbl, _col, _alter_exc)
                        if _added_any:
                            # Refresh live schema so the re-generated DDL picks up the new columns
                            logger.info("Refreshing live schema after adding missing dimension columns")
                            _fresh_meta = self.schema_manager._fetch_schema_metadata(cur)
                            self.semantic_view_builder.live_schema_metadata.update(_fresh_meta or {})
                            self.semantic_view_builder.missing_dims.clear()
                            # Regenerate DDLs with the updated schema
                            if is_osi:
                                ddls = self.semantic_view_builder.generate_ddls_from_osi(model)
                            else:
                                ddls = self.semantic_view_builder.generate_ddls(model)
                            logger.info("Regenerated %d DDL statement(s) after schema update", len(ddls))

                # Step 2c: UPSERT preserve only skips existing base-table DDLs.
                if preserve_existing and existing_tables:
                    logger.info("Filtering DDLs to skip existing tables")
                    ddls = self._filter_ddls_for_existing_tables(ddls, existing_tables)

                if not ddls:
                    raise ConnectorError(
                        "No Snowflake semantic-view DDL statements were generated. "
                        "Verify model datasets/mappings and target database/schema settings."
                    )

                logger.info("[%s] generated %s DDL statement(s) for model=%s", path_type, len(ddls), model_name)

                # Step 3: Execute DDLs  (generate_ddls returns list[str])
                # Pre-pass A: Refresh any stale *_ENRICHED regular views referenced by the DDL.
                # Snowflake error 002057 fires when a semantic view is compiled against an
                # enriched view whose stored column count no longer matches the live SELECT
                # (e.g. the underlying fact table gained columns since the enriched view was
                # last created).  Recreating the enriched view here keeps the counts in sync.
                import re as _re_drop
                _enriched_pattern = _re_drop.compile(
                    r'"?(\w+_ENRICHED)"?', _re_drop.IGNORECASE
                )
                _seen_enriched: set = set()
                for _s in ddls:
                    if not _s:
                        continue
                    for _em in _enriched_pattern.findall(_s):
                        _en = _em.upper()
                        if _en in _seen_enriched:
                            continue
                        _seen_enriched.add(_en)
                        # Resolve which dataset this specific enriched view belongs to
                        # (e.g. "SALESFACT_ENRICHED" -> "SalesFact") so the refresh
                        # rebuilds the matching view instead of always guessing the
                        # same single fact table.
                        _candidate_name = _en[: -len("_ENRICHED")] if _en.endswith("_ENRICHED") else _en
                        _candidate_dataset = self._get_dataset_by_name(model, _candidate_name)
                        _refresh_fact_table = (
                            getattr(_candidate_dataset, "unique_name", None) or _candidate_name
                        )
                        # Recreate the enriched view so column count matches the current
                        # fact table regardless of when it was last created.
                        try:
                            _refreshed = self._create_enriched_view(model, cur, fact_table=_refresh_fact_table)
                            if _refreshed:
                                logger.info(
                                    "Pre-refreshed enriched view '%s' to sync column count", _refreshed
                                )
                            else:
                                # Fallback: just drop the stale view so Snowflake won't
                                # reject the semantic view DDL on column-count mismatch.
                                _drop_enriched = f'DROP VIEW IF EXISTS "{_en}"'
                                self.connection_manager._execute_sql(
                                    cur, _drop_enriched, context="pre-drop-enriched-view"
                                )
                                logger.info(
                                    "Pre-dropped stale enriched view '%s' (could not recreate)", _en
                                )
                        except Exception as _enr_exc:
                            logger.warning(
                                "Could not refresh enriched view '%s' (non-fatal): %s", _en, _enr_exc
                            )

                # Pre-pass B: DROP existing semantic views before (re)creating them.
                # Snowflake error 002057 fires when a CREATE OR REPLACE SEMANTIC VIEW
                # changes the number of declared columns vs the existing view definition.
                # Dropping first eliminates that constraint entirely.
                for _s in ddls:
                    if not _s:
                        continue
                    _upper = _s.upper()
                    if "CREATE" in _upper and "SEMANTIC VIEW" in _upper:
                        _vm = _re_drop.search(
                            r'CREATE\s+(?:OR\s+REPLACE\s+)?SEMANTIC\s+VIEW\s+"?(\w+)"?',
                            _s, _re_drop.IGNORECASE,
                        )
                        if _vm:
                            _drop_sql = f'DROP SEMANTIC VIEW IF EXISTS "{_vm.group(1)}"'
                            try:
                                self.connection_manager._execute_sql(cur, _drop_sql, context="pre-drop-semantic-view")
                                logger.info("Pre-dropped semantic view '%s' before recreation", _vm.group(1))
                            except Exception as _drop_exc:
                                logger.warning("Pre-drop of semantic view '%s' failed (non-fatal): %s", _vm.group(1), _drop_exc)

                for idx, sql in enumerate(ddls):
                    if not sql:
                        continue
                    try:
                        self.connection_manager._execute_sql(cur, sql, context=f"DDL[{idx}]")
                    except Exception as ddl_exc:
                        # Auto-remediate invalid identifier errors iteratively.
                        # Each pass fixes one invalid identifier; we retry up to
                        # MAX_REMEDIATION_PASSES times so chained bad identifiers
                        # (e.g. MAX_DATE → fixed, then cross-metric reference → fixed)
                        # are all resolved before giving up.
                        from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
                        _sanitizer = SemanticDDLSanitizer(self._id)
                        _current_sql = sql
                        _current_exc = ddl_exc
                        _MAX_PASSES = 10
                        _remediated = False
                        for _pass in range(_MAX_PASSES):
                            _invalid_id = self.connection_manager._extract_invalid_identifier(_current_exc)
                            if not _invalid_id:
                                break
                            _fixed_sql, _changed = _sanitizer.remediate_invalid_identifier(_current_sql, _invalid_id)
                            if not _changed:
                                logger.warning(
                                    "DDL[%d] pass %d: sanitizer could not fix '%s' — giving up",
                                    idx, _pass + 1, _invalid_id,
                                )
                                break
                            logger.warning(
                                "DDL[%d] pass %d: fixing invalid identifier '%s'",
                                idx, _pass + 1, _invalid_id,
                            )
                            # Track metric-definition drops (as opposed to anchor-column
                            # substitutions like MAX_DATE/MAX_MONTHINDEX, which keep the
                            # metric's real semantics) so the user gets a clear final
                            # summary of which metrics didn't make it into the deployed
                            # semantic view, instead of this being buried in these
                            # pass-by-pass WARNING logs.
                            _invalid_upper = _invalid_id.upper().replace('"', "")
                            if "." in _invalid_id and _invalid_upper not in ("MAX_DATE", "MAX_MONTHINDEX"):
                                # Resolve the raw rejected identifier (e.g. a
                                # table.column reference inside a metric's SQL)
                                # back to the METRICS-clause name that declares
                                # it, so the ledger records the same metric
                                # identity used everywhere else (the SML
                                # snapshot's unique_name-derived name) instead
                                # of an opaque SQL identifier no downstream
                                # reconciliation can match against.
                                _dropped_metric_name = self._resolve_metric_name_for_invalid_identifier(
                                    _current_sql, _invalid_id
                                )
                                self._dropped_metrics.append(
                                    {"metric": _dropped_metric_name, "reason": str(_current_exc)}
                                )
                                self.drop_ledger.record(
                                    "metric", _dropped_metric_name, DropStage.DDL_DEPLOYMENT,
                                    "Snowflake rejected this identifier when executing the "
                                    "compiled semantic-view DDL, and it could not be "
                                    "automatically remediated.",
                                    detail=str(_current_exc)[:300],
                                )
                            _current_sql = _fixed_sql
                            try:
                                self.connection_manager._execute_sql(
                                    cur, _current_sql, context=f"DDL[{idx}] pass {_pass + 1}"
                                )
                                _remediated = True
                                break
                            except Exception as _retry_exc:
                                _current_exc = _retry_exc
                        if _remediated:
                            continue  # DDL succeeded after remediation
                        raise _current_exc  # re-raise last failure

                # Step 5: Artifact Generation (Cortex YAML / Audit)
                if not is_osi:
                    self._generate_deployment_artifacts(model, ddls, cur)

                # Step 6: Post-deploy smoke test — catch runtime errors early
                # ddls is list[str]; extract view name from each DDL for the test.
                # The DDL declares the view fully qualified as "DB"."SCHEMA"."VIEW"
                # (see ddl_builder.py full_view_name), and Snowflake allows mixed
                # quoting per segment, so the view name is always the LAST
                # dot-separated segment, not the first.
                import re as _re_smoke
                for _ddl_sql in ddls:
                    _m = _re_smoke.search(
                        r'CREATE\s+(?:OR\s+REPLACE\s+)?SEMANTIC\s+VIEW\s+'
                        r'((?:"[^"]+"|\w+)(?:\s*\.\s*(?:"[^"]+"|\w+))*)',
                        _ddl_sql, _re_smoke.IGNORECASE,
                    )
                    if not _m:
                        continue
                    _qualified_name = _m.group(1)
                    _view_name = _qualified_name.split(".")[-1].strip().strip('"')
                    _smoke_err = self._smoke_test_semantic_view(cur, _view_name, _ddl_sql)
                    if _smoke_err:
                        logger.warning(
                            "[%s] Semantic view '%s' deployed but smoke test failed: %s",
                            path_type, _view_name, _smoke_err,
                        )
                        # Non-fatal: view was accepted by Snowflake DDL validation.
                        # Surface as a warning in the run log without rolling back.
                        self._smoke_test_warnings.append(
                            {"view": _view_name, "error": _smoke_err}
                        )

                # Final run summary: surface any metrics that were dropped during
                # auto-remediation. These are non-fatal to deployment (Snowflake
                # accepted the DDL after nulling them out) but the user needs a
                # clear, un-missable record of which metrics didn't make it in.
                if self._dropped_metrics:
                    logger.warning("=" * 70)
                    logger.warning(
                        "⚠️  [%s] %d metric(s) DROPPED from '%s' — Snowflake rejected "
                        "their definition, so they were replaced with "
                        "CAST(NULL AS DOUBLE) to let deployment complete:",
                        path_type, len(self._dropped_metrics), model_name,
                    )
                    for _dm in self._dropped_metrics:
                        logger.warning("  - %s: %s", _dm["metric"], _dm["reason"])
                    logger.warning(
                        "These metrics will return NULL until translated manually. "
                        "This commonly affects time-comparison metrics (SPLY/YoY) "
                        "whose DAX references another metric inside CALCULATE(...), "
                        "which Snowflake semantic views cannot express as a "
                        "single-level aggregate."
                    )
                    logger.warning("=" * 70)

                logger.info("[%s] success model=%s (%.2fs)", path_type, model_name, time.perf_counter() - deploy_started_at)
                return True

            finally:
                if owns_conn:
                    conn.close()

        except Exception as exc:
            self.last_deployment_error = str(exc)
            logger.error("[%s] FAILED model=%s: %s", path_type, model_name, exc, exc_info=True)
            return False

    @staticmethod
    def _first_semantic_view_ref(ddl: str) -> Optional[Tuple[str, str, str]]:
        """
        Find the first declared METRICS or DIMENSIONS entry in a semantic-view
        DDL, e.g. '  SALES_FACT."TOTAL_REVENUE" AS SUM(...)' -> ("METRICS",
        "SALES_FACT", "TOTAL_REVENUE"). Used to build a minimal, valid
        SEMANTIC_VIEW() smoke-test query without hardcoding any dataset- or
        model-specific names.
        """
        section: Optional[str] = None
        for line in (ddl or "").splitlines():
            stripped = line.strip()
            upper = stripped.upper()
            if upper.startswith("METRICS ("):
                section = "METRICS"
                continue
            if upper.startswith("DIMENSIONS ("):
                section = "DIMENSIONS"
                continue
            if stripped.startswith(")"):
                section = None
                continue
            if section:
                ref_match = re.match(r'^(\w+)\."?(\w+)"?\s+AS\s', stripped)
                if ref_match:
                    return section, ref_match.group(1), ref_match.group(2)
        return None

    @staticmethod
    def _resolve_metric_name_for_invalid_identifier(ddl: str, invalid_id: str) -> str:
        """Map a rejected SQL identifier back to the METRICS-clause name
        whose expression contains it.

        Best-effort and read-only: scans the METRICS( ... ) block for the
        first line whose text contains the invalid identifier and returns
        that line's declared name. Falls back to the raw identifier if no
        owning METRICS line is found (e.g. the rejection came from
        TABLES/DIMENSIONS/RELATIONSHIPS instead), so callers never regress
        to worse-than-today behaviour.
        """
        invalid_norm = invalid_id.upper().replace('"', "")
        in_metrics = False
        for line in (ddl or "").splitlines():
            stripped_upper = line.strip().upper()
            if stripped_upper.startswith("METRICS ("):
                in_metrics = True
                continue
            if in_metrics and re.match(r'^\s*\)\s*;?\s*$', line):
                in_metrics = False
                continue
            if not in_metrics:
                continue
            if invalid_norm in line.upper().replace('"', ""):
                m = re.search(r'\."([^"]+)"\s+AS\s+', line)
                if m:
                    return m.group(1)
        return invalid_id

    def _smoke_test_semantic_view(self, cursor: Any, view_name: str, ddl: str = "") -> Optional[str]:
        """Run a lightweight query against the semantic view to catch runtime errors.

        Snowflake accepts some invalid DDL that only fails at query time
        (e.g., column referenced in DIMENSIONS that doesn't exist in the physical table).

        Semantic views cannot be queried with a plain `SELECT * FROM view` — Snowflake
        requires the `SEMANTIC_VIEW(view METRICS ... | DIMENSIONS ...)` table function.
        A bare SELECT always fails with a false-negative "does not exist" error, so this
        pulls one real METRICS or DIMENSIONS reference straight out of the generated DDL
        (no hardcoded names) and queries through it.

        Args:
            cursor: Active Snowflake cursor.
            view_name: Name of the semantic view to test.
            ddl: The CREATE SEMANTIC VIEW DDL that was just deployed, used to find a
                real METRICS/DIMENSIONS reference to query through.

        Returns:
            None on success; error message string on failure.
        """
        ref = self._first_semantic_view_ref(ddl)
        if not ref:
            logger.debug("Smoke test skipped for '%s': no METRICS/DIMENSIONS found in DDL", view_name)
            return None
        section, alias, name = ref
        test_sql = (
            f'SELECT * FROM SEMANTIC_VIEW("{view_name}" {section} {alias}.{name}) LIMIT 0'
        )
        try:
            self.connection_manager._execute_sql(cursor, test_sql, context=f"smoke_test:{view_name}")
            return None
        except Exception as e:
            return str(e)

    def _generate_deployment_artifacts(self, sml: SMLModel, ddls: Dict[str, str], cur=None) -> None:
        """Generate side-car artifacts like Cortex YAML."""
        if self.behavior.features.enable_cortex_analyst:
            sample_fetcher = self._build_sample_fetcher(sml, cur) if cur is not None else None
            cortex_yaml = self.generate_cortex_yaml(sml, sample_fetcher=sample_fetcher)
            self._save_cortex_yaml_artifact(sml, cortex_yaml)

        if getattr(self.sf_behavior, "generate_audit_yaml", False):
            logger.debug("generate_audit_yaml flagged but renderer not yet implemented; skipping.")

    def _save_cortex_yaml_artifact(self, sml: SMLModel, cortex_yaml: str) -> None:
        """Persist the Cortex Analyst YAML to the same output/reverse/<project>/
        directory the target-conversion stage already writes semantic_view.sql
        and a sample-less cortex_analyst.yaml to (core/engine/targets/snowflake.py).

        This overwrites that placeholder with the deploy-time version, which is
        the only point in the pipeline with a live cursor and therefore the
        only place real sample_values can be populated (Fix 1). Runs
        independent of deployment_method — the DDL path deploys metrics/
        synonyms straight to Snowflake, but sample_values only ever exist in
        this side-car YAML.
        """
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sml.unique_name or "model"))
        safe_name = re.sub(r"_+", "_", safe_name).strip("._") or "model"
        yaml_path = Path("output") / "reverse" / safe_name / "cortex_analyst.yaml"
        try:
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            yaml_path.write_text(cortex_yaml, encoding="utf-8")
            logger.info("Cortex Analyst YAML (with live sample values) written to %s", yaml_path)
        except Exception as exc:
            logger.warning("Failed to persist Cortex Analyst YAML artifact to %s: %s", yaml_path, exc)

    def _build_sample_fetcher(self, sml: SMLModel, cur):
        """Build a memoized (dataset, column) -> sample-values callback for the Cortex renderer.

        Kept separate from the renderer so `generate_cortex_yaml` stays a pure
        model-to-YAML converter with no direct Snowflake dependency.
        """
        cache: Dict[tuple, list] = {}

        def _fetch(dataset_unique_name: str, column: Optional[str]) -> list:
            key = (dataset_unique_name, column)
            if key in cache:
                return cache[key]
            values: list = []
            ds = next((d for d in sml.datasets if d.unique_name == dataset_unique_name), None)
            if ds is not None and column:
                safe_table = self._safe_table_name(ds.source_table or ds.unique_name)
                safe_column = self._sanitize_col_name(column)
                values = self.connection_manager.fetch_distinct_sample_values(
                    cur, self.config.database, self.config.schema_name, safe_table, safe_column,
                )
            cache[key] = values
            return values

        return _fetch

    # =========================================================================
    # BASE EMITTER INTERFACE (Abstract Method Implementations)
    # =========================================================================

    def authenticate(self) -> None:
        """Establish connection to Snowflake."""
        self.connection_manager.authenticate()

    def discover(self) -> Dict[str, Any]:
        """List tables and views in the schema."""
        return self.connection_manager.discover()

    def validate_target(self) -> bool:
        """Check if Snowflake is reachable."""
        return self.connection_manager.validate_target()

    def emit(self, sml: Any) -> Dict[str, Any]:
        """Emit SML model to Snowflake."""
        success = self.deploy(sml)
        missing = dict(getattr(self.semantic_view_builder, "missing_dims", {}) or {})
        return {"success": success, "missing_dims": missing}

    @property
    def max_concurrency(self) -> int:
        """Snowflake DDL operations should be serialized more strictly."""
        return 3

    # =========================================================================
    # PUBLIC PROXIES (Backward Compatibility)
    # =========================================================================

    def generate_ddls(self, sml: SMLModel) -> list[str]:
        return self.semantic_view_builder.generate_ddls(sml)

    def generate_ddls_from_osi(self, osi: OSIModel) -> list[str]:
        return self.semantic_view_builder.generate_ddls_from_osi(osi)

    def sync_all_measures(self, sml: SMLModel, fabric_extractor, dataset_id: str, grain_dimensions=None) -> dict:
        return self.measure_synchronizer.sync_all_measures(sml, fabric_extractor, dataset_id, grain_dimensions)

    def _build_history_snapshot_ddls_for_sml(self, sml: SMLModel) -> list[str]:
        return self.semantic_view_builder._build_history_snapshot_ddls_for_sml(sml)

    def _build_history_snapshot_ddls_for_osi(self, osi: OSIModel) -> list[str]:
        return self.semantic_view_builder._build_history_snapshot_ddls_for_osi(osi)

    def generate_cortex_yaml(self, sml: SMLModel, sample_fetcher=None) -> str:
        return _renderers.generate_cortex_yaml(self, sml, sample_fetcher=sample_fetcher)

    def generate_cortex_yaml_from_osi(self, osi: OSIModel, sample_fetcher=None) -> str:
        return _renderers.generate_cortex_yaml_from_osi(self, osi, sample_fetcher=sample_fetcher)

    def _get_source_table_mapping(self) -> Dict[str, str]:
        """Return effective source_table_mapping, merging behavior config and local enriched mapping.

        Behavior-level mapping takes precedence over local emitter mapping.
        """
        mapping = getattr(self.sf_behavior, 'source_table_mapping', None) or {}
        merged = dict(self._enriched_view_mapping or {})
        merged.update(mapping or {})
        return merged

    def deploy_cortex_yaml(self, cursor: Any, sml: SMLModel, yaml_content: str) -> None:
        """Upload and register Cortex Analyst YAML via a Snowflake internal stage.

        The YAML is written to an internal stage at
        ``@<database>.<schema>.SEMABRIDGE_CORTEX/<model_name>.yaml``
        using a PUT-equivalent ``$$ ... $$`` inline file statement so that no
        local filesystem access is required.  A semantic-model registration
        call is then executed so Cortex Analyst can discover the file.
        """
        from semabridge.utils.name_translator import get_target_deployment_name
        model_name_raw = getattr(sml, "unique_name", None) or getattr(sml, "label", None) or "model"
        model_name = get_target_deployment_name(model_name_raw, "snowflake")
        stage_fqn = (
            f"{self.config.database}.{self.config.schema_name}.SEMABRIDGE_CORTEX"
        )
        stage_path = f"@{stage_fqn}/{model_name}.yaml"

        # Ensure the stage exists
        self.connection_manager._execute_sql(
            cursor,
            f"CREATE STAGE IF NOT EXISTS {stage_fqn} COMMENT = 'SemaBridge Cortex Analyst YAML store'",
            context="CREATE STAGE",
        )

        # Upload via PUT from an in-memory string (single-quoted, dollar-quoted body)
        escaped = yaml_content.replace("\\", "\\\\").replace("'", "\\'")
        put_sql = f"PUT TEXT '{escaped}' {stage_path} OVERWRITE = TRUE AUTO_COMPRESS = FALSE"
        try:
            self.connection_manager._execute_sql(cursor, put_sql, context="PUT YAML")
        except Exception as exc:
            logger.warning(
                "PUT to Cortex stage failed (%s); YAML deploy skipped. Stage path: %s",
                exc, stage_path,
            )
            return

        logger.info("Cortex Analyst YAML uploaded to %s", stage_path)

    # =========================================================================
    # UTILITY HELPERS (Internal Facade APIs)
    # =========================================================================

    def _sanitize_col_name(self, name: str) -> str:
        return self._id.sanitize_column(name)

    def _sanitize_semantic_name(self, name: str) -> str:
        """Sanitize semantic name and ensure it does not start with a digit."""
        sanitized = self._id.sanitize_column(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _sanitize_alias(self, name: str) -> str:
        """Sanitize alias names and ensure they do not start with a digit."""
        sanitized = self._id.sanitize_alias(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _get_safe_object_name(self, name: str) -> str:
        """Sanitize object name for Snowflake."""
        return self._id.sanitize_column(name)

    def _safe_table_name(self, name: str) -> str:
        """Sanitize a physical table name via unified IdentifierSanitizer."""
        return self._id.sanitize_table_name(name)

    def _resolve_column_name_for_dataset(self, known_columns: set[str], candidate: str) -> Optional[str]:
        return self.translator._resolve_column_name_for_dataset(known_columns, candidate)

    def _qualify_bare_partition_identifiers(self, *args, **kwargs) -> str:
        return self.translator._qualify_bare_partition_identifiers(*args, **kwargs)

    def _dedupe_qualified_column_tokens(self, *args, **kwargs) -> str:
        return self.translator._dedupe_qualified_column_tokens(*args, **kwargs)

    def _rewrite_window_metric_expression(self, *args, **kwargs) -> str:
        return self.translator._rewrite_window_metric_expression(*args, **kwargs)

    @staticmethod
    def _is_physical_source_column(source_expression: str) -> bool:
        """Determine if a source_expression represents a plain physical column."""
        return IdentifierSanitizer.is_physical_source_column(source_expression)

    def _sanitize_sql_markdown(self, sql: str) -> str:
        if not sql: return ""
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
        return sql.strip()

    def _execute_sql(self, cursor: Any, sql: str, context: str = "") -> Any:
        return self.connection_manager._execute_sql(cursor, sql, context=context)

    def _drop_deprecated_views(self, cursor: Any, model: Any) -> None:
        view_name = self._id.sanitize_column(getattr(model, "unique_name", None) or getattr(model, "label", None))
        legacy_view = f"{self.config.database}.{self.config.schema_name}.{view_name}_SV"
        try:
            self.connection_manager._execute_sql(cursor, f"DROP VIEW IF EXISTS {legacy_view}")
        except Exception:
            pass

    def get_semantic_view(self, view_name: str) -> Optional[str]:
        """Return the semantic view DDL for a configured Snowflake view name."""
        if not view_name:
            logger.warning("No semantic view name provided for extraction")
            return None

        conn, owns_conn = self.connection_manager.get_connection()
        try:
            cur = conn.cursor()
            safe_view_name = str(view_name).replace('"', "").strip()
            full_view_name = (
                f'"{self.config.database}"."{self.config.schema_name}"."{safe_view_name}"'
            )

            self.connection_manager._execute_sql(
                cur,
                f"SELECT GET_DDL('SEMANTIC_VIEW', '{full_view_name}')",
                context="GET_SEMANTIC_VIEW_DDL",
            )
            row = cur.fetchone()
            if not row or not row[0]:
                logger.info("No DDL returned for semantic view %s", full_view_name)
                return None

            ddl = str(row[0])
            logger.info(
                "Retrieved semantic view DDL for %s (%d chars)",
                full_view_name,
                len(ddl),
            )
            return ddl
        except Exception as exc:
            logger.warning("Failed to retrieve semantic view %s: %s", view_name, exc)
            return None
        finally:
            if owns_conn:
                conn.close()

    # =========================================================================
    # PRESERVE EXISTING TABLES: Helper Methods
    # =========================================================================

    def _view_exists(self, cursor: Any, full_view_name: str) -> bool:
        """Check if a semantic view exists in Snowflake.
        
        Args:
            cursor: Snowflake cursor for executing queries
            full_view_name: Fully qualified view name (e.g., "DB"."SCHEMA"."VIEW")
        
        Returns:
            True if the view exists, False otherwise
        """
        try:
            # Extract parts from full view name (handle quoted identifiers)
            parts = full_view_name.replace('"', '').split('.')
            if len(parts) != 3:
                logger.warning("Invalid view name format: %s", full_view_name)
                return False
            
            db, schema, view = parts
            safe_view = view.replace("'", "''")

            try:
                self.connection_manager._execute_sql(
                    cursor,
                    f"SHOW SEMANTIC VIEWS LIKE '{safe_view}' IN SCHEMA \"{db}\".\"{schema}\"",
                    context="CHECK_SEMANTIC_VIEW_EXISTS",
                )
                exists = bool(cursor.fetchall())
            except Exception as show_exc:
                logger.debug(
                    "SHOW SEMANTIC VIEWS check failed for %s: %s; falling back to INFORMATION_SCHEMA",
                    full_view_name,
                    show_exc,
                )
                query = f"""
                SELECT COUNT(*) as cnt
                FROM "{db}".INFORMATION_SCHEMA.TABLES
                WHERE UPPER(TABLE_CATALOG) = UPPER('{db.replace("'", "''")}')
                  AND UPPER(TABLE_SCHEMA) = UPPER('{schema.replace("'", "''")}')
                  AND UPPER(TABLE_NAME) = UPPER('{safe_view}')
                  AND UPPER(TABLE_TYPE) IN ('SEMANTIC VIEW', 'DYNAMIC VIEW')
                """

                result = self.connection_manager._execute_sql(cursor, query, context="CHECK_VIEW_EXISTS")
                row = result.fetchone()
                exists = row[0] > 0 if row else False
            
            logger.info("View existence check: %s -> %s", full_view_name, "EXISTS" if exists else "DOES NOT EXIST")
            return exists
            
        except Exception as exc:
            logger.warning("Error checking view existence for %s: %s", full_view_name, exc)
            return False

    def _validate_model_on_existing_tables(
        self, cursor: Any, model: Any, full_view_name: str, is_osi: bool = False
    ) -> Tuple[bool, List[str]]:
        """Validate that the model's dependencies are compatible with existing tables.
        
        Checks:
        1. All datasets reference existing Snowflake tables
        2. All relationships reference existing columns in source/target tables
        3. All metrics reference existing columns in datasets
        
        Args:
            cursor: Snowflake cursor
            model: SML or OSI model
            full_view_name: Fully qualified view name (unused, for logging context)
            is_osi: True if model is OSI, False if SML
        
        Returns:
            Tuple of (validation_passed, error_messages)
        """
        errors: List[str] = []
        
        try:
            # Get existing table columns from Snowflake
            existing_tables = self.schema_manager._fetch_schema_metadata(cursor) or {}
            
            # Extract datasets from model
            datasets = list(getattr(model, "datasets", []) or [])
            
            if not datasets:
                logger.warning("No datasets found in model for validation")
                return True, []  # No datasets to validate
            
            logger.info("Validating %d dataset(s) against existing Snowflake tables", len(datasets))
            
            # Validate each dataset references an existing table
            for dataset in datasets:
                dataset_name = getattr(dataset, "name", None) or getattr(dataset, "label", None)
                table_name = getattr(dataset, "table_name", None)
                schema_name = getattr(dataset, "schema_name", None) or self.config.schema_name
                
                if not table_name:
                    errors.append(f"Dataset '{dataset_name}' has no table_name specified")
                    continue
                
                # Build full table name
                full_table_name = f"{schema_name}.{table_name}".lower()
                
                # Check if table exists in Snowflake metadata
                if full_table_name not in existing_tables:
                    errors.append(
                        f"Dataset '{dataset_name}' references table '{table_name}' "
                        f"in schema '{schema_name}', but this table does not exist in Snowflake"
                    )
                    continue
                
                logger.info("✓ Dataset '%s' table exists: %s.%s", dataset_name, schema_name, table_name)
            
            # Validate relationships
            relationships = list(getattr(model, "relationships", []) or [])
            for rel in relationships:
                is_active = getattr(rel, "is_active", True)
                if not is_active:
                    continue
                
                from_dataset = getattr(rel, "from_dataset", None)
                to_dataset = getattr(rel, "to_dataset", None)
                from_column = getattr(rel, "from_column", None)
                to_column = getattr(rel, "to_column", None)
                
                if not (from_dataset and to_dataset and from_column and to_column):
                    continue
                
                # Verify datasets exist (already checked above)
                from_found = any(
                    (getattr(d, "name", None) or getattr(d, "label", None)) == from_dataset
                    for d in datasets
                )
                to_found = any(
                    (getattr(d, "name", None) or getattr(d, "label", None)) == to_dataset
                    for d in datasets
                )
                
                if not from_found or not to_found:
                    errors.append(
                        f"Relationship references missing dataset(s): "
                        f"from_dataset='{from_dataset}' (found={from_found}), "
                        f"to_dataset='{to_dataset}' (found={to_found})"
                    )
                    continue
                
                logger.info("✓ Relationship validated: %s.%s -> %s.%s", from_dataset, from_column, to_dataset, to_column)
            
            # Validate metrics
            metrics = list(getattr(model, "metrics", []) or [])
            for metric in metrics:
                metric_name = getattr(metric, "name", None) or getattr(metric, "label", None)
                metric_dataset = getattr(metric, "dataset", None)
                
                if not metric_dataset:
                    errors.append(f"Metric '{metric_name}' has no dataset specified")
                    continue
                
                # Check if metric's dataset exists
                dataset_found = any(
                    (getattr(d, "name", None) or getattr(d, "label", None)) == metric_dataset
                    for d in datasets
                )
                
                if not dataset_found:
                    errors.append(
                        f"Metric '{metric_name}' references dataset '{metric_dataset}' "
                        f"which does not exist in the model"
                    )
                    continue
                
                logger.info("✓ Metric '%s' references existing dataset: %s", metric_name, metric_dataset)
            
            validation_passed = len(errors) == 0
            if validation_passed:
                logger.info("All validations passed for model on existing tables")
            else:
                logger.error("Validation failed with %d error(s)", len(errors))
            
            return validation_passed, errors
            
        except Exception as exc:
            error_msg = f"Validation check failed with exception: {exc}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)
            return False, errors

    def _create_missing_entities(
        self, cursor: Any, model: Any, full_view_name: str, is_osi: bool = False
    ) -> bool:
        """Create only the missing datasets and dimensions in existing tables.
        
        This method preserves existing table data by:
        1. Querying which datasets/tables already exist
        2. Only creating DDL for missing datasets
        3. Adding missing columns to existing datasets
        
        Args:
            cursor: Snowflake cursor
            model: SML or OSI model
            full_view_name: Fully qualified view name
            is_osi: True if model is OSI, False if SML
        
        Returns:
            True if successful, False on error
        """
        try:
            logger.info("Preserve-existing-tables: creating missing entities for %s", full_view_name)
            
            existing_tables = self.schema_manager._fetch_schema_metadata(cursor) or {}
            datasets = list(getattr(model, "datasets", []) or [])
            
            created_count = 0
            skipped_count = 0
            
            for dataset in datasets:
                dataset_name = getattr(dataset, "name", None) or getattr(dataset, "label", None)
                table_name = getattr(dataset, "table_name", None)
                schema_name = getattr(dataset, "schema_name", None) or self.config.schema_name
                
                if not table_name:
                    logger.debug("Dataset '%s' has no table_name, skipping", dataset_name)
                    continue
                
                full_table_name = f"{schema_name}.{table_name}".lower()
                
                if full_table_name in existing_tables:
                    logger.info("Dataset '%s' already exists as table %s.%s - preserving", 
                              dataset_name, schema_name, table_name)
                    skipped_count += 1
                else:
                    logger.info("Dataset '%s' (table %s.%s) does not exist - will be created", 
                              dataset_name, schema_name, table_name)
                    created_count += 1
            
            logger.info(
                "Preserve-existing-tables summary: %d datasets will be created, %d existing datasets preserved",
                created_count, skipped_count
            )
            
            return True
            
        except Exception as exc:
            logger.error("Failed to prepare missing entities: %s", exc, exc_info=True)
            return False

    def _get_existing_base_tables(self, cursor: Any, model: Any) -> dict:
        """
        Check which base tables from the model already exist in Snowflake.
        
        Returns: dict mapping table_name -> {'exists': True/False, 'columns': [...], 'column_types': {...}}
        """
        try:
            existing_tables = {}
            datasets = getattr(model, 'datasets', []) or []
            
            for dataset in datasets:
                dataset_name = getattr(dataset, 'label', None) or getattr(dataset, 'unique_name', None)
                if not dataset_name:
                    continue
                
                from semabridge.utils.name_translator import get_target_deployment_name
                source_table = getattr(dataset, "source_table", None) or dataset_name
                safe_table_name = get_target_deployment_name(source_table)
                
                # Check if table exists in INFORMATION_SCHEMA
                query = f"""
                    SELECT COLUMN_NAME, DATA_TYPE FROM "{str(self.config.database).replace('"', '""')}".INFORMATION_SCHEMA.COLUMNS
                    WHERE UPPER(TABLE_CATALOG) = UPPER('{str(self.config.database).replace("'", "''")}')
                    AND UPPER(TABLE_SCHEMA) = UPPER('{str(self.config.schema_name).replace("'", "''")}')
                    AND UPPER(TABLE_NAME) = UPPER('{safe_table_name.replace("'", "''")}')
                """
                
                cursor.execute(query)
                rows = cursor.fetchall()
                
                if rows:
                    existing_tables[dataset_name] = {
                        'exists': True,
                        'columns': [row[0] for row in rows],
                        'column_types': {row[0].upper(): row[1] for row in rows},
                        'table_name': f'{self.config.schema_name}.{safe_table_name}'
                    }
                    logger.info("Table for dataset '%s' exists: %s (%d columns)", 
                              dataset_name, safe_table_name, len(rows))
                else:
                    existing_tables[dataset_name] = {
                        'exists': False,
                        'columns': [],
                        'table_name': f'{self.config.schema_name}.{safe_table_name}'
                    }
                    logger.info("Table for dataset '%s' does NOT exist: %s", dataset_name, safe_table_name)
            
            return existing_tables
            
        except Exception as exc:
            logger.warning("Error checking existing tables: %s", exc, exc_info=True)
            return {}

    def _validate_relationships_measures_on_existing_tables(self, cursor: Any, model: Any, 
                                                           existing_tables: dict, is_osi: bool) -> list:
        """
        Validate that relationships and measures work correctly with existing tables.
        
        Returns: list of error messages (empty if all valid)
        """
        errors = []
        incompatible_datasets = set()
        
        try:
            def resolve_table_columns(dataset_name: str) -> set[str]:
                table_info = existing_tables.get(dataset_name) or {}
                return {str(col).upper() for col in table_info.get('columns', [])}

            def resolve_table_column_types(dataset_name: str) -> dict[str, str]:
                table_info = existing_tables.get(dataset_name) or {}
                return {str(col).upper(): str(dtype).upper() for col, dtype in (table_info.get('column_types') or {}).items()}

            def dataset_label(dataset_name: str) -> str:
                table_info = existing_tables.get(dataset_name) or {}
                return table_info.get('table_name', dataset_name)

            def expected_dataset(dataset_name: str) -> Any:
                if hasattr(model, 'get_dataset'):
                    return model.get_dataset(dataset_name)
                for dataset in getattr(model, 'datasets', []) or []:
                    if str(getattr(dataset, 'unique_name', '')).upper() == str(dataset_name).upper():
                        return dataset
                return None

            def normalize_type(dtype: Optional[str]) -> Optional[str]:
                if not dtype:
                    return None
                dtype = str(dtype).upper()
                if dtype in ('TEXT', 'STRING', 'VARCHAR'):
                    return 'VARCHAR'
                if dtype in ('NUMBER', 'FLOAT', 'DOUBLE', 'DECIMAL'):
                    return 'NUMBER'
                if dtype in ('INTEGER', 'INT', 'BIGINT', 'SMALLINT'):
                    return 'INTEGER'
                return dtype

            def expected_column_type(dataset_name: str, column_name: str) -> Optional[str]:
                dataset = expected_dataset(dataset_name)
                if not dataset:
                    return None
                column = getattr(dataset, 'get_column', lambda _name: None)(column_name)
                if not column:
                    return None
                data_type = getattr(column, 'data_type', None)
                return str(getattr(data_type, 'value', data_type)).upper() if data_type else None

            # Check relationships
            relationships = getattr(model, 'relationships', []) or []
            for rel in relationships:
                if not getattr(rel, 'is_active', True):
                    continue
                
                from_dataset = getattr(rel, 'from_dataset', None)
                to_dataset = getattr(rel, 'to_dataset', None)
                from_columns = [str(col).upper() for col in (getattr(rel, 'from_columns', []) or [])]
                to_columns = [str(col).upper() for col in (getattr(rel, 'to_columns', []) or [])]
                
                if from_dataset and from_dataset in existing_tables:
                    if existing_tables[from_dataset]['exists']:
                        available_columns = resolve_table_columns(from_dataset)
                        available_column_types = resolve_table_column_types(from_dataset)
                        
                        for col in from_columns:
                            expected_type = normalize_type(expected_column_type(from_dataset, col))
                            actual_type = normalize_type(available_column_types.get(col))
                            if expected_type and actual_type and expected_type != actual_type:
                                errors.append(
                                    f"Relationship {rel.unique_name}: column type mismatch for {dataset_label(from_dataset)}.{col} (expected {expected_type}, found {actual_type})"
                                )
                                incompatible_datasets.add(from_dataset)
                        
                        missing_from = [col for col in from_columns if col not in available_columns]
                        if missing_from:
                            errors.append(
                                f"Relationship {rel.unique_name}: missing from_columns {missing_from} in {dataset_label(from_dataset)}"
                            )
                            incompatible_datasets.add(from_dataset)
                
                if to_dataset and to_dataset in existing_tables:
                    if existing_tables[to_dataset]['exists']:
                        available_columns = resolve_table_columns(to_dataset)
                        available_column_types = resolve_table_column_types(to_dataset)
                        
                        for col in to_columns:
                            expected_type = normalize_type(expected_column_type(to_dataset, col))
                            actual_type = normalize_type(available_column_types.get(col))
                            if expected_type and actual_type and expected_type != actual_type:
                                errors.append(
                                    f"Relationship {rel.unique_name}: column type mismatch for {dataset_label(to_dataset)}.{col} (expected {expected_type}, found {actual_type})"
                                )
                                incompatible_datasets.add(to_dataset)
                        
                        missing_to = [col for col in to_columns if col not in available_columns]
                        if missing_to:
                            errors.append(
                                f"Relationship {rel.unique_name}: missing to_columns {missing_to} in {dataset_label(to_dataset)}"
                            )
                            incompatible_datasets.add(to_dataset)
                
                logger.info("Relationship validation: %s (from=%s, to=%s)", 
                          rel.unique_name, from_dataset, to_dataset)
            
            # Check metrics
            metrics = getattr(model, 'metrics', []) or []
            for metric in metrics:
                metric_dataset = getattr(metric, 'dataset', None)
                source_column = getattr(metric, 'source_column', None)
                if metric_dataset and metric_dataset in existing_tables:
                    if existing_tables[metric_dataset]['exists'] and source_column:
                        available_columns = resolve_table_columns(metric_dataset)
                        available_column_types = resolve_table_column_types(metric_dataset)
                        if str(source_column).upper() not in available_columns:
                            errors.append(
                                f"Metric {metric.unique_name}: source_column '{source_column}' missing in {dataset_label(metric_dataset)}"
                            )
                            incompatible_datasets.add(metric_dataset)
                        else:
                            expected_type = normalize_type(expected_column_type(metric_dataset, source_column))
                            actual_type = normalize_type(available_column_types.get(str(source_column).upper()))
                            if expected_type and actual_type and expected_type != actual_type:
                                errors.append(
                                    f"Metric {metric.unique_name}: source_column type mismatch for {dataset_label(metric_dataset)}.{source_column} (expected {expected_type}, found {actual_type})"
                                )
                                incompatible_datasets.add(metric_dataset)
                
                logger.info("Metric validation: %s (dataset=%s)", metric.unique_name, metric_dataset)
            
            if errors:
                logger.warning("Validation failed with %d error(s)", len(errors))
            else:
                logger.info("All relationships and measures validated successfully")
            
            return errors, list(incompatible_datasets)
            
        except Exception as exc:
            logger.error("Error during relationship/measure validation: %s", exc, exc_info=True)
            return [f"Validation error: {str(exc)}"]

    def _filter_ddls_for_existing_tables(self, ddls: list, existing_tables: dict) -> list:
        """
        Filter DDLs to skip CREATE TABLE statements for tables that already exist.
        Only the semantic view and relationships/measures DDL are executed.
        
        Strategy:
        1. Skip any CREATE TABLE statements for existing tables
        2. Keep the semantic view definition (CREATE OR REPLACE SEMANTIC VIEW)
        3. This allows relationships/measures to bind to existing tables
        """
        import re
        
        filtered_ddls = []
        
        for ddl in ddls:
            if not ddl or not isinstance(ddl, str):
                continue
            
            ddl_upper = ddl.upper().strip()
            
            # Keep semantic view DDLs (always execute)
            if 'CREATE OR REPLACE SEMANTIC VIEW' in ddl_upper:
                logger.info("Including semantic view DDL")
                filtered_ddls.append(ddl)
                continue
            
            # Check if this is a CREATE TABLE statement
            if ddl_upper.startswith('CREATE TABLE') or 'CREATE OR REPLACE TABLE' in ddl_upper:
                # Extract the table name from the DDL (handles fully-qualified and quoted identifiers)
                match = re.search(r'(?:CREATE\s+(?:OR\s+REPLACE\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+([^\s(]+)', ddl, re.IGNORECASE)
                if match:
                    full_name = match.group(1)
                    ddl_table_name = full_name.split('.')[-1].strip('"').strip("'").upper()
                else:
                    ddl_table_name = None
                
                skip = False
                if ddl_table_name:
                    for dataset_name, table_info in existing_tables.items():
                        if table_info['exists']:
                            # Extract just the table name from fully qualified name
                            target_name = str(table_info['table_name']).split('.')[-1].strip('"').upper()
                            # Exact match (not substring)
                            if ddl_table_name == target_name:
                                logger.info("Skipping CREATE TABLE for existing table: %s", table_info['table_name'])
                                skip = True
                                break
                
                if not skip:
                    logger.info("Including CREATE TABLE DDL (table doesn't exist yet)")
                    filtered_ddls.append(ddl)
                continue
            
            # Keep all other DDLs (measures, dimensions, etc.)
            logger.info("Including other DDL statement")
            filtered_ddls.append(ddl)
        
        logger.info("DDL filtering complete: %d original -> %d filtered DDLs", len(ddls), len(filtered_ddls))
        return filtered_ddls



    _FACT_KEYWORDS = frozenset({"FACT", "FACTS", "SALES", "TRANSACTION", "TRANSACTIONS"})

    @staticmethod
    def _tokenize_dataset_name(name: str) -> list[str]:
        """
        Split a dataset name into whole "words" so keyword matching can't be
        fooled by a keyword appearing mid-word (e.g. "Manufacturer" contains
        "fact" as a substring, "Artifact" and "Satisfaction" do too, but none
        of them are actual fact tables).

        Splits on non-alphanumeric separators (underscores, spaces, hyphens)
        and on camelCase/PascalCase boundaries, e.g. "SalesFact" -> ["Sales",
        "Fact"], but "Manufacturer" stays a single token since it has no
        internal case transition.
        """
        word_re = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+')
        tokens: list[str] = []
        for part in re.split(r'[^A-Za-z0-9]+', name or ""):
            if part:
                tokens.extend(word_re.findall(part))
        return [t.upper() for t in tokens if t]

    def _identify_fact_table(self, model):
        """
        Automatically identify the fact table in the model.
        Strategy: Table with most relationships OR most metrics OR contains 'FACT' keyword.
        """
        datasets = getattr(model, 'datasets', [])
        if not datasets:
            return None

        # Strategy 1: Look for a table whose name contains 'FACT'/'SALES'/'TRANSACTION'
        # as a whole word — not merely as a substring (e.g. "Manufacturer", "Artifact",
        # and "Satisfaction" all contain "fact" mid-word but aren't fact tables).
        for dataset in datasets:
            tokens = set(self._tokenize_dataset_name(dataset.unique_name))
            if tokens & self._FACT_KEYWORDS:
                logger.info(f"✅ Identified fact table: {dataset.unique_name} (keyword match)")
                return dataset.unique_name
        
        # Strategy 2: Table with most relationships
        relationships = getattr(model, 'relationships', [])
        from_counts = {}
        for rel in relationships:
            from_ds = getattr(rel, 'from_dataset', None)
            if from_ds:
                from_counts[from_ds] = from_counts.get(from_ds, 0) + 1
        
        if from_counts:
            max_from = max(from_counts, key=from_counts.get)
            logger.info(f"✅ Identified fact table: {max_from} (most relationships: {from_counts[max_from]})")
            return max_from
        
        # Strategy 3: Table with most metrics
        metrics = getattr(model, 'metrics', [])
        metric_counts = {}
        for metric in metrics:
            ds = getattr(metric, 'dataset', None)
            if ds:
                metric_counts[ds] = metric_counts.get(ds, 0) + 1
        
        if metric_counts:
            max_metrics = max(metric_counts, key=metric_counts.get)
            logger.info(f"✅ Identified fact table: {max_metrics} (most metrics: {metric_counts[max_metrics]})")
            return max_metrics
        
        # Fallback: first dataset
        logger.warning(f"⚠️ Using first dataset as fact table: {datasets[0].unique_name}")
        return datasets[0].unique_name

    def _get_fact_tables_needing_enrichment(self, model) -> list[str]:
        """
        Return every dataset that actually needs an enriched view, i.e. every
        distinct target_dataset surfaced by the cross-table-reference pre-compute
        analysis (the same data behind the "PRE-COMPUTE SUGGESTIONS FOR
        CROSS-TABLE REFERENCES" log). Falls back to the single best-guess fact
        table from _identify_fact_table() only when no pre-compute suggestions
        exist at all.
        """
        self.semantic_view_builder._precompute_suggestions(model)
        seen: set = set()
        fact_tables: list[str] = []
        for detail in self.semantic_view_builder.get_precompute_details():
            target = detail.get("target_dataset")
            if target and target.casefold() not in seen:
                seen.add(target.casefold())
                fact_tables.append(target)

        if fact_tables:
            return fact_tables

        fallback = self._identify_fact_table(model)
        return [fallback] if fallback else []


    def _find_source_table_for_precompute(self, target_table: str, column_name: str):
        """
        Find the source table that contains the column for pre-computation.
        Uses model relationships to trace back to dimension table.
        """
        if not hasattr(self, '_model') or not self._model:
            logger.warning("No model available for relationship lookup")
            return None
        
        relationships = getattr(self._model, 'relationships', [])
        
        # Look for relationship where target_table is the 'to' side (dimension)
        for rel in relationships:
            to_dataset = getattr(rel, 'to_dataset', None)
            if to_dataset and to_dataset.upper() == target_table.upper():
                from_dataset = getattr(rel, 'from_dataset', None)
                if from_dataset:
                    # Check if column exists in source table
                    for dataset in getattr(self._model, 'datasets', []):
                        if dataset.unique_name == from_dataset:
                            for col in getattr(dataset, 'columns', []):
                                if col.unique_name.upper() == column_name.upper():
                                    logger.info(f"✅ Found source table {from_dataset} for column {column_name}")
                                    return from_dataset
        
        # Alternative: Look for relationship where target_table is 'from' side
        for rel in relationships:
            from_dataset = getattr(rel, 'from_dataset', None)
            if from_dataset and from_dataset.upper() == target_table.upper():
                to_dataset = getattr(rel, 'to_dataset', None)
                if to_dataset:
                    logger.info(f"✅ Found source table {to_dataset} for column {column_name} (reverse relationship)")
                    return to_dataset
        
        logger.warning(f"⚠️ Could not find source table for {target_table}.{column_name}")
        return None


    def _get_join_key(self, table1: str, table2: str) -> str:
        """
        Find the join key between two tables from relationships.
        """
        if not hasattr(self, '_model') or not self._model:
            return "ID"
        
        relationships = getattr(self._model, 'relationships', [])
        
        for rel in relationships:
            from_ds = getattr(rel, 'from_dataset', None)
            to_ds = getattr(rel, 'to_dataset', None)
            
            if from_ds and to_ds:
                if (from_ds.upper() == table1.upper() and to_ds.upper() == table2.upper()) or \
                   (from_ds.upper() == table2.upper() and to_ds.upper() == table1.upper()):
                    
                    from_cols = getattr(rel, 'from_columns', [])
                    to_cols = getattr(rel, 'to_columns', [])
                    
                    if from_cols:
                        return from_cols[0].upper()
                    if to_cols:
                        return to_cols[0].upper()
        
        # Common default keys
        common_keys = ['PRODUCTID', 'ID', 'CUSTOMERID', 'BUSINESS_UNIT', 'FISCAL_YR_PERIOD']
        for key in common_keys:
            if key in table1.upper() or key in table2.upper():
                return key
        
        return "ID"


    def _find_date_table(self, model):
        """
        Dynamically find the date/calendar table in the model.
        Returns (table_name, date_column, fiscal_period_column) or None.

        Delegates to the same config-driven resolver TablesClauseBuilder
        uses (DateResolutionConfig, see date_resolution.py /
        Config/date_resolution.yaml) instead of a separate hardcoded
        keyword list. The old body here required an exact date-column name
        match AND a "fiscal"-named column to both be present, so it
        returned None (silently skipping MAX_DATE/_CURRENT_FISCAL_PERIOD
        anchor injection in _create_enriched_view) for any model whose date
        table has no fiscal-period column at all — even though the later,
        correct resolution in TablesClauseBuilder only ever required the
        date column.
        """
        from semabridge.converter.date_resolution import DateResolutionConfig

        resolution = DateResolutionConfig().resolve(model)
        if not resolution:
            return None
        return (resolution.table, resolution.date_col, resolution.monthindex_col or resolution.date_col)

    def _auto_execute_precompute_suggestions(self, model, cursor) -> None:
        """
        Automatically execute pre-compute suggestions in Snowflake.
        Only adds denormalized columns to fact/bridge tables when the column
        truly does NOT already exist in that table.
        """
        suggestions = self.semantic_view_builder._precompute_suggestions(model)

        if not suggestions:
            return

        logger.info("🚀 Auto-executing pre-compute suggestions...")

        rich_details = self.semantic_view_builder.get_precompute_details()
        if rich_details:
            logger.info(
                "Pre-compute suggestions will be projected through enriched views; "
                "skipping physical base-table mutation."
            )
            return

        # Build a live lookup: table_name_upper -> set of existing column names (upper)
        live_meta: dict[str, set[str]] = {}
        if hasattr(self, 'semantic_view_builder') and hasattr(self.semantic_view_builder, 'live_schema_metadata'):
            live_meta = {
                k.upper(): {c.upper() for c in v}
                for k, v in self.semantic_view_builder.live_schema_metadata.items()
            }

        if not live_meta:
            sf_meta = self.schema_manager._fetch_schema_metadata(cursor)
            if not sf_meta:
                datasets = list(getattr(model, "datasets", []) or [])
                sf_meta = self.schema_manager._fetch_model_table_metadata(cursor, datasets)
            if sf_meta:
                self._live_schema_metadata.update(sf_meta)
                live_meta = {
                    k.upper(): {c.upper() for c in v}
                    for k, v in sf_meta.items()
                }

        for table, cols in suggestions.items():
            table_upper = table.upper()

            # Resolve physical table name (model name may differ from Snowflake name)
            existing_cols = live_meta.get(table_upper, set())

            # Skip suggestion entirely if ALL referenced columns already exist in
            # the target table — these are DAX self-references (e.g. 'Date'[MonthIndex])
            # not actual cross-table denormalization requests.
            cols_needing_add = []
            for col in cols:
                col_upper = col.upper().replace(' ', '_')
                # Map common DAX name -> physical name (e.g. Date -> COL_DATE)
                physical = self._resolve_physical_col_name(col, existing_cols)
                if physical in existing_cols or col_upper in existing_cols:
                    logger.debug(
                        "Skipping pre-compute for %s.%s: column already exists as %s",
                        table, col, physical or col_upper
                    )
                else:
                    cols_needing_add.append(col)

            if not cols_needing_add:
                logger.info(
                    "Skipping all pre-compute suggestions for %s: columns already present", table
                )
                continue

            # Check table exists
            try:
                cursor.execute(f"SHOW TABLES LIKE '{table_upper}'")
                if not cursor.fetchone():
                    logger.warning("Table %s not found, skipping pre-compute", table)
                    continue
            except Exception:
                continue

            for col in cols_needing_add:
                col_safe = col.upper().replace(' ', '_')
                source_table = self._find_source_table_for_precompute(table, col)
                if not source_table:
                    logger.warning("Could not find source table for %s.%s; skipping", table, col)
                    continue

                # Resolve source column physical name
                source_existing = live_meta.get(source_table.upper(), set())
                src_col_phys = self._resolve_physical_col_name(col, source_existing) or col_safe

                # Resolve join keys
                from_key = self._get_directional_join_key(table, source_table, from_side=table)
                to_key = self._get_directional_join_key(table, source_table, from_side=source_table)

                # Add column if not exists
                try:
                    cursor.execute(
                        f'ALTER TABLE "{table_upper}" ADD COLUMN IF NOT EXISTS "{col_safe}" VARCHAR'
                    )
                    logger.info("Added column %s to %s", col_safe, table)
                except Exception as e:
                    logger.debug("Column %s may already exist in %s: %s", col_safe, table, e)

                # Populate using Snowflake-compatible correlated UPDATE
                update_sql = (
                    f'UPDATE "{table_upper}" t '
                    f'SET t."{col_safe}" = ('
                    f'  SELECT s."{src_col_phys}" FROM "{source_table.upper()}" s '
                    f'  WHERE s."{to_key}" = t."{from_key}" LIMIT 1'
                    f')'
                )
                try:
                    cursor.execute(update_sql)
                    logger.info("Populated %s in %s from %s", col_safe, table, source_table)
                except Exception as e:
                    logger.warning("Could not auto-populate %s: %s", col_safe, e)

        logger.info("✅ Pre-compute suggestions executed successfully")

    def _resolve_physical_col_name(self, dax_col_name: str, existing_cols: set[str]) -> str:
        """
        Map a DAX column name (potentially model-level) to its physical Snowflake column name.
        Tries: exact, COL_{name}, {name}ID, and case-insensitive match.
        """
        candidates = [
            dax_col_name.upper(),
            f"COL_{dax_col_name.upper()}",
            dax_col_name.upper().replace(' ', '_'),
        ]
        for c in candidates:
            if c in existing_cols:
                return c
        # Try substring match (e.g. DAX 'Date' might be 'COL_DATE')
        for existing in existing_cols:
            if dax_col_name.upper() in existing:
                return existing
        return dax_col_name.upper().replace(' ', '_')

    def _get_directional_join_key(self, table1: str, table2: str, from_side: str) -> str:
        """
        Return the join column for `from_side` in the relationship between table1 and table2.
        """
        if not hasattr(self, '_model') or not self._model:
            return "ID"
        for rel in getattr(self._model, 'relationships', []):
            from_ds = getattr(rel, 'from_dataset', None)
            to_ds = getattr(rel, 'to_dataset', None)
            if not from_ds or not to_ds:
                continue
            if from_ds.upper() == table1.upper() and to_ds.upper() == table2.upper():
                cols = getattr(rel, 'from_columns', []) if from_side.upper() == table1.upper() else getattr(rel, 'to_columns', [])
                return cols[0].upper() if cols else "ID"
            if from_ds.upper() == table2.upper() and to_ds.upper() == table1.upper():
                cols = getattr(rel, 'to_columns', []) if from_side.upper() == table1.upper() else getattr(rel, 'from_columns', [])
                return cols[0].upper() if cols else "ID"
        return "ID"

    def _get_dataset_by_name(self, model: Any, dataset_name: str) -> Optional[Any]:
        for dataset in getattr(model, "datasets", []) or []:
            if str(getattr(dataset, "unique_name", "")).casefold() == str(dataset_name).casefold():
                return dataset
        return None

    def _dataset_source_ref(self, model: Any, dataset_name: str) -> str:
        dataset = self._get_dataset_by_name(model, dataset_name)
        source_table_mapping = getattr(self.behavior.snowflake, "source_table_mapping", {}) or {}
        source_table = source_table_mapping.get(
            getattr(dataset, "unique_name", dataset_name),
            getattr(dataset, "source_table", None) or dataset_name,
        )
        safe_table = self._id.sanitize_table_name(source_table)
        return f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'

    def _resolve_model_column_name(self, model: Any, dataset_name: str, column_name: str) -> str:
        dataset = self._get_dataset_by_name(model, dataset_name)
        if dataset is not None:
            try:
                return self.schema_manager._resolve_physical_column_name(dataset, column_name, model=model)
            except Exception:
                pass
        return self._id.sanitize_column(column_name)

    def _relationship_edges(self, model: Any, dataset_name: str) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        for rel in getattr(model, "relationships", []) or []:
            if not getattr(rel, "is_active", True):
                continue
            from_ds = getattr(rel, "from_dataset", None)
            to_ds = getattr(rel, "to_dataset", None)
            from_cols = list(getattr(rel, "from_columns", []) or [])
            to_cols = list(getattr(rel, "to_columns", []) or [])
            if not from_ds or not to_ds or not from_cols or not to_cols:
                continue
            if str(from_ds).casefold() == str(dataset_name).casefold():
                edges.append(
                    {
                        "current_dataset": from_ds,
                        "next_dataset": to_ds,
                        "current_columns": from_cols,
                        "next_columns": to_cols,
                    }
                )
            if str(to_ds).casefold() == str(dataset_name).casefold():
                edges.append(
                    {
                        "current_dataset": to_ds,
                        "next_dataset": from_ds,
                        "current_columns": to_cols,
                        "next_columns": from_cols,
                    }
                )
        return edges

    def _find_relationship_path(self, model: Any, start_dataset: str, target_dataset: str) -> list[dict[str, Any]]:
        if str(start_dataset).casefold() == str(target_dataset).casefold():
            return []
        queue: list[tuple[str, list[dict[str, Any]]]] = [(start_dataset, [])]
        visited = {str(start_dataset).casefold()}
        while queue:
            current, path = queue.pop(0)
            for edge in self._relationship_edges(model, current):
                nxt = str(edge["next_dataset"])
                key = nxt.casefold()
                if key in visited:
                    continue
                next_path = [*path, edge]
                if key == str(target_dataset).casefold():
                    return next_path
                visited.add(key)
                queue.append((nxt, next_path))
        return []

    def _build_precomputed_column_select(
        self,
        model: Any,
        target_dataset: str,
        source_dataset: str,
        source_column: str,
        precomputed_column: str,
    ) -> Optional[str]:
        path = self._find_relationship_path(model, target_dataset, source_dataset)
        if not path:
            logger.warning(
                "No active relationship path from %s to %s; cannot precompute %s.%s",
                target_dataset,
                source_dataset,
                source_dataset,
                source_column,
            )
            return None

        first = path[0]
        from_clause = f'FROM {self._dataset_source_ref(model, first["next_dataset"])} j1'
        first_current_col = self._resolve_model_column_name(model, first["current_dataset"], first["current_columns"][0])
        first_next_col = self._resolve_model_column_name(model, first["next_dataset"], first["next_columns"][0])
        where_clause = f'f."{first_current_col}" = j1."{first_next_col}"'
        joins: list[str] = []

        for idx, edge in enumerate(path[1:], start=2):
            prev_alias = f"j{idx - 1}"
            alias = f"j{idx}"
            prev_col = self._resolve_model_column_name(model, edge["current_dataset"], edge["current_columns"][0])
            next_col = self._resolve_model_column_name(model, edge["next_dataset"], edge["next_columns"][0])
            joins.append(
                f'JOIN {self._dataset_source_ref(model, edge["next_dataset"])} {alias} '
                f'ON {prev_alias}."{prev_col}" = {alias}."{next_col}"'
            )

        source_alias = f"j{len(path)}"
        source_col = self._resolve_model_column_name(model, source_dataset, source_column)
        return (
            f'\n        , (SELECT {source_alias}."{source_col}"\n'
            f'           {from_clause}\n'
            f'           {" ".join(joins)}\n'
            f'           WHERE {where_clause}\n'
            f'           LIMIT 1) AS "{precomputed_column}"'
        )


    def _create_enriched_view(self, model, cursor, fact_table: Optional[str] = None):
        """
        Automatically create an enriched view with pre-computed columns and anchors.
        This view can be used as the source for semantic model.

        `fact_table` may be passed explicitly by callers that already know which
        dataset needs enrichment (e.g. looping over every fact table identified by
        the pre-compute analysis, or refreshing a specific *_ENRICHED view found in
        generated DDL). When omitted, falls back to the single best-guess fact
        table from _identify_fact_table().
        """
        if not fact_table:
            fact_table = self._identify_fact_table(model)
        if not fact_table:
            return None
        
        date_info = self._find_date_table(model)
        enriched_view_name = f"{fact_table}_ENRICHED"
        fact_dataset = self._get_dataset_by_name(model, fact_table)
        fact_source_name = getattr(fact_dataset, "source_table", None) or fact_table
        fact_source_safe = self._id.sanitize_table_name(fact_source_name)
        fact_source_ref = f'"{self.config.database}"."{self.config.schema_name}"."{fact_source_safe}"'
        fact_source_key = self._id.sanitize_table_name(fact_source_name).upper()
        fact_cols = {
            str(c).upper()
            for c in self._live_schema_metadata.get(fact_source_key, set())
        }
        if not fact_cols and fact_dataset is not None:
            fact_cols = {
                self._resolve_model_column_name(model, fact_table, getattr(c, "unique_name", "")).upper()
                for c in getattr(fact_dataset, "columns", []) or []
                if getattr(c, "unique_name", None)
            }
        projected_cols: set[str] = set()

        from semabridge.connectors.ddl_helpers import format_scalar_sql_literal

        def _fetch_literal(sql: str) -> Optional[str]:
            # Snowflake rejects subqueries embedded inside CREATE VIEW column
            # expressions; these anchors have no per-row dependency, so fetch
            # them once here and splice in the literal result instead.
            try:
                cursor.execute(sql)
                row = cursor.fetchone()
                return format_scalar_sql_literal(row[0] if row else None)
            except Exception as exc:
                logger.warning("Could not precompute enriched-view anchor value (%s): %s", sql[:80], exc)
                return None

        select_parts = [f"SELECT f.*"]

        if "UNITS" in fact_cols:
            total_units_literal = _fetch_literal(f'SELECT SUM("UNITS") FROM {fact_source_ref}')
            if total_units_literal is not None:
                select_parts.append(f'\n        , {total_units_literal} AS "TOTAL_UNITS_ALL"')
                projected_cols.add("TOTAL_UNITS_ALL")
        
        # Add generic cross-dataset precomputed columns into the fact enriched view.
        suggestions = self.semantic_view_builder._precompute_suggestions(model)
        _ = suggestions
        for detail in self.semantic_view_builder.get_precompute_details():
            if str(detail.get("target_dataset", "")).casefold() != str(fact_table).casefold():
                continue
            projection = self._build_precomputed_column_select(
                model=model,
                target_dataset=detail["target_dataset"],
                source_dataset=detail["source_dataset"],
                source_column=detail["source_column"],
                precomputed_column=detail["precomputed_column"],
            )
            if projection:
                select_parts.append(projection)
                projected_cols.add(detail["precomputed_column"].upper())
        
        # Add YTD anchor
        if date_info:
            date_table, date_col, fiscal_col = date_info
            source_table_mapping = getattr(self.behavior.snowflake, "source_table_mapping", {}) or {}

            date_dataset = next(
                (d for d in getattr(model, "datasets", []) or [] if d.unique_name == date_table),
                None,
            )

            if date_dataset:
                resolved_source_table = source_table_mapping.get(
                    date_dataset.unique_name,
                    date_dataset.source_table or date_dataset.unique_name,
                )
                safe_table = self._id.sanitize_table_name(resolved_source_table)
                date_table_ref = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'
                resolved_date_col = self.schema_manager._resolve_physical_column_name(date_dataset, date_col, model=model)
                resolved_fiscal_col = self.schema_manager._resolve_physical_column_name(date_dataset, fiscal_col, model=model)
            else:
                safe_table = self._id.sanitize_table_name(date_table)
                date_table_ref = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'
                resolved_date_col = self._id.sanitize_column(date_col)
                resolved_fiscal_col = self._id.sanitize_column(fiscal_col)

            # Table selection for each anchor must be driven by which table
            # actually owns the column — never by loop order or first-match.
            # Mirrors the "UNITS" in fact_cols" gate above: only fire the
            # MAX_DATE probe against fact_source_ref when fact_table's own
            # columns actually contain resolved_date_col (this fact table may
            # not be the one the date anchor belongs to — see the KPI/SalesFact
            # multi-fact-table regression this guard was added to fix).
            if resolved_date_col in fact_cols:
                max_date_literal = _fetch_literal(f'SELECT MAX("{resolved_date_col}") FROM {fact_source_ref}')
                if max_date_literal is not None:
                    select_parts.append(f'\n            , {max_date_literal} AS "MAX_DATE"')
                    projected_cols.add("MAX_DATE")

            # Same principle for the fiscal-period anchor, but checked against
            # the date table's own columns (it queries date_table_ref, not
            # fact_source_ref).
            date_source_key = self._id.sanitize_table_name(
                (date_dataset.source_table or date_dataset.unique_name) if date_dataset else date_table
            ).upper()
            date_cols = {
                str(c).upper()
                for c in self._live_schema_metadata.get(date_source_key, set())
            }
            if not date_cols and date_dataset is not None:
                date_cols = {
                    self._resolve_model_column_name(model, date_dataset.unique_name, getattr(c, "unique_name", "")).upper()
                    for c in getattr(date_dataset, "columns", []) or []
                    if getattr(c, "unique_name", None)
                }
            if resolved_fiscal_col in date_cols and resolved_date_col in date_cols:
                fiscal_period_literal = _fetch_literal(
                    f'SELECT MAX("{resolved_fiscal_col}") FROM {date_table_ref} '
                    f'WHERE "{resolved_date_col}" = CURRENT_DATE()'
                )
                if fiscal_period_literal is not None:
                    select_parts.append(f'\n            , {fiscal_period_literal} AS "_CURRENT_FISCAL_PERIOD"')
                    projected_cols.add("_CURRENT_FISCAL_PERIOD")
        
        select_parts.append(f"FROM {fact_source_ref} f")
        
        create_view_sql = "\n".join(select_parts)
        full_sql = f"CREATE OR REPLACE VIEW {enriched_view_name} AS\n{create_view_sql}"
        
        try:
            cursor.execute(full_sql)
            enriched_cols = set(fact_cols) | projected_cols
            if enriched_cols:
                self._live_schema_metadata[enriched_view_name.upper()] = enriched_cols
                if hasattr(self, "semantic_view_builder"):
                    self.semantic_view_builder.live_schema_metadata[enriched_view_name.upper()] = enriched_cols
            logger.info(f"✅ Created enriched view: {enriched_view_name}")
            return enriched_view_name
        except Exception as e:
            logger.warning(f"Failed to create enriched view: {e}")
            return None

    # =========================================================================
    # BACKWARD COMPATIBILITY PROXY METHODS
    # =========================================================================

    def _validate_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None
    ) -> Tuple[bool, Optional[str]]:
        return self.translator._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_names
        )

    def _normalize_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
        preferred_table_alias: Optional[str] = None,
        metric_to_alias: Optional[Dict[str, str]] = None
    ) -> str:
        return self.translator._normalize_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_names, preferred_table_alias, metric_to_alias
        )

    def _build_safe_sum_sql(self, expr_sql: str, identifier_hint: Optional[str] = None) -> str:
        return self.measure_synchronizer._build_safe_sum_sql(expr_sql, identifier_hint)

    def generate_semantic_view_tiered(
        self,
        model_name: str,
        shadow_table: str,
        triage_results: Dict[str, Any],
        grain_dimensions: list[str]
    ) -> str:
        return self.measure_synchronizer.generate_semantic_view_tiered(
            model_name, shadow_table, triage_results, grain_dimensions
        )

    def _try_basic_dax_metric_fallback_expression(
        self,
        *,
        metric: Any,
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
        model: Optional[Any] = None,
        dataset_by_name: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        return self.translator._try_basic_dax_metric_fallback_expression(
            metric=metric,
            table_alias=table_alias,
            dataset_col_lookup=dataset_col_lookup,
            model=model,
            dataset_by_name=dataset_by_name
        )

    def _build_known_metric_fallback_expression(self, *args, **kwargs) -> None:
        return None

    def _rewrite_window_metric_expression(self, metric_sql: str, preferred_table_alias: Optional[str] = None) -> str:
        return self.translator._rewrite_window_metric_expression(metric_sql, preferred_table_alias)

    def _dedupe_qualified_column_tokens(self, sql: str) -> str:
        return self.translator._dedupe_qualified_column_tokens(sql)

    def _resolve_metric_emission_alias(
        self,
        default_alias: str,
        expr_sql: str,
        dataset_aliases: Dict[str, str],
        metric_to_alias: Optional[Dict[str, str]] = None
    ) -> str:
        from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
        builder = MetricsClauseBuilder(
            identifier_sanitizer=self._id,
            schema_manager=self.schema_manager,
            sanitizer=None,
            translator=self.translator,
            config=self.config,
            dup_name_repo=self._dup_name_repo
        )
        fact_aliases = set()
        if hasattr(self, "_model") and self._model:
            for ds in getattr(self._model, "datasets", []):
                if getattr(ds, "is_fact", False) and ds.unique_name in dataset_aliases:
                    fact_aliases.add(dataset_aliases[ds.unique_name])
        return builder._resolve_metric_emission_alias(default_alias, expr_sql, dataset_aliases, fact_aliases)

    def _build_schema_validation_map(self, sml: Any) -> Dict[str, set[str]]:
        schema_map = {}
        for ds in getattr(sml, "datasets", []):
            columns = set()
            for col in getattr(ds, "columns", []):
                if getattr(col, "source_expression", None) is None:
                    columns.add(col.unique_name.upper())
            schema_map[ds.unique_name.lower()] = columns
        return schema_map

    def _generate_semantic_view(self, sml: Any) -> str:
        return self.semantic_view_builder._generate_semantic_view(sml)

    def _generate_semantic_view_from_osi(self, osi: Any) -> str:
        return self.semantic_view_builder._generate_semantic_view_from_osi(osi)
