"""
Snowflake Emitter.
Generates and executes Snowflake Semantic View DDL and Cortex Analyst YAML from SML models.
"""

from __future__ import annotations

import os
import time
import re
import yaml
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

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
from semabridge.extractor.dynamic_extractor import DynamicSchemaExtractor
from typing import List

if TYPE_CHECKING:
    from semabridge.intermediate.models import OSIModel

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Central SQL date-function sanitizer
# ---------------------------------------------------------------------------
_DATE_PART_RE = re.compile(
    r"\bDATE_PART\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND|DOW|DOY|EPOCH|TIMEZONE_HOUR|TIMEZONE_MINUTE)['\"]* *,",
    re.IGNORECASE,
)
_DATE_TRUNC_RE = re.compile(
    r"\bDATE_TRUNC\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)['\"]* *,",
    re.IGNORECASE,
)
_DATEADD_RE = re.compile(
    r"\bDATEADD\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)['\"]* *,",
    re.IGNORECASE,
)
_DATEDIFF_RE = re.compile(
    r"\bDATEDIFF\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)['\"]* *,",
    re.IGNORECASE,
)

_DATE_FUNC_QUALIFIED_RE = re.compile(
    r'\b(DATE_TRUNC|DATEADD|DATEDIFF|DATE_PART)\s*\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?'
    r'"?(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)"?\s*,',
    re.IGNORECASE,
)


def _sanitize_snowflake_date_functions(sql: str) -> str:
    """Ensure Snowflake date-function part arguments are single-quoted strings."""
    if not sql:
        return sql

    def _repl_qualified(m: re.Match) -> str:
        fn = m.group(1)
        part = m.group(2).lower()
        return f"{fn}('{part}',"
    sql = _DATE_FUNC_QUALIFIED_RE.sub(_repl_qualified, sql)

    def _repl(fn_name: str) -> Callable[[re.Match], str]:
        def _inner(m: re.Match) -> str:
            part = m.group(1).strip("\"'").lower()
            return f"{fn_name}('{part}',"
        return _inner

    sql = _DATE_PART_RE.sub(_repl("DATE_PART"), sql)
    sql = _DATE_TRUNC_RE.sub(_repl("DATE_TRUNC"), sql)
    sql = _DATEADD_RE.sub(_repl("DATEADD"), sql)
    sql = _DATEDIFF_RE.sub(_repl("DATEDIFF"), sql)

    try:
        import sqlglot
        from sqlglot import exp

        ast = sqlglot.parse_one(sql, read="snowflake")
        _DATE_PART_KEYWORDS = frozenset({
            "year", "month", "day", "week", "quarter",
            "hour", "minute", "second", "dow", "doy",
            "epoch", "timezone_hour", "timezone_minute",
        })

        def _fix_unit_arg(node_unit):
            if node_unit is None:
                return None
            if isinstance(node_unit, exp.Var):
                val = node_unit.this.lower()
                return exp.Var(this=val) if val in _DATE_PART_KEYWORDS else None
            if isinstance(node_unit, (exp.Column, exp.Identifier)):
                col_name = (node_unit.name or "").lower()
                return exp.Var(this=col_name) if col_name in _DATE_PART_KEYWORDS else None
            if isinstance(node_unit, exp.Literal) and node_unit.is_string:
                val = node_unit.this.lower()
                return exp.Var(this=val) if val in _DATE_PART_KEYWORDS else None
            return None

        for node in list(ast.find_all(exp.TimestampTrunc)):
            fixed = _fix_unit_arg(node.args.get("unit"))
            if fixed is not None:
                node.set("unit", fixed)
        for node in list(ast.find_all(exp.DateAdd)):
            fixed = _fix_unit_arg(node.args.get("unit"))
            if fixed is not None:
                node.set("unit", fixed)
        for node in list(ast.find_all(exp.DateDiff)):
            fixed = _fix_unit_arg(node.args.get("unit"))
            if fixed is not None:
                node.set("unit", fixed)
        for node in list(ast.find_all(exp.Extract)):
            part = node.args.get("this")
            if isinstance(part, exp.Var):
                val = part.this.lower()
                if val in _DATE_PART_KEYWORDS:
                    node.set("this", exp.Var(this=val))
        for node in list(ast.find_all(exp.Anonymous)):
            fn = node.name.lower()
            if fn in {"dateadd", "datediff", "date_part", "date_trunc"}:
                args = list(node.expressions)
                if args:
                    fixed = _fix_unit_arg(args[0])
                    if fixed is not None:
                        args[0].replace(exp.Literal.string(fixed.this))
        sql = ast.sql(dialect="snowflake")
    except Exception:
        pass
    return sql


class MissingSourceTableWarning(UserWarning):
    pass


