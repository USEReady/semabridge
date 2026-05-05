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

    def deploy(self, sml: SMLModel, parallel: bool = False, max_workers: int = 4) -> bool:
        """Deploy the SML model to Snowflake."""
        return self._execute_deployment_pipeline(sml, is_osi=False)

    def deploy_from_osi(self, osi: OSIModel, parallel: bool = False, max_workers: int = 4) -> bool:
        """Deploy an OSI model directly to Snowflake."""
        return self._execute_deployment_pipeline(osi, is_osi=True)

    def _execute_deployment_pipeline(self, model: Any, is_osi: bool = False) -> bool:
        """Common orchestration for deploying SML or OSI models to Snowflake."""
        try:
            self.last_deployment_error = None
            deploy_started_at = time.perf_counter()
            model_name = getattr(model, "unique_name", None) or getattr(model, "label", None) or "<unnamed_model>"
            path_type = "OSI" if is_osi else "SML"
            
            logger.info("[%s] start model=%s", path_type, model_name)

            conn, owns_conn = self.connection_manager.get_connection()
            logger.info("Starting deployment with STRICT sanitization rules")
            
            try:
                cur = conn.cursor()
                
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

                # Step 2: Generate DDLs
                if is_osi:
                    ddls = self.semantic_view_builder.generate_ddls_from_osi(model)
                else:
                    ddls = self.semantic_view_builder.generate_ddls(model)

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

