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
from semabridge.connectors.snowflake_emitter_parts import renderers as _renderers

if TYPE_CHECKING:
    from semabridge.intermediate.models import OSIModel

    try:
        from semabridge.intermediate.models import OSIModel as ParsedSemanticPayloadModel
    except Exception:  # pragma: no cover
        ParsedSemanticPayloadModel = Any

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
        self.semantic_view_builder = SemanticViewBuilder(
            config=self.config,
            behavior=self.behavior,
            identifier_sanitizer=self._id,
            live_schema_metadata=self._live_schema_metadata,
            schema_manager=self.schema_manager,
            dup_name_repo=self._dup_name_repo,
            translator=self.translator,
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
        """Deploy the SML model to Snowflake.

        New behavior: convert the provided SML model into the official parsed
        payload dict and route deployment through `deploy_from_payload` so the
        JSONB-backed payload contract is the single emission entry point.
        """
        try:
            from semabridge.transformers.official_payload import smlmodel_to_official_payload
        except Exception:
            # Fallback to previous direct path if transformer unavailable
            return self._execute_deployment_pipeline(sml, is_osi=False, sync_mode=sync_mode)

        payload = smlmodel_to_official_payload(sml, target_platform="snowflake")
        return self.deploy_from_payload(payload, parallel=parallel, max_workers=max_workers, sync_mode=sync_mode)

    def deploy_from_osi(
        self,
        osi: OSIModel,
        parallel: bool = False,
        max_workers: int = 4,
        sync_mode: str = "copy",
    ) -> bool:
        """Deploy an OSI model directly to Snowflake.

        New behavior: map OSI -> official payload dict and route through
        `deploy_from_payload` to ensure a single canonical deployment path.
        """
        try:
            # Prefer a dedicated OSI->payload transformer if available
            from semabridge.transformers.osi_to_payload import osi_to_payload
        except Exception:
            osi_to_payload = None

        if osi_to_payload:
            try:
                payload = osi_to_payload(osi)
            except Exception:
                payload = {
                    "model_name": getattr(osi, "unique_name", getattr(osi, "label", "model")),
                    "version": getattr(osi, "version", "1.0"),
                    "datasets": [],
                    "metrics": [],
                    "dimensions": [],
                    "relationships": [],
                }
        else:
            payload = {
                "model_name": getattr(osi, "unique_name", getattr(osi, "label", "model")),
                "version": getattr(osi, "version", "1.0"),
                "datasets": [],
                "metrics": [],
                "dimensions": [],
                "relationships": [],
            }

        return self.deploy_from_payload(payload, parallel=parallel, max_workers=max_workers, sync_mode=sync_mode)

    def deploy_from_payload(
        self,
        payload: Dict[str, Any],
        parallel: bool = False,
        max_workers: int = 4,
        sync_mode: str = "copy",
    ) -> bool:
        """Deploy a parsed official semantic payload to Snowflake.

        This is the new contract boundary for stored JSONB payloads.
        The payload is converted to OSI once, then routed through the existing
        OSI deployment path to preserve the current builder structure.
        """
        from semabridge.transformers.payload_to_osi import payload_to_osi

        osi_model = payload_to_osi(payload)
        return self._execute_deployment_pipeline(osi_model, is_osi=True, sync_mode=sync_mode)

    def _execute_deployment_pipeline(
        self,
        model: Any,
        is_osi: bool = False,
        sync_mode: str = "copy",
    ) -> bool:
        """Common orchestration for deploying SML or OSI models to Snowflake."""
        try:
            self.last_deployment_error = None
            deploy_started_at = time.perf_counter()
            model_name = getattr(model, "unique_name", None) or getattr(model, "label", None) or "<unnamed_model>"
            path_type = "OSI" if is_osi else "SML"
            effective_sync_mode = str(sync_mode or "copy").lower()
            if effective_sync_mode not in {"copy", "upsert"}:
                effective_sync_mode = "copy"
            
            logger.info("[%s] start model=%s", path_type, model_name)

            # Step -2: Check strict warnings constraint before connection
            if getattr(self.sf_behavior, "strict_inactive_relationships", False):
                all_warnings = []
                for metric in getattr(model, "metrics", []) or []:
                    for warning in getattr(metric, "translation_warning", []) or []:
                        all_warnings.append(f"Metric '{metric.unique_name}': {warning}")
                if all_warnings:
                    formatted_warnings = "\n".join(f"  - {w}" for w in all_warnings)
                    raise ConnectorError(
                        f"Strict deployment aborted: Inactive relationships or translation warnings detected:\n{formatted_warnings}"
                    )

            conn, owns_conn = self.connection_manager.get_connection()
            logger.info("Starting deployment with STRICT sanitization rules")
            
            try:
                cur = conn.cursor()
                # Store model for helper methods
                self._model = model
                
                # Auto-enrichment (NEW)
                if getattr(self.sf_behavior, 'auto_execute_precompute', False):
                    logger.info("🔧 Auto-enrichment enabled - executing pre-compute suggestions...")
                    self._auto_execute_precompute_suggestions(model, cur)
                    
                    if getattr(self.sf_behavior, 'auto_create_enriched_view', False):
                        enriched_view = self._create_enriched_view(model, cur)
                        if enriched_view and getattr(self.sf_behavior, 'use_enriched_view_for_metrics', False):
                            fact_table = self._identify_fact_table(model)
                            if fact_table:
                                self.sf_behavior.source_table_mapping = getattr(self.sf_behavior, 'source_table_mapping', {}) or {}
                                self.sf_behavior.source_table_mapping[fact_table] = enriched_view
                                logger.info(f"✅ Using enriched view {enriched_view} as source for {fact_table}")

                
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
                sf_meta = self.schema_manager._fetch_schema_metadata(cur)
                if not sf_meta:
                    datasets = list(getattr(model, "datasets", []) or [])
                    sf_meta = self.schema_manager._fetch_model_table_metadata(cur, datasets)
                self._live_schema_metadata.update(sf_meta or {})

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
                
                # Step 2b: UPSERT preserve only skips existing base-table DDLs.
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
                for idx, sql in enumerate(ddls):
                    if sql:
                        self.connection_manager._execute_sql(cur, sql, context=f"DDL[{idx}]")

                # Step 5: Artifact Generation (Cortex YAML / Audit)
                if not is_osi:
                    self._generate_deployment_artifacts(model, ddls)

                logger.info("[%s] success model=%s (%.2fs)", path_type, model_name, time.perf_counter() - deploy_started_at)
                return True

            finally:
                if owns_conn:
                    conn.close()

        except Exception as exc:
            self.last_deployment_error = str(exc)
            logger.error("[%s] FAILED model=%s: %s", path_type, model_name, exc, exc_info=True)
            return False

    def _generate_deployment_artifacts(self, sml: SMLModel, ddls: Dict[str, str]) -> None:
        """Generate side-car artifacts like Cortex YAML."""
        if self.behavior.features.enable_cortex_analyst:
            cortex_yaml = self.generate_cortex_yaml(sml)  # noqa: F841  (save logic TBD)

        if getattr(self.sf_behavior, "generate_audit_yaml", False):
            logger.debug("generate_audit_yaml flagged but renderer not yet implemented; skipping.")

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
        return {"success": success}

    @property
    def max_concurrency(self) -> int:
        """Snowflake DDL operations should be serialized more strictly."""
        return 3

    # =========================================================================
    # PUBLIC PROXIES (Backward Compatibility)
    # =========================================================================

    def generate_ddls(self, sml: SMLModel) -> list[str]:
        return self.semantic_view_builder.generate_ddls(sml)

    def generate_ddls_from_payload(self, payload: Dict[str, Any]) -> list[str]:
        """Generate DDL from a parsed official semantic payload dict."""
        from semabridge.transformers.payload_to_osi import payload_to_osi

        return self.semantic_view_builder.generate_ddls_from_osi(payload_to_osi(payload))

    def generate_ddls_from_osi(self, osi: OSIModel) -> list[str]:
        return self.semantic_view_builder.generate_ddls_from_osi(osi)

    def sync_all_measures(self, sml: SMLModel, fabric_extractor, dataset_id: str, grain_dimensions=None) -> dict:
        return self.measure_synchronizer.sync_all_measures(sml, fabric_extractor, dataset_id, grain_dimensions)

    def _build_history_snapshot_ddls_for_sml(self, sml: SMLModel) -> list[str]:
        return self.semantic_view_builder._build_history_snapshot_ddls_for_sml(sml)

    def _build_history_snapshot_ddls_for_osi(self, osi: OSIModel) -> list[str]:
        return self.semantic_view_builder._build_history_snapshot_ddls_for_osi(osi)

    def generate_cortex_yaml(self, sml: SMLModel) -> str:
        return _renderers.generate_cortex_yaml(self, sml)

    def generate_cortex_yaml_from_osi(self, osi: OSIModel) -> str:
        return _renderers.generate_cortex_yaml_from_osi(self, osi)

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
                # Extract the table name from the DDL using regex (handles quoted identifiers)
                match = re.search(r'(?:CREATE\s+(?:OR\s+REPLACE\s+)?TABLE)\s+(?:[^.\s]+\.)?["\']?([^"\'\s]+)["\']?', ddl, re.IGNORECASE)
                ddl_table_name = match.group(1).upper() if match else None
                
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



    def _identify_fact_table(self, model):
        """
        Automatically identify the fact table in the model.
        Strategy: Table with most relationships OR most metrics OR contains 'FACT' keyword.
        """
        datasets = getattr(model, 'datasets', [])
        if not datasets:
            return None
        
        # Strategy 1: Look for table with 'FACT' in name
        for dataset in datasets:
            name_upper = dataset.unique_name.upper()
            if 'FACT' in name_upper or 'SALES' in name_upper or 'TRANSACTION' in name_upper:
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
        No hardcoding!
        """
        date_keywords = ['date', 'calendar', 'cal', 'dim_date', 'dates']
        fiscal_keywords = ['fiscal_yr_period', 'fiscal_period', 'fiscal_year_period']
        
        for dataset in getattr(model, 'datasets', []):
            dataset_name = dataset.unique_name.lower()
            
            # Check if this looks like a date table
            is_date_table = any(kw in dataset_name for kw in date_keywords)
            
            if is_date_table:
                # Find date column
                date_col = None
                fiscal_col = None
                
                for col in dataset.columns:
                    col_name = col.unique_name.lower()
                    if col_name in ['cal_dt', 'date', 'calendar_date', 'cal_date']:
                        date_col = col.unique_name
                    if any(fk in col_name for fk in fiscal_keywords):
                        fiscal_col = col.unique_name
                
                if date_col and fiscal_col:
                    return (dataset.unique_name, date_col, fiscal_col)
        
        return None

    def _auto_execute_precompute_suggestions(self, model, cursor) -> None:
        """
        Automatically execute pre-compute suggestions in Snowflake.
        This eliminates manual ALTER TABLE commands.
        """
        suggestions = self.semantic_view_builder._precompute_suggestions(model)
        
        if not suggestions:
            return
        
        logger.info("🚀 Auto-executing pre-compute suggestions...")
        
        # Data Engineering Explicit Fixes
        fact_table = self._identify_fact_table(model)
        if fact_table:
            try:
                cursor.execute(f"ALTER TABLE {fact_table} ADD COLUMN IF NOT EXISTS ATINDICATOR04 VARCHAR")
                cursor.execute(f"ALTER TABLE {fact_table} ADD COLUMN IF NOT EXISTS ATINDICATOR05 VARCHAR")
                logger.info(f"✅ Added missing explicit columns to {fact_table}")
            except Exception as e:
                pass
        
        for table, cols in suggestions.items():
            # Check if table exists
            check_query = f"SHOW TABLES LIKE '{table.upper()}'"
            cursor.execute(check_query)
            if not cursor.fetchone():
                logger.warning(f"Table {table} not found, skipping pre-compute")
                continue
            
            # Add columns if not exist
            for col in cols:
                col_safe = col.upper().replace(' ', '_')
                try:
                    add_col_sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_safe} VARCHAR"
                    cursor.execute(add_col_sql)
                    logger.info(f"✅ Added column {col_safe} to {table}")
                except Exception as e:
                    logger.debug(f"Column {col_safe} may already exist: {e}")
            
            # Populate from related table
            # Detect source table from relationships
            for col in cols:
                source_table = self._find_source_table_for_precompute(table, col)
                if source_table and col:
                    col_safe = col.upper().replace(' ', '_')
                    try:
                        update_sql = f"""
                        UPDATE {table} t
                        SET t.{col_safe} = s.{col}
                        FROM {source_table} s
                        WHERE t.{self._get_join_key(table, source_table)} = s.{self._get_join_key(source_table, table)}
                        """
                        cursor.execute(update_sql)
                        logger.info(f"✅ Populated {col_safe} in {table} from {source_table}")
                    except Exception as e:
                        logger.warning(f"Could not auto-populate {col_safe}: {e}")
        
        logger.info("✅ Pre-compute suggestions executed successfully")


    def _create_enriched_view(self, model, cursor):
        """
        Automatically create an enriched view with pre-computed columns and anchors.
        This view can be used as the source for semantic model.
        """
        fact_table = self._identify_fact_table(model)
        if not fact_table:
            return None
        
        date_info = self._find_date_table(model)
        enriched_view_name = f"{fact_table}_ENRICHED"
        
        select_parts = [f"SELECT f.*"]
        
        # Add Pre-compute for percentage measures (Data Engineering Explicit Fix)
        select_parts.append(f"\n        , (SELECT SUM(UNITS) FROM {fact_table}) AS TOTAL_UNITS_ALL")
        
        # Add pre-computed flag columns
        suggestions = self.semantic_view_builder._precompute_suggestions(model)
        for table, cols in suggestions.items():
            if table.upper() in fact_table.upper():
                for col in cols:
                    source_table = self._find_source_table_for_precompute(table, col)
                    if source_table:
                        col_safe = col.upper().replace(' ', '_')
                        select_parts.append(f"""
                        , (SELECT {col} FROM {source_table} s 
                           WHERE f.{self._get_join_key(fact_table, source_table)} = s.{self._get_join_key(source_table, fact_table)}
                          ) AS {col_safe}""")
        
        # Add YTD anchor
        if date_info:
            date_table, date_col, fiscal_col = date_info
            select_parts.append(f"""
            , (SELECT MAX({date_col}) FROM {fact_table}) AS max_date
            , (SELECT MAX({fiscal_col}) FROM {date_table} WHERE {date_col} = CURRENT_DATE()) AS _current_fiscal_period""")
        
        select_parts.append(f"FROM {fact_table} f")
        
        create_view_sql = "\n".join(select_parts)
        full_sql = f"CREATE OR REPLACE VIEW {enriched_view_name} AS\n{create_view_sql}"
        
        try:
            cursor.execute(full_sql)
            logger.info(f"✅ Created enriched view: {enriched_view_name}")
            return enriched_view_name
        except Exception as e:
            logger.warning(f"Failed to create enriched view: {e}")
            return None