class SnowflakeEmitter(BaseEmitter):
    def __init__(self, config: SnowflakeConfig, behavior: Optional[ConnectorBehavior] = None):
        self.config = config
        self.behavior = behavior or ConnectorBehavior()
        self.sf_behavior = self.behavior.snowflake
        self.last_deployment_error: Optional[str] = None

        self._id = IdentifierSanitizer(
            force_uppercase=self.behavior.compatibility.force_uppercase,
            always_quote=self.sf_behavior.quote_identifiers,
            suppress_reserved=self.behavior.compatibility.suppress_reserved_words,
            additional_reserved=set(getattr(self.behavior.compatibility, 'additional_reserved_words', []) or []),
        )

        self._live_schema_metadata: Dict[str, set[str]] = {}
        self._verified_tables: set[str] = set()
        self._enriched_view_mapping: Dict[str, str] = {}

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
            identifier_sanitizer=self._id,
            config=self.config
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
        self.semantic_view_builder.emitter = self

        if not self.sf_behavior.dynamic.detection_patterns:
            try:
                config_path = Path(__file__).parent.parent.parent.parent.parent / "config" / "translation_rules.yaml"
                if config_path.exists():
                    with open(config_path, "r") as f:
                        cfg = yaml.safe_load(f) or {}
                        self.sf_behavior.dynamic.detection_patterns = cfg.get("detection_patterns", {})
                        dyn_anchors = cfg.get("dynamic_anchors", {})
                        if "max_date_anchor" in dyn_anchors:
                            self.sf_behavior.dynamic.max_date_anchor_name = dyn_anchors["max_date_anchor"]
                        if "min_date_anchor" in dyn_anchors:
                            self.sf_behavior.dynamic.min_date_anchor_name = dyn_anchors["min_date_anchor"]
                        if "max_monthindex_anchor" in dyn_anchors:
                            self.sf_behavior.dynamic.max_monthindex_anchor_name = dyn_anchors["max_monthindex_anchor"]
                        if "fiscal_period_anchor" in dyn_anchors:
                            self.sf_behavior.dynamic.fiscal_period_anchor_name = dyn_anchors["fiscal_period_anchor"]
                        if "fiscal_period_column" in dyn_anchors:
                            self.sf_behavior.dynamic.fiscal_period_column = dyn_anchors["fiscal_period_column"]
                        if "date_column" in dyn_anchors:
                            self.sf_behavior.dynamic.date_column = dyn_anchors["date_column"]
            except Exception as e:
                logger.warning(f"Failed to load translation_rules.yaml into dynamic config: {e}")

    def _invalidate_schema_cache(self) -> None:
        self._live_schema_metadata.clear()
        logger.debug("Schema metadata cache invalidated.")

    def deploy(self, sml: SMLModel, parallel: bool = False, max_workers: int = 4, sync_mode: str = "copy") -> bool:
        return self._execute_deployment_pipeline(sml, is_osi=False, sync_mode=sync_mode)

    def deploy_from_osi(self, osi: OSIModel, parallel: bool = False, max_workers: int = 4, sync_mode: str = "copy") -> bool:
        return self._execute_deployment_pipeline(osi, is_osi=True, sync_mode=sync_mode)

    def _get_existing_metrics(self, cursor, view_name: str) -> dict:
        cursor.execute(f'DESCRIBE SEMANTIC VIEW {view_name}')
        col_names = [desc[0].upper() for desc in cursor.description]
        name_col = None
        expr_col = None
        for col in col_names:
            if col in ('NAME', 'METRIC_NAME', 'METRIC'):
                name_col = col
            elif col in ('EXPRESSION', 'EXPR', 'DEFINITION', 'METRIC_EXPRESSION'):
                expr_col = col
        if not name_col or not expr_col:
            logger.warning(f"Cannot identify metric columns in DESCRIBE output. Columns: {col_names}")
            return {}
        metrics = {}
        for row in cursor.fetchall():
            row_dict = dict(zip(col_names, row))
            metric_name = row_dict.get(name_col)
            metric_expr = row_dict.get(expr_col)
            type_val = row_dict.get('TYPE', '').upper() if 'TYPE' in col_names else 'METRIC'
            if metric_name and metric_expr and type_val == 'METRIC':
                metrics[metric_name] = metric_expr
        return metrics

    def _extract_metrics_from_ddl(self, ddl: str) -> dict:
        metrics = {}
        pattern = r'(?:METRICS|MEASURES)\s*\(\s*([^)]+)\s*\)'
        match = re.search(pattern, ddl, re.IGNORECASE | re.DOTALL)
        if not match:
            return metrics
        metrics_block = match.group(1)
        metric_pattern = r'"([^"]+)"\s+AS\s+([^,]+(?:\([^)]*\)[^,]*)*)'
        for metric_match in re.finditer(metric_pattern, metrics_block, re.IGNORECASE):
            name = metric_match.group(1)
            definition = metric_match.group(2).strip()
            metrics[name] = definition
        return metrics

    def _normalize_metric_definition(self, definition: str) -> str:
        normalized = ' '.join(definition.split())
        normalized = normalized.upper()
        normalized = normalized.rstrip(';')
        return normalized

    def _compare_metric_definitions(self, def1: str, def2: str) -> bool:
        return self._normalize_metric_definition(def1) == self._normalize_metric_definition(def2)

    def _sync_metrics_dynamic(self, cursor, view_name: str, new_metrics: dict, existing_metrics: dict) -> dict:
        to_set = []
        to_keep = []
        for name, new_def in new_metrics.items():
            if name not in existing_metrics or not self._compare_metric_definitions(new_def, existing_metrics[name]):
                to_set.append((name, new_def))
            else:
                to_keep.append(name)
        if to_set:
            metrics_list = ', '.join([f'"{name}" AS {defn}' for name, defn in to_set])
            sql = f'ALTER SEMANTIC VIEW {view_name} SET METRICS ({metrics_list})'
            sql = _sanitize_snowflake_date_functions(sql)
            try:
                cursor.execute(sql)
                logger.info(f"✅ Set {len(to_set)} metrics in bulk on {view_name}")
            except Exception as e:
                logger.warning(f"Failed to set metrics in bulk, falling back to individual SETs. Error: {e}")
                for name, defn in to_set:
                    defn_fixed = _sanitize_snowflake_date_functions(defn)
                    try:
                        cursor.execute(f'ALTER SEMANTIC VIEW {view_name} SET METRICS ("{name}" AS {defn_fixed})')
                        logger.info(f"✅ Set metric: {name}")
                    except Exception as e2:
                        logger.error(f"Failed to set metric {name}: {e2}")
                        raise Exception(f"Smart Upsert failed during SET METRICS for {name}: {e2}")
        return {
            "added": 0,
            "updated": len(to_set),
            "kept": len(to_keep),
            "added_names": [],
            "updated_names": [n for n, _ in to_set]
        }

    def _execute_deployment_pipeline(self, model: Any, is_osi: bool = False, sync_mode: str = "copy") -> bool:
        try:
            self.last_deployment_error = None
            deploy_started_at = time.perf_counter()
            model_name = getattr(model, "unique_name", None) or getattr(model, "label", None) or "<unnamed_model>"
            path_type = "OSI" if is_osi else "SML"
            effective_sync_mode = str(sync_mode or "copy").lower()
            if effective_sync_mode not in {"copy", "upsert"}:
                effective_sync_mode = "copy"

            logger.info("[%s] start model=%s", path_type, model_name)

            date_info = self._find_date_table(model)
            if date_info:
                os.environ["SEMABRIDGE_DATE_ALIAS"] = date_info[0]

            try:
                from semabridge.converter.runtime_discovery import RuntimeColumnDiscovery
                detected_mappings = RuntimeColumnDiscovery.discover_column_mappings(
                    model, self.sf_behavior.dynamic.detection_patterns
                )
                if detected_mappings:
                    self.sf_behavior.dynamic.column_mappings.update(detected_mappings)
                    logger.info(f"Dynamically discovered {len(detected_mappings)} column mappings")
            except Exception as e:
                logger.warning(f"Failed to run dynamic column discovery: {e}")

            self.connection_manager.open_session(operation="deploy")
            conn, owns_conn = self.connection_manager.get_connection()
            logger.info("Starting deployment with STRICT sanitization rules")
            self._enriched_view_mapping = {}

            try:
                cur = conn.cursor()
                self._model = model

                if is_osi:
                    self.semantic_view_builder._precompute_duplicate_mappings(model, is_osi=True)
                else:
                    self.semantic_view_builder._precompute_duplicate_mappings(model)

                if self.behavior.legacy.drop_deprecated_views:
                    self._drop_deprecated_views(cur, model)

                if self.sf_behavior.create_missing_tables:
                    self.schema_manager._ensure_source_tables_exist(cur, model)

                if self.sf_behavior.apply_inferred_types:
                    if is_osi:
                        self.schema_manager._apply_inferred_types_ctas_osi(cur, model)
                    else:
                        self.schema_manager._apply_inferred_types_ctas_sml(cur, model)
                    self._invalidate_schema_cache()

                sf_meta = self.schema_manager._fetch_schema_metadata(cur)
                if not sf_meta:
                    datasets = list(getattr(model, "datasets", []) or [])
                    sf_meta = self.schema_manager._fetch_model_table_metadata(cur, datasets)
                self._live_schema_metadata.update(sf_meta or {})

                if is_osi:
                    self.schema_manager._preflight_check_osi(cur, model)

                if getattr(self.sf_behavior, 'auto_execute_precompute', False):
                    logger.info("🔧 Auto-enrichment enabled - executing pre-compute suggestions...")
                    self._auto_execute_precompute_suggestions(model, cur)

                if getattr(self.sf_behavior, 'auto_create_enriched_view', False):
                    date_info_first = self._find_date_table(model)
                    if date_info_first:
                        date_table_name, date_column_phys, _ = date_info_first
                        date_dataset = self._get_dataset_by_name(model, date_info_first[0])
                        actual_date_name = date_table_name
                        if date_dataset and getattr(date_dataset, 'unique_name', None):
                            actual_date_name = date_dataset.unique_name
                        enriched_view = self._create_enriched_view_for_table(cur, date_table_name, is_date_table=True, date_column_physical=date_column_phys, model=model)
                        if enriched_view and getattr(self.sf_behavior, 'use_enriched_view_for_metrics', False):
                            self._enriched_view_mapping[actual_date_name] = enriched_view
                            mapping = getattr(self.sf_behavior, 'source_table_mapping', None)
                            if mapping is not None:
                                mapping[actual_date_name] = enriched_view
                            logger.info("Using date enriched view %s as source for %s before fact enrichment", enriched_view, actual_date_name)
                    fact_table = self._identify_fact_table(model)
                    if fact_table:
                        enriched_view = self._create_enriched_view_for_table(cur, fact_table, is_date_table=False, model=model)
                        if enriched_view and getattr(self.sf_behavior, 'use_enriched_view_for_metrics', False):
                            self._enriched_view_mapping[fact_table] = enriched_view
                            mapping = getattr(self.sf_behavior, 'source_table_mapping', None)
                            if mapping is not None:
                                mapping[fact_table] = enriched_view
                            logger.info(f"✅ Using enriched view {enriched_view} as source for {fact_table}")
                    date_info = self._find_date_table(model)
                    if date_info:
                        date_table_name, date_column_phys, _ = date_info
                        date_dataset = self._get_dataset_by_name(model, date_info[0])
                        actual_date_name = date_table_name
                        if date_dataset and getattr(date_dataset, 'unique_name', None):
                            actual_date_name = date_dataset.unique_name
                        enriched_view = self._create_enriched_view_for_table(cur, date_table_name, is_date_table=True, date_column_physical=date_column_phys, model=model)
                        if enriched_view and getattr(self.sf_behavior, 'use_enriched_view_for_metrics', False):
                            self._enriched_view_mapping[actual_date_name] = enriched_view
                            mapping = getattr(self.sf_behavior, 'source_table_mapping', None)
                            if mapping is not None:
                                mapping[actual_date_name] = enriched_view
                            logger.info(f"✅ Using enriched view {enriched_view} as source for {actual_date_name}")

                sf_meta_new = self.schema_manager._fetch_schema_metadata(cur)
                if sf_meta_new:
                    self._live_schema_metadata.update(sf_meta_new)
                self.semantic_view_builder.live_schema_metadata = self._live_schema_metadata

                from semabridge.utils.name_translator import get_target_deployment_name
                view_name_raw = getattr(model, "label", None) or getattr(model, "unique_name", None) or "model"
                safe_view_name = get_target_deployment_name(view_name_raw, "snowflake")
                full_view_name = f'"{self.config.database}"."{self.config.schema_name}"."{safe_view_name}"'

                existing_tables: dict[str, dict[str, Any]] = {}
                preserve_existing = False

                if effective_sync_mode == "upsert":
                    view_exists = self._view_exists(cur, full_view_name)
                    if not view_exists:
                        logger.info("UPSERT bootstrap: semantic view %s does not exist. Creating full Snowflake semantic view.", full_view_name)
                    else:
                        preserve_existing = True
                        existing_tables = self._get_existing_base_tables(cur, model)
                        logger.info("UPSERT preserve: existing base tables found: %s", list(existing_tables.keys()) if existing_tables else "none")
                        missing_tables = sorted(dataset_name for dataset_name, table_info in existing_tables.items() if not bool(table_info.get("exists")))
                        if missing_tables and len(missing_tables) == len(existing_tables):
                            preserve_existing = False
                            logger.info("UPSERT bootstrap: semantic view %s exists, but none of the required base tables were found in %s.%s. Recreating the full Snowflake semantic view/table structure.", full_view_name, self.config.database, self.config.schema_name)
                        elif missing_tables:
                            logger.warning("UPSERT partial bootstrap: semantic view %s exists, but some required base tables are missing: %s. Proceeding to create missing tables while preserving existing ones.", full_view_name, missing_tables)
                            preserve_existing = True
                    if preserve_existing and existing_tables:
                        logger.info("Validating relationships and measures against existing tables")
                        validation_errors, incompatible_tables = self._validate_relationships_measures_on_existing_tables(cur, model, existing_tables, is_osi)
                        if validation_errors:
                            logger.warning("Found %d validation error(s) across %d table(s). Incompatible tables will be recreated: %s", len(validation_errors), len(incompatible_tables), incompatible_tables)
                            for table_name in incompatible_tables:
                                if table_name in existing_tables:
                                    existing_tables[table_name]['exists'] = False
                                    logger.info("Forced re-creation for incompatible table: %s", table_name)
                        logger.info("All validation checks passed for existing tables")
                if getattr(self.sf_behavior, "preserve_existing_tables", False):
                    logger.info("COPY mode selected; preserve_existing_tables is ignored so COPY keeps full-replace behavior")

                self.semantic_view_builder.enriched_view_mapping = dict(self._enriched_view_mapping or {})
                self._dry_run_validate_metrics(cur, model)

                if is_osi:
                    ddls = self.semantic_view_builder.generate_ddls_from_osi(model)
                else:
                    ddls = self.semantic_view_builder.generate_ddls(model)

                if preserve_existing and existing_tables:
                    logger.info("Filtering DDLs to skip existing tables")
                    ddls = self._filter_ddls_for_existing_tables(ddls, existing_tables)

                if not ddls:
                    raise ConnectorError("No Snowflake semantic-view DDL statements were generated. Verify model datasets/mappings and target database/schema settings.")

                logger.info("[%s] generated %s DDL statement(s) for model=%s", path_type, len(ddls), model_name)

                for idx, sql in enumerate(ddls):
                    if sql:
                        sql = _sanitize_snowflake_date_functions(sql)
                        if preserve_existing and "CREATE OR REPLACE SEMANTIC VIEW" in sql.upper():
                            logger.info("Smart Upsert: Intercepting semantic view DDL to update metrics dynamically")
                            try:
                                new_metrics = self._extract_metrics_from_ddl(sql)
                                existing_metrics = self._get_existing_metrics(cur, full_view_name)
                                if new_metrics and existing_metrics:
                                    result = self._sync_metrics_dynamic(cur, full_view_name, new_metrics, existing_metrics)
                                    logger.info(f"📊 Metric sync: +{result['added']} ~{result['updated']} ={result['kept']}")
                                    continue
                                else:
                                    logger.warning(f"Smart Upsert parsing yielded empty results. Falling back to Full Replace for {full_view_name}")
                            except Exception as e:
                                logger.warning(f"Smart Upsert encountered an error: {e}. Falling back to Full Replace for {full_view_name}")
                        self.connection_manager._execute_sql(cur, sql, context=f"DDL[{idx}]")
                logger.info("[%s] success model=%s (%.2fs)", path_type, model_name, time.perf_counter() - deploy_started_at)
                return True

            finally:
                self.connection_manager.close_session()
                if owns_conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        except Exception as exc:
            self.last_deployment_error = str(exc)
            logger.error("[%s] FAILED model=%s: %s", path_type, model_name, exc, exc_info=True)
            return False

    def _expected_enrichment_aliases_for_dataset(self, dataset_name: str) -> set[str]:
        aliases: set[str] = set()
        if not hasattr(self, "semantic_view_builder"):
            return aliases
        try:
            details = self.semantic_view_builder.get_precompute_details()
        except Exception:
            details = []
        for detail in details:
            if str(detail.get("target_dataset", "")).casefold() != str(dataset_name).casefold():
                continue
            precomputed = str(detail.get("precomputed_column", "") or "").upper()
            source_dataset = str(detail.get("source_dataset", "") or "").upper()
            source_column = str(detail.get("source_column", "") or "").upper()
            if precomputed:
                aliases.add(precomputed)
            if source_dataset and source_column:
                aliases.add(f"{source_dataset}_{source_column}")
                aliases.add(f"{source_dataset}.{source_column}")
        return aliases

    def _is_expected_enrichment_validation_failure(self, exc: Exception, dataset_name: str, sql_expr: str) -> bool:
        expected_aliases = self._expected_enrichment_aliases_for_dataset(dataset_name)
        if not expected_aliases:
            return False
        err = str(exc or "").upper()
        expr = str(sql_expr or "").upper()
        normalized_err = re.sub(r'[^A-Z0-9]+', '_', err).strip("_")
        normalized_expr = re.sub(r'[^A-Z0-9]+', '_', expr).strip("_")
        for alias in expected_aliases:
            normalized_alias = re.sub(r'[^A-Z0-9]+', '_', alias.upper()).strip("_")
            if not normalized_alias:
                continue
            if normalized_alias in normalized_err or normalized_alias in normalized_expr:
                return True
        return False

    def _dry_run_validate_metrics(self, cursor: Any, model: Any) -> None:
        """
        Runs EXPLAIN SELECT {sql} AS DUMMY on each metric.
        If validation fails, falls back to CAST(NULL AS DOUBLE).
        """
        schema_ref = f'"{self.config.database}"."{self.config.schema_name}"'
        metrics = getattr(model, "metrics", []) or []
        for metric in metrics:
            sql_expr = getattr(metric, "sql_expression", None)
            if not sql_expr:
                continue
            
            # Find the view/table to test against
            dataset_name = getattr(metric, "dataset", "")
            if not dataset_name:
                continue
            
            source_table = dataset_name
            ds = self._get_dataset_by_name(model, dataset_name)
            if ds and getattr(ds, "source_table", None):
                source_table = ds.source_table
            
            safe_table = self._id.sanitize_table_name(source_table)
            source_mapping = self._get_source_table_mapping()
            target_view = (
                source_mapping.get(dataset_name)
                or source_mapping.get(str(dataset_name).upper())
                or source_mapping.get(source_table)
                or source_mapping.get(str(source_table).upper())
                or safe_table
            )
            
            test_sql = f'EXPLAIN SELECT {sql_expr} AS DUMMY FROM {schema_ref}."{target_view}" AS "{dataset_name}" LIMIT 1'
            try:
                cursor.execute(test_sql)
            except Exception as e:
                if self._is_expected_enrichment_validation_failure(e, dataset_name, sql_expr):
                    logger.warning(
                        "Dry-run validation for metric '%s' hit expected enrichment column before final TABLES projection; keeping SQL. Error: %s",
                        metric.unique_name,
                        e,
                    )
                    continue
                logger.warning(f"Dry-run validation failed for metric '{metric.unique_name}'. Fallback to NULL. Error: {e}")
                metric.sql_expression = "CAST(NULL AS DOUBLE)"


    def authenticate(self) -> None:
        self.connection_manager.authenticate()

    def discover(self) -> Dict[str, Any]:
        return self.connection_manager.discover()

    def validate_target(self) -> bool:
        return self.connection_manager.validate_target()

    def emit(self, sml: Any) -> Dict[str, Any]:
        success = self.deploy(sml)
        return {"success": success}

    @property
    def max_concurrency(self) -> int:
        return 3

    def generate_ddls(self, sml: SMLModel) -> list[str]:
        date_info = self._find_date_table(sml)
        if date_info:
            os.environ["SEMABRIDGE_DATE_ALIAS"] = date_info[0]
        return self.semantic_view_builder.generate_ddls(sml)

    def generate_ddls_from_osi(self, osi: OSIModel) -> list[str]:
        date_info = self._find_date_table(osi)
        if date_info:
            os.environ["SEMABRIDGE_DATE_ALIAS"] = date_info[0]
        return self.semantic_view_builder.generate_ddls_from_osi(osi)

    def sync_all_measures(self, sml: SMLModel, fabric_extractor, dataset_id: str, grain_dimensions=None) -> dict:
        return self.measure_synchronizer.sync_all_measures(sml, fabric_extractor, dataset_id, grain_dimensions)

    def generate_cortex_yaml(self, sml: SMLModel) -> str:
        return _renderers.generate_cortex_yaml(self, sml)

    def generate_cortex_yaml_from_osi(self, osi: OSIModel) -> str:
        return _renderers.generate_cortex_yaml_from_osi(self, osi)

    def _get_source_table_mapping(self) -> Dict[str, str]:
        mapping = getattr(self.sf_behavior, 'source_table_mapping', None) or {}
        merged = dict(mapping or {})
        merged.update(self._enriched_view_mapping or {})
        return merged

    def deploy_cortex_yaml(self, cursor: Any, sml: SMLModel, yaml_content: str) -> None:
        from semabridge.utils.name_translator import get_target_deployment_name
        model_name_raw = getattr(sml, "unique_name", None) or getattr(sml, "label", None) or "model"
        model_name = get_target_deployment_name(model_name_raw, "snowflake")
        stage_fqn = f"{self.config.database}.{self.config.schema_name}.SEMABRIDGE_CORTEX"
        stage_path = f"@{stage_fqn}/{model_name}.yaml"
        self.connection_manager._execute_sql(cursor, f"CREATE STAGE IF NOT EXISTS {stage_fqn} COMMENT = 'SemaBridge Cortex Analyst YAML store'", context="CREATE STAGE")
        escaped = yaml_content.replace("\\", "\\\\").replace("'", "\\'")
        put_sql = f"PUT TEXT '{escaped}' {stage_path} OVERWRITE = TRUE AUTO_COMPRESS = FALSE"
        try:
            self.connection_manager._execute_sql(cursor, put_sql, context="PUT YAML")
        except Exception as exc:
            logger.warning("PUT to Cortex stage failed (%s); YAML deploy skipped. Stage path: %s", exc, stage_path)
            return
        logger.info("Cortex Analyst YAML uploaded to %s", stage_path)

    def _sanitize_col_name(self, name: str) -> str:
        return self._id.sanitize_column(name)

    def _sanitize_semantic_name(self, name: str) -> str:
        sanitized = self._id.sanitize_column(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _sanitize_alias(self, name: str) -> str:
        sanitized = self._id.sanitize_alias(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _get_safe_object_name(self, name: str) -> str:
        return self._id.sanitize_column(name)

    def _safe_table_name(self, name: str) -> str:
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
        if not view_name:
            logger.warning("No semantic view name provided for extraction")
            return None
        conn, owns_conn = self.connection_manager.get_connection()
        try:
            cur = conn.cursor()
            safe_view_name = str(view_name).replace('"', "").strip()
            full_view_name = f'"{self.config.database}"."{self.config.schema_name}"."{safe_view_name}"'
            self.connection_manager._execute_sql(cur, f"SELECT GET_DDL('SEMANTIC_VIEW', '{full_view_name}')", context="GET_SEMANTIC_VIEW_DDL")
            row = cur.fetchone()
            if not row or not row[0]:
                logger.info("No DDL returned for semantic view %s", full_view_name)
                return None
            ddl = str(row[0])
            logger.info("Retrieved semantic view DDL for %s (%d chars)", full_view_name, len(ddl))
            return ddl
        except Exception as exc:
            logger.warning("Failed to retrieve semantic view %s: %s", view_name, exc)
            return None
        finally:
            if owns_conn:
                conn.close()

    def _view_exists(self, cursor: Any, full_view_name: str) -> bool:
        try:
            parts = full_view_name.replace('"', '').split('.')
            if len(parts) != 3:
                logger.warning("Invalid view name format: %s", full_view_name)
                return False
            db, schema, view = parts
            safe_view = view.replace("'", "''")
            try:
                self.connection_manager._execute_sql(cursor, f"SHOW SEMANTIC VIEWS LIKE '{safe_view}' IN SCHEMA \"{db}\".\"{schema}\"", context="CHECK_SEMANTIC_VIEW_EXISTS")
                exists = bool(cursor.fetchall())
            except Exception as show_exc:
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

    def _get_existing_base_tables(self, cursor: Any, model: Any) -> dict:
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
                    logger.info("Table for dataset '%s' exists: %s (%d columns)", dataset_name, safe_table_name, len(rows))
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

    def _validate_relationships_measures_on_existing_tables(self, cursor: Any, model: Any, existing_tables: dict, is_osi: bool) -> list:
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

            relationships = getattr(model, 'relationships', []) or []
            for rel in relationships:
                if not getattr(rel, 'is_active', True):
                    continue
                from_dataset = getattr(rel, 'from_dataset', None)
                to_dataset = getattr(rel, 'to_dataset', None)
                from_columns = [str(col).upper() for col in (getattr(rel, 'from_columns', []) or [])]
                to_columns = [str(col).upper() for col in (getattr(rel, 'to_columns', []) or [])]
                if from_dataset and from_dataset in existing_tables and existing_tables[from_dataset]['exists']:
                    available_columns = resolve_table_columns(from_dataset)
                    available_column_types = resolve_table_column_types(from_dataset)
                    for col in from_columns:
                        expected_type = normalize_type(expected_column_type(from_dataset, col))
                        actual_type = normalize_type(available_column_types.get(col))
                        if expected_type and actual_type and expected_type != actual_type:
                            errors.append(f"Relationship {rel.unique_name}: column type mismatch for {dataset_label(from_dataset)}.{col} (expected {expected_type}, found {actual_type})")
                            incompatible_datasets.add(from_dataset)
                    missing_from = [col for col in from_columns if col not in available_columns]
                    if missing_from:
                        errors.append(f"Relationship {rel.unique_name}: missing from_columns {missing_from} in {dataset_label(from_dataset)}")
                        incompatible_datasets.add(from_dataset)
                if to_dataset and to_dataset in existing_tables and existing_tables[to_dataset]['exists']:
                    available_columns = resolve_table_columns(to_dataset)
                    available_column_types = resolve_table_column_types(to_dataset)
                    for col in to_columns:
                        expected_type = normalize_type(expected_column_type(to_dataset, col))
                        actual_type = normalize_type(available_column_types.get(col))
                        if expected_type and actual_type and expected_type != actual_type:
                            errors.append(f"Relationship {rel.unique_name}: column type mismatch for {dataset_label(to_dataset)}.{col} (expected {expected_type}, found {actual_type})")
                            incompatible_datasets.add(to_dataset)
                    missing_to = [col for col in to_columns if col not in available_columns]
                    if missing_to:
                        errors.append(f"Relationship {rel.unique_name}: missing to_columns {missing_to} in {dataset_label(to_dataset)}")
                        incompatible_datasets.add(to_dataset)
                logger.info("Relationship validation: %s (from=%s, to=%s)", rel.unique_name, from_dataset, to_dataset)

            metrics = getattr(model, 'metrics', []) or []
            for metric in metrics:
                metric_dataset = getattr(metric, 'dataset', None)
                source_column = getattr(metric, 'source_column', None)
                if metric_dataset and metric_dataset in existing_tables and existing_tables[metric_dataset]['exists'] and source_column:
                    available_columns = resolve_table_columns(metric_dataset)
                    available_column_types = resolve_table_column_types(metric_dataset)
                    if str(source_column).upper() not in available_columns:
                        errors.append(f"Metric {metric.unique_name}: source_column '{source_column}' missing in {dataset_label(metric_dataset)}")
                        incompatible_datasets.add(metric_dataset)
                    else:
                        expected_type = normalize_type(expected_column_type(metric_dataset, source_column))
                        actual_type = normalize_type(available_column_types.get(str(source_column).upper()))
                        if expected_type and actual_type and expected_type != actual_type:
                            errors.append(f"Metric {metric.unique_name}: source_column type mismatch for {dataset_label(metric_dataset)}.{source_column} (expected {expected_type}, found {actual_type})")
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
        filtered_ddls = []
        for ddl in ddls:
            if not ddl or not isinstance(ddl, str):
                continue
            ddl_upper = ddl.upper().strip()
            if 'CREATE OR REPLACE SEMANTIC VIEW' in ddl_upper:
                logger.info("Including semantic view DDL")
                filtered_ddls.append(ddl)
                continue
            if ddl_upper.startswith('CREATE TABLE') or 'CREATE OR REPLACE TABLE' in ddl_upper:
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
                            target_name = str(table_info['table_name']).split('.')[-1].strip('"').upper()
                            if ddl_table_name == target_name:
                                logger.info("Skipping CREATE TABLE for existing table: %s", table_info['table_name'])
                                skip = True
                                break
                if not skip:
                    logger.info("Including CREATE TABLE DDL (table doesn't exist yet)")
                    filtered_ddls.append(ddl)
                continue
            logger.info("Including other DDL statement")
            filtered_ddls.append(ddl)
        logger.info("DDL filtering complete: %d original -> %d filtered DDLs", len(ddls), len(filtered_ddls))
        return filtered_ddls

    def _identify_fact_table(self, model):
        try:
            conn, owns_conn = self.connection_manager.get_connection()
            from semabridge.extractor.dynamic_extractor import DynamicSchemaExtractor
            extractor = DynamicSchemaExtractor(conn)
            schema = extractor.extract_full_schema()
            fact_tables = [(table, score) for table, score in schema.get('table_types', {}).items() if score == 'fact']
            if fact_tables:
                logger.info(f"💡 Identified fact table dynamically: {fact_tables[0][0]}")
                return fact_tables[0][0]
        except Exception as e:
            logger.warning(f"Dynamic schema extraction failed for fact table: {e}")
        datasets = getattr(model, "datasets", []) or []
        if not datasets:
            return None
        from_counts = {}
        for rel in getattr(model, "relationships", []) or []:
            from_ds = getattr(rel, "from_dataset", getattr(rel, "from_table", None))
            if from_ds:
                from_counts[from_ds] = from_counts.get(from_ds, 0) + 1
        if from_counts:
            max_from = max(from_counts, key=lambda k: from_counts[k])
            logger.info(f"💡 Identified fact table: {max_from} (most relationships: {from_counts[max_from]})")
            return max_from
        logger.warning(f"⚠️ Using first dataset as fact table: {datasets[0].unique_name}")
        return datasets[0].unique_name

    def _find_source_table_for_precompute(self, target_table: str, column_name: str) -> Optional[str]:
        if not hasattr(self, '_model') or not self._model:
            return None
        relationships = getattr(self._model, 'relationships', [])
        datasets = {ds.unique_name: ds for ds in getattr(self._model, 'datasets', [])}
        graph = self._build_relationship_graph(relationships)
        visited = set()
        from collections import deque
        queue = deque([(target_table, [])])
        while queue:
            current, path = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            current_dataset = datasets.get(current)
            if current_dataset:
                for col in getattr(current_dataset, 'columns', []):
                    if col.unique_name.upper() == column_name.upper():
                        source_table = getattr(current_dataset, 'source_table', current)
                        logger.info(f"✅ Found source table {source_table} for column {column_name} via BFS")
                        return source_table
            for neighbor in graph.get(current, []):
                if neighbor not in visited:
                    queue.append((neighbor, path + [current]))
        inferred = self._infer_source_from_column_pattern(column_name, datasets)
        if inferred:
            logger.info(f"Inferred source table {inferred} for column {column_name} via naming pattern")
            return inferred
        logger.warning(f"Could not find source table for {target_table}.{column_name}")
        return None

    def _build_relationship_graph(self, relationships: list) -> dict:
        graph = {}
        for rel in relationships:
            from_ds = getattr(rel, 'from_dataset', None)
            to_ds = getattr(rel, 'to_dataset', None)
            if from_ds and to_ds:
                graph.setdefault(from_ds, []).append(to_ds)
                graph.setdefault(to_ds, []).append(from_ds)
        return graph

    def _infer_source_from_column_pattern(self, column_name: str, datasets: dict) -> Optional[str]:
        col_upper = column_name.upper()
        for ds_name in datasets.keys():
            ds_upper = ds_name.upper()
            if col_upper.startswith(ds_upper) and len(ds_upper) > 3:
                return ds_name
            if ds_upper in col_upper:
                return ds_name
        return None

    def _get_join_key(self, table1: str, table2: str) -> str:
        if not hasattr(self, '_model') or not self._model:
            return "ID"
        relationships = getattr(self._model, 'relationships', [])
        for rel in relationships:
            from_ds = getattr(rel, 'from_dataset', None)
            to_ds = getattr(rel, 'to_dataset', None)
            if from_ds and to_ds:
                if (from_ds.upper() == table1.upper() and to_ds.upper() == table2.upper()) or (from_ds.upper() == table2.upper() and to_ds.upper() == table1.upper()):
                    from_cols = getattr(rel, 'from_columns', [])
                    to_cols = getattr(rel, 'to_columns', [])
                    if from_cols:
                        return from_cols[0].upper()
                    if to_cols:
                        return to_cols[0].upper()
        common_keys = ['PRODUCTID', 'ID', 'CUSTOMERID', 'BUSINESS_UNIT', 'FISCAL_YR_PERIOD']
        for key in common_keys:
            if key in table1.upper() or key in table2.upper():
                return key
        return "ID"

    def _find_date_table(self, model):
        date_keywords = ['date', 'calendar', 'cal', 'dim_date', 'dates']
        fiscal_keywords = ['fiscal_yr_period', 'fiscal_period', 'fiscal_year_period']
        for dataset in getattr(model, 'datasets', []):
            dataset_name = dataset.unique_name.lower()
            is_date_table = any(kw in dataset_name for kw in date_keywords)
            if is_date_table:
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
        suggestions = self.semantic_view_builder._precompute_suggestions(model)
        if not suggestions:
            return
        logger.info("🚀 Auto-executing pre-compute suggestions...")
        rich_details = self.semantic_view_builder.get_precompute_details()
        if rich_details:
            logger.info("Pre-compute suggestions will be projected through enriched views; skipping physical base-table mutation.")
            return
        live_meta: dict[str, set[str]] = {}
        if hasattr(self, 'semantic_view_builder') and hasattr(self.semantic_view_builder, 'live_schema_metadata'):
            live_meta = {k.upper(): {c.upper() for c in v} for k, v in self.semantic_view_builder.live_schema_metadata.items()}
        if not live_meta:
            sf_meta = self.schema_manager._fetch_schema_metadata(cursor)
            if not sf_meta:
                datasets = list(getattr(model, "datasets", []) or [])
                sf_meta = self.schema_manager._fetch_model_table_metadata(cursor, datasets)
            if sf_meta:
                self._live_schema_metadata.update(sf_meta)
                live_meta = {k.upper(): {c.upper() for c in v} for k, v in sf_meta.items()}
        for table, cols in suggestions.items():
            table_upper = table.upper()
            existing_cols = live_meta.get(table_upper, set())
            cols_needing_add = []
            for col in cols:
                col_upper = col.upper().replace(' ', '_')
                physical = self._resolve_physical_col_name(col, existing_cols)
                if physical in existing_cols or col_upper in existing_cols:
                    logger.debug("Skipping pre-compute for %s.%s: column already exists as %s", table, col, physical or col_upper)
                else:
                    cols_needing_add.append(col)
            if not cols_needing_add:
                logger.info("Skipping all pre-compute suggestions for %s: columns already present", table)
                continue
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
                source_existing = live_meta.get(source_table.upper(), set())
                src_col_phys = self._resolve_physical_col_name(col, source_existing) or col_safe
                from_key = self._get_directional_join_key(table, source_table, from_side=table)
                to_key = self._get_directional_join_key(table, source_table, from_side=source_table)
                try:
                    cursor.execute(f'ALTER TABLE "{table_upper}" ADD COLUMN IF NOT EXISTS "{col_safe}" VARCHAR')
                    logger.info("Added column %s to %s", col_safe, table)
                except Exception as e:
                    logger.debug("Column %s may already exist in %s: %s", col_safe, table, e)
                update_sql = f'UPDATE "{table_upper}" t SET t."{col_safe}" = (SELECT s."{src_col_phys}" FROM "{source_table.upper()}" s WHERE s."{to_key}" = t."{from_key}" LIMIT 1)'
                try:
                    cursor.execute(update_sql)
                    logger.info("Populated %s in %s from %s", col_safe, table, source_table)
                except Exception as e:
                    logger.warning("Could not auto-populate %s: %s", col_safe, e)
        logger.info("✅ Pre-compute suggestions executed successfully")

    def _resolve_physical_col_name(self, dax_col_name: str, existing_cols: set[str]) -> str:
        candidates = [dax_col_name.upper(), f"COL_{dax_col_name.upper()}", dax_col_name.upper().replace(' ', '_')]
        for c in candidates:
            if c in existing_cols:
                return c
        for existing in existing_cols:
            if dax_col_name.upper() in existing:
                return existing
        return dax_col_name.upper().replace(' ', '_')

    def _get_directional_join_key(self, table1: str, table2: str, from_side: str) -> str:
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
        source_table = source_table_mapping.get(getattr(dataset, "unique_name", dataset_name), getattr(dataset, "source_table", None) or dataset_name)
        safe_table = self._id.sanitize_table_name(source_table)
        return f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'

    def _resolve_model_column_name(self, model: Any, dataset_name: str, column_name: str) -> str:
        dataset = self._get_dataset_by_name(model, dataset_name)
        if dataset is not None:
            try:
                return self.schema_manager._resolve_physical_column_name(dataset, column_name)
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
                edges.append({
                    "from_dataset": from_ds,
                    "to_dataset": to_ds,
                    "from_columns": from_cols,
                    "to_columns": to_cols,
                    "current_dataset": from_ds,
                    "next_dataset": to_ds,
                    "current_columns": from_cols,
                    "next_columns": to_cols,
                })
            if str(to_ds).casefold() == str(dataset_name).casefold():
                edges.append({
                    "from_dataset": to_ds,
                    "to_dataset": from_ds,
                    "from_columns": to_cols,
                    "to_columns": from_cols,
                    "current_dataset": to_ds,
                    "next_dataset": from_ds,
                    "current_columns": to_cols,
                    "next_columns": from_cols,
                })
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
                    logger.debug(
                        "Relationship path %s -> %s: %s",
                        start_dataset,
                        target_dataset,
                        [
                            {
                                "from_dataset": e.get("from_dataset"),
                                "to_dataset": e.get("to_dataset"),
                                "from_columns": e.get("from_columns"),
                                "to_columns": e.get("to_columns"),
                            }
                            for e in next_path
                        ],
                    )
                    return next_path
                visited.add(key)
                queue.append((nxt, next_path))
        return []

    def _auto_execute_precompute_suggestions(self, model: Any, cursor: Any) -> None:
        """
        Runs the precompute analysis and logs the cross-table columns needed.
        The actual injection happens dynamically inside _create_enriched_view_for_table.
        """
        try:
            self.semantic_view_builder._precompute_suggestions(model)
        except Exception as e:
            logger.warning(f"Failed to auto-execute precompute suggestions: {e}")

    def _build_precomputed_column_select(self, model: Any, target_dataset: str, source_dataset: str, source_column: str, precomputed_column: str) -> tuple[Optional[str], Optional[str]]:
        path = self._find_relationship_path(model, target_dataset, source_dataset)
        if not path:
            logger.warning("No active relationship path from %s to %s; cannot precompute %s.%s", target_dataset, source_dataset, source_dataset, source_column)
            return None, None

        joins: list[str] = []
        current_alias = "f"
        for idx, edge in enumerate(path):
            next_alias = f"j{idx + 1}"
            from_dataset = edge.get("from_dataset") or edge.get("current_dataset")
            to_dataset = edge.get("to_dataset") or edge.get("next_dataset")
            from_columns = edge.get("from_columns") or edge.get("current_columns") or []
            to_columns = edge.get("to_columns") or edge.get("next_columns") or []
            if not from_dataset or not to_dataset or not from_columns or not to_columns:
                logger.warning("Invalid relationship edge at index %s for %s -> %s: %s", idx, target_dataset, source_dataset, edge)
                return None, None
            from_col = self._resolve_model_column_name(model, from_dataset, from_columns[0])
            to_col = self._resolve_model_column_name(model, to_dataset, to_columns[0])
            joins.append(
                f'LEFT JOIN {self._dataset_source_ref(model, to_dataset)} AS {next_alias} '
                f'ON {current_alias}."{from_col}" = {next_alias}."{to_col}"'
            )
            current_alias = next_alias

        source_alias = f"j{len(path)}"
        source_col = self._resolve_model_column_name(model, source_dataset, source_column)
        select_alias = f'{source_alias}."{source_col}" AS "{precomputed_column}"'
        return "\n".join(joins), select_alias

    def _get_date_table_name(self, model: Any) -> str:
        """Return the active date table/view, preferring a live enriched view."""
        date_dataset = next(
            (ds for ds in getattr(model, "datasets", []) or [] if getattr(ds, "is_date_table", False)),
            None,
        )
        if not date_dataset:
            date_dataset = next(
                (
                    ds for ds in getattr(model, "datasets", []) or []
                    if str(getattr(ds, "unique_name", "")).upper() in {"DATE", "CALENDAR"}
                ),
                None,
            )

        logical_name = str(getattr(date_dataset, "unique_name", "") or "DATE")
        source_name = str(getattr(date_dataset, "source_table", None) or logical_name)
        source_mapping = self._get_source_table_mapping() if hasattr(self, "_get_source_table_mapping") else {}
        mapped_name = (
            source_mapping.get(logical_name)
            or source_mapping.get(logical_name.upper())
            or source_mapping.get(source_name)
            or source_mapping.get(source_name.upper())
        )
        if mapped_name:
            return self._id.sanitize_table_name(mapped_name).upper()

        safe_source = self._id.sanitize_table_name(source_name).upper()
        for candidate in self._get_possible_enriched_view_names(safe_source):
            safe_candidate = self._id.sanitize_table_name(candidate).upper()
            if (
                safe_candidate in self._live_schema_metadata
                or safe_candidate in {str(v).upper() for v in getattr(self, "_enriched_view_mapping", {}).values()}
            ):
                return safe_candidate
        return safe_source

    def _create_enriched_view_for_table(self, cursor, table_name: str, is_date_table: bool = False, date_column_physical: str = None, model: Any = None):
        schema_ref = f'"{self.config.database}"."{self.config.schema_name}"'
        safe_table = self._id.sanitize_table_name(table_name)
        base_query_table = safe_table
        if base_query_table.upper().endswith("_ENRICHED"):
            base_query_table = base_query_table[:-9]
        elif base_query_table.upper().startswith("ENRICHED_"):
            base_query_table = base_query_table[9:]
        possible_names = self._get_possible_enriched_view_names(base_query_table)
        enriched_name = possible_names[0] if possible_names else f"{base_query_table}_ENRICHED"
        try:
            cursor.execute(f'DROP VIEW IF EXISTS {schema_ref}."{enriched_name}"')
        except Exception as e:
            logger.warning(f"Failed to drop stale view {enriched_name}: {e}")
        if is_date_table and model:
            date_dataset = self._get_dataset_by_name(model, table_name)
            all_physical_cols = self._live_schema_metadata.get(base_query_table.upper(), set())
            if not all_physical_cols:
                try:
                    cursor.execute(f"SELECT COLUMN_NAME FROM {self.config.database}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{self.config.schema_name}' AND TABLE_NAME = '{base_query_table.upper()}'")
                    all_physical_cols = {row[0] for row in cursor.fetchall()}
                except Exception as e:
                    logger.warning(f"Failed to get table columns for {base_query_table}: {e}")
                    all_physical_cols = set()
            if not all_physical_cols:
                logger.warning(f"No columns found for {base_query_table}, skipping enriched view creation")
                return safe_table
            physical_to_logical = {}
            if date_dataset:
                for col in getattr(date_dataset, 'columns', []):
                    if getattr(col, 'expression', None):
                        continue
                    source_col = getattr(col, 'source_column', None)
                    unique_name = getattr(col, 'unique_name', '')
                    phys = (source_col or unique_name).strip('"').upper()
                    logical = unique_name
                    physical_to_logical[phys] = logical
            select_items = []
            for phys in sorted(all_physical_cols):
                phys_upper = phys.upper()
                logical = physical_to_logical.get(phys_upper, phys)
                select_items.append(f'"{phys}"')
                if phys_upper != logical.upper():
                    select_items.append(f'"{phys}" AS "{logical}"')
                if date_column_physical and phys_upper == date_column_physical.upper():
                    if logical.upper() != "COL_DATE" and phys_upper != "COL_DATE":
                        select_items.append(f'"{phys}" AS "COL_DATE"')
            month_index_col = getattr(self.behavior.snowflake.dynamic, 'month_index_column', 'MONTHINDEX')
            selected_aliases = {
                m.group(1).upper()
                for item in select_items
                for m in [re.search(r'\s+AS\s+"([^"]+)"\s*$', item, flags=re.IGNORECASE)]
                if m
            }
            selected_aliases.update(
                item.strip().strip('"').upper()
                for item in select_items
                if re.fullmatch(r'"[^"]+"', item.strip())
            )
            if month_index_col.upper() not in selected_aliases:
                all_physical_by_upper = {str(col).upper(): str(col) for col in all_physical_cols}
                month_source = all_physical_by_upper.get(month_index_col.upper())
                date_source = all_physical_by_upper.get(str(date_column_physical or "COL_DATE").strip('"').upper())
                if not date_source:
                    date_source = all_physical_by_upper.get("COL_DATE")
                if not month_source and date_dataset:
                    for col in getattr(date_dataset, 'columns', []):
                        names = [
                            getattr(col, 'source_column', None),
                            getattr(col, 'unique_name', None),
                            getattr(col, 'name', None),
                        ]
                        if any(str(name or '').strip('"').upper() == month_index_col.upper() for name in names):
                            for name in names:
                                candidate = all_physical_by_upper.get(str(name or '').strip('"').upper())
                                if candidate:
                                    month_source = candidate
                                    break
                        if month_source:
                            break
                if month_source:
                    select_items.append(f'"{month_source}" AS "{month_index_col}"')
                    selected_aliases.add(month_index_col.upper())
                elif date_source:
                    select_items.append(f'(YEAR("{date_source}") * 12 + MONTH("{date_source}")) AS "{month_index_col}"')
                    selected_aliases.add(month_index_col.upper())
            else:
                all_physical_by_upper = {str(col).upper(): str(col) for col in all_physical_cols}
                date_source = all_physical_by_upper.get(str(date_column_physical or "COL_DATE").strip('"').upper()) or all_physical_by_upper.get("COL_DATE")

            if date_source:
                if "RUNNING_YEAR" not in selected_aliases:
                    select_items.append(
                        f'(YEAR("{date_source}") - (SELECT MIN(YEAR("{date_source}")) FROM {schema_ref}."{safe_table}") + 1) AS "RUNNING_YEAR"'
                    )
                    selected_aliases.add("RUNNING_YEAR")
                if "ROLLING_PERIOD_SORT" not in selected_aliases:
                    select_items.append(f'(YEAR("{date_source}") * 100 + MONTH("{date_source}")) AS "ROLLING_PERIOD_SORT"')
                    selected_aliases.add("ROLLING_PERIOD_SORT")
        else:
            all_cols = self._live_schema_metadata.get(base_query_table.upper(), set())
            if not all_cols:
                try:
                    cursor.execute(f"SELECT COLUMN_NAME FROM {self.config.database}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{self.config.schema_name}' AND TABLE_NAME = '{base_query_table.upper()}'")
                    all_cols = {row[0] for row in cursor.fetchall()}
                except Exception as e:
                    logger.warning(f"Failed to get table columns for {base_query_table}: {e}")
                    all_cols = set()
            if not all_cols:
                logger.warning(f"No columns found for {base_query_table}, skipping enriched view creation")
                return safe_table
            select_items = [f'"{col}"' for col in all_cols]
        if model:
            # Determine the date column and table reference for anchor injection
            if is_date_table:
                # If we are currently building the date table enriched view, use its physical column and table
                date_col_phys = date_column_physical
                if not date_col_phys:
                    ds = self._get_dataset_by_name(model, table_name)
                    if hasattr(self.schema_manager, '_find_date_column') and ds:
                        date_col_phys = self.schema_manager._find_date_column({ds.unique_name: {c.name for c in ds.columns}})
                if not date_col_phys:
                    date_col_phys = "COL_DATE" # Fallback since we know it mapped to COL_DATE in the select_items
                safe_date_table = safe_table
            else:
                # If we are in a fact table, we still need to cross-join the anchors from the date table
                date_col_phys = None
                date_table_name = self._get_date_table_name(model) if hasattr(self, "_get_date_table_name") else "DATE"
                date_dataset = next((ds for ds in getattr(model, "datasets", []) if getattr(ds, "is_date_table", False)), None)
                if not date_dataset:
                    date_dataset = next((ds for ds in getattr(model, "datasets", []) if ds.unique_name.upper() == "DATE" or ds.unique_name.upper() == "CALENDAR"), None)
                if date_dataset:
                    if hasattr(self.schema_manager, '_find_date_column'):
                        date_col_phys_raw = self.schema_manager._find_date_column({date_dataset.unique_name: {c.name for c in date_dataset.columns}})
                    else:
                        date_col_phys_raw = "Date"
                    if date_col_phys_raw:
                        date_col_phys = self._id.sanitize_column(date_col_phys_raw)
                    live_date_cols = {
                        str(col).strip('"').upper(): str(col).strip('"')
                        for col in (self._live_schema_metadata.get(str(date_table_name).upper(), set()) or set())
                    }
                    mapped_date_cols = {
                        str(col).strip('"').upper(): str(col).strip('"')
                        for col in (self._live_schema_metadata.get(str(self._id.sanitize_table_name(str(date_table_name or "DATE")).upper()).upper(), set()) or set())
                    }
                    available_date_cols = {**live_date_cols, **mapped_date_cols}
                    normalized_date_col = str(date_col_phys or "").strip('"').upper()
                    if normalized_date_col in available_date_cols:
                        date_col_phys = available_date_cols[normalized_date_col]
                    elif normalized_date_col in {"DATE", "CALENDAR_DATE"} and "COL_DATE" in available_date_cols:
                        date_col_phys = available_date_cols["COL_DATE"]
                if not date_col_phys:
                    date_col_phys = "COL_DATE"
                safe_date_table = self._id.sanitize_table_name(str(date_table_name or "DATE")).upper()

            required_anchors = self._get_required_anchor_columns(model)
            if model and not is_date_table and date_col_phys:
                required_by_name = {str(name).upper(): (name, anchor_type) for name, anchor_type in required_anchors}
                max_date_anchor = getattr(self.behavior.snowflake.dynamic, 'max_date_anchor_name', 'MAX_DATE')
                max_monthindex_anchor = getattr(self.behavior.snowflake.dynamic, 'max_monthindex_anchor_name', 'MAX_MONTHINDEX')
                required_by_name.setdefault(max_date_anchor.upper(), (max_date_anchor, 'max_date'))
                required_by_name.setdefault(max_monthindex_anchor.upper(), (max_monthindex_anchor, 'max_monthindex'))
                required_anchors = list(required_by_name.values())
            if date_col_phys and required_anchors:
                month_index_col = getattr(self.behavior.snowflake.dynamic, 'month_index_column', 'MONTHINDEX')
                existing_anchor_aliases = {
                    m.group(1).upper()
                    for item in select_items
                    for m in [re.search(r'\s+AS\s+"([^"]+)"\s*$', item, flags=re.IGNORECASE)]
                    if m
                }
                for anchor_name, anchor_type in required_anchors:
                    if str(anchor_name).upper() in existing_anchor_aliases:
                        continue
                    if anchor_type == 'max_date':
                        select_items.append(f'(SELECT MAX("{date_col_phys}") FROM {schema_ref}."{safe_date_table}") AS "{anchor_name}"')
                    elif anchor_type == 'min_date':
                        select_items.append(f'(SELECT MIN("{date_col_phys}") FROM {schema_ref}."{safe_date_table}") AS "{anchor_name}"')
                    elif anchor_type == 'fiscal_period':
                        select_items.append(f'(SELECT MAX("{month_index_col}") FROM {schema_ref}."{safe_date_table}" WHERE "{date_col_phys}" = CURRENT_DATE()) AS "{anchor_name}"')
                    elif anchor_type == 'max_monthindex':
                        select_items.append(
                            f'(SELECT MAX(YEAR("{date_col_phys}") * 12 + MONTH("{date_col_phys}")) '
                            f'FROM {schema_ref}."{safe_date_table}") AS "{anchor_name}"'
                        )
                    existing_anchor_aliases.add(str(anchor_name).upper())
        join_clauses = []
        from_clause = f'FROM {schema_ref}."{safe_table}"'
        if model and not is_date_table:
            dataset_name = next((ds.unique_name for ds in getattr(model, "datasets", []) if ds.unique_name.upper() == table_name.upper()), table_name)
            precompute_details = []
            if hasattr(self, 'semantic_view_builder'):
                precompute_details = [
                    detail for detail in self.semantic_view_builder.get_precompute_details()
                    if str(detail.get("target_dataset", "")).casefold() == str(dataset_name).casefold()
                ]
            if precompute_details:
                base_alias = "f"
                select_items = [f'{base_alias}.{item}' if item.startswith('"') else item for item in select_items]
                from_clause = f'FROM {schema_ref}."{safe_table}" AS {base_alias}'
                joined_aliases = {str(dataset_name).casefold(): base_alias}
                joined_edges: set[tuple[str, str, str, str]] = set()
                alias_idx = 1

                for detail in precompute_details:
                    source_dataset = detail.get("source_dataset", "")
                    source_column = detail.get("source_column", "")
                    precomputed_column = detail.get("precomputed_column", "")
                    path = self._find_relationship_path(model, dataset_name, source_dataset)
                    if not path:
                        logger.warning("No active relationship path from %s to %s; cannot enrich %s", dataset_name, source_dataset, precomputed_column)
                        continue

                    for edge in path:
                        from_dataset = edge.get("from_dataset") or edge.get("current_dataset")
                        to_dataset = edge.get("to_dataset") or edge.get("next_dataset")
                        from_columns = edge.get("from_columns") or edge.get("current_columns") or []
                        to_columns = edge.get("to_columns") or edge.get("next_columns") or []
                        if not from_dataset or not to_dataset or not from_columns or not to_columns:
                            logger.warning("Invalid join edge for %s: %s", precomputed_column, edge)
                            break
                        current_key = str(from_dataset).casefold()
                        next_key = str(to_dataset).casefold()
                        if current_key not in joined_aliases:
                            logger.warning("Join path for %s lost alias at %s", precomputed_column, from_dataset)
                            break
                        edge_key = (
                            current_key,
                            next_key,
                            str(from_columns[0]).casefold(),
                            str(to_columns[0]).casefold(),
                        )
                        if next_key not in joined_aliases:
                            next_alias = f"j{alias_idx}"
                            alias_idx += 1
                            joined_aliases[next_key] = next_alias
                        if edge_key not in joined_edges:
                            from_alias = joined_aliases[current_key]
                            next_alias = joined_aliases[next_key]
                            from_col = self._resolve_model_column_name(model, from_dataset, from_columns[0])
                            to_col = self._resolve_model_column_name(model, to_dataset, to_columns[0])
                            join_clauses.append(
                                f'LEFT JOIN {self._dataset_source_ref(model, to_dataset)} AS {next_alias} '
                                f'ON {from_alias}."{from_col}" = {next_alias}."{to_col}"'
                            )
                            joined_edges.add(edge_key)

                    source_alias = joined_aliases.get(str(source_dataset).casefold())
                    if not source_alias:
                        continue
                    source_col = self._resolve_model_column_name(model, source_dataset, source_column)
                    select_items.append(f'{source_alias}."{source_col}" AS "{precomputed_column}"')

                if join_clauses:
                    from_clause += "\n" + "\n".join(join_clauses)
        select_clause = "SELECT\n    " + ",\n    ".join(select_items)
        create_sql = f'CREATE OR REPLACE VIEW {schema_ref}."{enriched_name}" AS\n{select_clause}\n{from_clause}'
        logger.debug("Enriched view SQL for %s:\n%s", enriched_name, create_sql)
        try:
            cursor.execute(create_sql)
            logger.info(f"✅ Created enriched view {enriched_name} for table {table_name} (date_table={is_date_table})")
            projected_cols = set()
            for item in select_items:
                alias_match = re.search(r'\s+AS\s+"([^"]+)"\s*$', item, flags=re.IGNORECASE)
                if alias_match:
                    projected_cols.add(alias_match.group(1).upper())
                    continue
                plain_match = re.fullmatch(r'(?:[A-Za-z_][A-Za-z0-9_]*\.)?"([^"]+)"', item.strip())
                if plain_match:
                    projected_cols.add(plain_match.group(1).upper())
            if projected_cols:
                self._live_schema_metadata[enriched_name.upper()] = projected_cols
                if hasattr(self, "semantic_view_builder"):
                    self.semantic_view_builder.live_schema_metadata[enriched_name.upper()] = projected_cols
            return enriched_name
        except Exception as e:
            logger.warning(f"Failed to create enriched view {enriched_name}: {e}")
            return safe_table

    def _get_possible_enriched_view_names(self, base_table: str) -> list:
        patterns = getattr(self.behavior.snowflake.dynamic, 'enriched_view_patterns', ["{table}_ENRICHED", "ENRICHED_{table}", "{table}_VW", "VW_{table}"])
        return [pattern.format(table=base_table) for pattern in patterns]

    def _find_existing_enriched_view(self, cursor, possible_names: list) -> Optional[str]:
        db_name = self.config.database
        schema_name = self.config.schema_name
        for view_name in possible_names:
            try:
                cursor.execute(f"SHOW VIEWS LIKE '{view_name}' IN SCHEMA \"{db_name}\".\"{schema_name}\"")
                if cursor.fetchone():
                    return view_name
            except:
                continue
        return None

    def _table_exists(self, cursor, table_name: str) -> bool:
        db_name = self.config.database
        schema_name = self.config.schema_name
        try:
            cursor.execute(f"SHOW TABLES LIKE '{table_name}' IN SCHEMA \"{db_name}\".\"{schema_name}\"")
            return bool(cursor.fetchone())
        except:
            return False

    def _object_exists(self, object_name: str) -> bool:
        conn = None
        owns = False
        try:
            conn, owns = self.connection_manager.get_connection()
            cur = conn.cursor()
            parts = object_name.split('.')
            name = parts[-1].strip('"').strip("'")
            db_name = parts[-3].strip('"').strip("'") if len(parts) >= 3 else self.config.database
            schema_name = parts[-2].strip('"').strip("'") if len(parts) >= 2 else self.config.schema_name
            cur.execute(f"SHOW OBJECTS LIKE '{name}' IN SCHEMA \"{db_name}\".\"{schema_name}\"")
            return bool(cur.fetchone())
        except Exception:
            return False
        finally:
            if conn and owns:
                try:
                    conn.close()
                except Exception:
                    pass

    def resolve_table_or_view(self, table_name: str) -> str:
        base_name = table_name.upper()
        if not hasattr(self, '_schema_objects_cache') or not self._schema_objects_cache:
            all_objects = []
            try:
                conn, owns = self.connection_manager.get_connection()
                cur = conn.cursor()
                cur.execute(f"SELECT TABLE_NAME FROM {self.config.database}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = '{self.config.schema_name}'")
                all_objects = [row[0].upper() for row in cur.fetchall()]
                self._schema_objects_cache = all_objects
            except Exception as e:
                logger.warning(f"Could not load INFORMATION_SCHEMA for table resolution: {e}")
            finally:
                if 'conn' in locals() and owns:
                    try:
                        conn.close()
                    except Exception:
                        pass
        else:
            all_objects = self._schema_objects_cache
        if not all_objects:
            return f'"{self.config.database}"."{self.config.schema_name}"."{base_name}"'
        patterns = getattr(self.behavior.snowflake.dynamic, 'enriched_view_patterns', ["{table}_ENRICHED", "ENRICHED_{table}", "{table}_VW", "VW_{table}"])
        candidates = []
        for pattern in patterns:
            enriched_name = pattern.format(table=base_name)
            if enriched_name.upper() != base_name:
                candidates.append(enriched_name.upper())
        candidates.append(base_name)
        for candidate in candidates:
            if candidate in all_objects:
                return f'"{self.config.database}"."{self.config.schema_name}"."{candidate}"'
        from difflib import get_close_matches
        matches = get_close_matches(base_name, all_objects, n=1, cutoff=0.6)
        if matches:
            return f'"{self.config.database}"."{self.config.schema_name}"."{matches[0]}"'
        return f'"{self.config.database}"."{self.config.schema_name}"."{base_name}"'

    def _build_dynamic_enriched_view_sql(self, model, fact_table, safe_table, enriched_view_name):
        fact_dataset = self._get_dataset_by_name(model, fact_table)
        fact_source_ref = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'
        schema_ref = f'"{self.config.database}"."{self.config.schema_name}"'
        is_date_table = False
        date_info = self._find_date_table(model)
        if date_info and date_info[0] == fact_table:
            is_date_table = True
        if is_date_table:
            date_col_raw = date_info[1]
            date_col_safe = self._resolve_model_column_name(model, fact_table, date_col_raw)
            select_clause = f'SELECT f.*, f."{date_col_safe}" AS "COL_DATE"'
            view_sql = f'CREATE OR REPLACE VIEW {schema_ref}."{enriched_view_name}" AS {select_clause} FROM {fact_source_ref} f;'
            return view_sql
        select_parts = ["SELECT f.*"]
        required_anchors = self._get_required_anchor_columns(model)
        date_info = self._find_date_table(model)
        date_table, date_col = None, None
        if date_info:
            date_table_raw, date_col_raw, _ = date_info
            date_table = self._id.sanitize_table_name(date_table_raw).upper()
            date_col = self._resolve_model_column_name(model, date_table_raw, date_col_raw).upper()
        month_index_col = getattr(self.behavior.snowflake.dynamic, 'month_index_column', 'MONTHINDEX')
        for anchor_name, anchor_type in required_anchors:
            if anchor_type == 'max_date' and date_table and date_col:
                select_parts.append(f'        , (SELECT MAX("{date_col}") FROM {schema_ref}."{date_table}") AS "{anchor_name}"')
            elif anchor_type == 'min_date' and date_table and date_col:
                select_parts.append(f'        , (SELECT MIN("{date_col}") FROM {schema_ref}."{date_table}") AS "{anchor_name}"')
            elif anchor_type == 'fiscal_period' and date_table and date_col:
                select_parts.append(f'        , (SELECT MAX("{month_index_col}") FROM {schema_ref}."{date_table}" WHERE "{date_col}" = CURRENT_DATE()) AS "{anchor_name}"')
            elif anchor_type == 'max_monthindex' and date_table and date_col:
                select_parts.append(f'        , (SELECT MAX("{month_index_col}") FROM {schema_ref}."{date_table}") AS "{anchor_name}"')
        numeric_cols = self._find_numeric_columns(safe_table)
        max_cols = getattr(self.behavior.snowflake.dynamic, 'max_precomputed_numeric_columns', 5)
        for col in numeric_cols[:max_cols]:
            agg_name = f"TOTAL_{col}_ALL"
            select_parts.append(f'        , (SELECT SUM("{col}") FROM {fact_source_ref}) AS "{agg_name}"')
        precompute_joins: list[str] = []
        if hasattr(self, 'semantic_view_builder'):
            for detail in self.semantic_view_builder.get_precompute_details():
                if str(detail.get("target_dataset", "")).casefold() != str(fact_table).casefold():
                    continue
                join_sql, select_sql = self._build_precomputed_column_select(
                    model,
                    detail["target_dataset"],
                    detail["source_dataset"],
                    detail["source_column"],
                    detail["precomputed_column"],
                )
                if join_sql and select_sql:
                    for join_line in join_sql.splitlines():
                        if join_line and join_line not in precompute_joins:
                            precompute_joins.append(join_line)
                    select_parts.append(f"        , {select_sql}")
        select_clause = "\n".join(select_parts)
        from_clause = f"FROM {fact_source_ref} f"
        if precompute_joins:
            from_clause += "\n" + "\n".join(precompute_joins)
        if "_CURRENT_FISCAL_PERIOD" in select_clause.upper() and "SELECT MAX" not in select_clause.upper():
            cte = self._build_fiscal_period_cte_dynamic(model)
            if cte:
                view_sql = f'CREATE OR REPLACE VIEW {schema_ref}."{enriched_view_name}" AS\n{cte}\n{select_clause}\n{from_clause};'
            else:
                view_sql = f'CREATE OR REPLACE VIEW {schema_ref}."{enriched_view_name}" AS\n{select_clause}\n{from_clause};'
        else:
            view_sql = f'CREATE OR REPLACE VIEW {schema_ref}."{enriched_view_name}" AS\n{select_clause}\n{from_clause};'
        return view_sql

    def _build_fiscal_period_cte_dynamic(self, model: Any) -> Optional[str]:
        if not getattr(self.behavior.snowflake.dynamic, 'use_fiscal_period_cte', False):
            return None
        date_table = getattr(self.behavior.snowflake.dynamic, 'date_table_name', None)
        date_column = getattr(self.behavior.snowflake.dynamic, 'date_column_name', None)
        month_index_col = getattr(self.behavior.snowflake.dynamic, 'month_index_column', 'MONTHINDEX')
        if not date_table or not date_column:
            date_info = self._find_date_table(model)
            if date_info:
                date_table, date_column, _ = date_info
            else:
                return None
        return f"""
        WITH _current_fiscal_period AS (
            SELECT MAX("{month_index_col}") 
            FROM "{date_table}" 
            WHERE "{date_column}" = CURRENT_DATE()
        )
        """

    def _find_numeric_columns(self, table_name: str) -> List[str]:
        conn, owns_conn = self.connection_manager.get_connection()
        extractor = DynamicSchemaExtractor(conn)
        return extractor.infer_numeric_columns(table_name)

    def _get_required_anchor_columns(self, model: Any) -> List[Tuple[str, str]]:
        anchors = []
        max_date_anchor = getattr(self.behavior.snowflake.dynamic, 'max_date_anchor_name', 'MAX_DATE')
        min_date_anchor = getattr(self.behavior.snowflake.dynamic, 'min_date_anchor_name', 'MIN_DATE')
        fiscal_period_anchor = getattr(self.behavior.snowflake.dynamic, 'fiscal_period_anchor_name', '_CURRENT_FISCAL_PERIOD')
        max_monthindex_anchor = getattr(self.behavior.snowflake.dynamic, 'max_monthindex_anchor_name', 'MAX_MONTHINDEX')
        for metric in getattr(model, 'metrics', []):
            sql_expr = getattr(metric, 'sql_expression', '') or ''
            dax_expr = getattr(metric, 'expression', '') or ''
            upper_sql = sql_expr.upper()
            upper_dax = dax_expr.upper()
            if max_date_anchor.upper() in upper_sql or max_date_anchor.upper() in upper_dax or 'TOTALYTD' in upper_dax:
                anchors.append((max_date_anchor, 'max_date'))
            if min_date_anchor.upper() in upper_sql or min_date_anchor.upper() in upper_dax:
                anchors.append((min_date_anchor, 'min_date'))
            if fiscal_period_anchor.upper() in upper_sql or 'FISCAL' in upper_dax:
                anchors.append((fiscal_period_anchor, 'fiscal_period'))
            if 'R12M' in upper_sql or 'ROLLING' in upper_sql or 'RUNNING' in upper_sql:
                anchors.append((max_monthindex_anchor, 'max_monthindex'))
        seen = set()
        unique_anchors = []
        for name, expr_type in anchors:
            if name not in seen:
                seen.add(name)
                unique_anchors.append((name, expr_type))
        if unique_anchors:
            logger.info(f"Auto-detected required enriched view anchors: {[a[0] for a in unique_anchors]}")
        return unique_anchors

    def _enriched_view_has_required_columns(self, cursor, view_name: str, required_columns: list) -> bool:
        if not required_columns:
            return True
        try:
            cursor.execute(f'DESC VIEW "{view_name}"')
            existing_cols = {row[0].upper() for row in cursor.fetchall()}
            missing = [col for col in required_columns if col.upper() not in existing_cols]
            if missing:
                logger.info(f"Enriched view {view_name} missing required columns: {missing}")
                return False
            return True
        except Exception as e:
            logger.warning(f"Could not check enriched view columns: {e}")
            return False

    # =========================================================================
    # BACKWARD COMPATIBILITY PROXY METHODS
    # =========================================================================

    def _validate_metric_column_references(self, metric_sql: str, metric_name: str, dataset_col_lookup: Dict[str, set[str]], dataset_aliases: Dict[str, str], metric_names: Optional[set[str]] = None) -> Tuple[bool, Optional[str]]:
        return self.translator._validate_metric_column_references(metric_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_names)

    def _normalize_metric_column_references(self, metric_sql: str, metric_name: str, dataset_col_lookup: Dict[str, set[str]], dataset_aliases: Dict[str, str], metric_names: Optional[set[str]] = None, preferred_table_alias: Optional[str] = None, metric_to_alias: Optional[Dict[str, str]] = None) -> str:
        return self.translator._normalize_metric_column_references(metric_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_names, preferred_table_alias, metric_to_alias)

    def _build_safe_sum_sql(self, expr_sql: str, identifier_hint: Optional[str] = None) -> str:
        return self.measure_synchronizer._build_safe_sum_sql(expr_sql, identifier_hint)

    def generate_semantic_view_tiered(self, model_name: str, shadow_table: str, triage_results: Dict[str, Any], grain_dimensions: list[str]) -> str:
        return self.measure_synchronizer.generate_semantic_view_tiered(model_name, shadow_table, triage_results, grain_dimensions)

    def _try_basic_dax_metric_fallback_expression(self, *, metric: Any, table_alias: str, dataset_col_lookup: Dict[str, set[str]], model: Optional[Any] = None, dataset_by_name: Optional[Dict[str, Any]] = None) -> Optional[str]:
        return self.translator._try_basic_dax_metric_fallback_expression(metric=metric, table_alias=table_alias, dataset_col_lookup=dataset_col_lookup, model=model, dataset_by_name=dataset_by_name)

    def _build_known_metric_fallback_expression(self, *args, **kwargs) -> None:
        return None

    def _rewrite_window_metric_expression(self, metric_sql: str, preferred_table_alias: Optional[str] = None) -> str:
        return self.translator._rewrite_window_metric_expression(metric_sql, preferred_table_alias)

    def _dedupe_qualified_column_tokens(self, sql: str) -> str:
        return self.translator._dedupe_qualified_column_tokens(sql)

    def _resolve_metric_emission_alias(self, default_alias: str, expr_sql: str, dataset_aliases: Dict[str, str], metric_to_alias: Optional[Dict[str, str]] = None) -> str:
        from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
        builder = MetricsClauseBuilder(identifier_sanitizer=self._id, schema_manager=self.schema_manager, sanitizer=None, translator=self.translator, config=self.config, dup_name_repo=self._dup_name_repo)
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
