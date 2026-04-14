"""
Snowflake Emitter.

Generates and executes Snowflake Semantic View DDL and Cortex Analyst YAML
from SML models.
"""

from __future__ import annotations

import time
import yaml
import re
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import snowflake.connector.cursor
    from semabridge.converter.measure_triage import TriageResult


# ───────────────────────────────────────────────────────────────────────────
# Custom Exceptions & Warnings
# ───────────────────────────────────────────────────────────────────────────

class MissingSourceTableWarning(UserWarning):
    """
    Warning raised when a source table referenced in an SML model
    cannot be found in the Snowflake schema.
    
    This is typically non-fatal and allows the deployment to continue
    with a warning status.
    """
    pass


# Custom YAML Dumper for better block style handling
class IndentDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super(IndentDumper, self).increase_indent(flow, False)

def str_presenter(dumper, data):
    if len(data.splitlines()) > 1 or len(data) > 80:  # Use block style for long strings
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

IndentDumper.add_representer(str, str_presenter)


from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior, SnowflakeBehavior
from semabridge.formats.sml.models import SMLModel, SMLDataset, SMLMetric, SMLDimension, SMLRelationship, AggregationType, DataType
from semabridge.utils.identifiers import IdentifierSanitizer, SQL_FUNCTION_NAMES
if TYPE_CHECKING:
    from semabridge.intermediate.models import (
        OSIModel,
        OSIDataset,
        OSIMetric,
        OSIDimension,
        OSIAttribute,
        OSIColumn,
        OSIDataType,
    )
else:
    try:
        from semabridge.intermediate.models import (
            OSIModel,
            OSIDataset,
            OSIMetric,
            OSIDimension,
            OSIAttribute,
            OSIColumn,
            OSIDataType,
        )
    except ImportError:
        OSIModel: TypeAlias = Any
        OSIDataset: TypeAlias = Any
        OSIMetric: TypeAlias = Any
        OSIDimension: TypeAlias = Any
        OSIAttribute: TypeAlias = Any
        OSIColumn: TypeAlias = Any
        OSIDataType = None
from semabridge.utils.logger import get_logger
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor

from semabridge.core.interfaces import BaseEmitter
from semabridge.core.exceptions import ConnectorError
from semabridge.utils.logger import get_logger
from semabridge.repository.duplicate_name_mapping_repository import (
    DuplicateNameMappingRepository,
)

logger = get_logger(__name__)


class SnowflakeEmitter(BaseEmitter):
    """
    Emits SML models to Snowflake artifacts.
    """
    
    def __init__(self, config: SnowflakeConfig, behavior: Optional[ConnectorBehavior] = None):
        self.config = config
        self.behavior = behavior or ConnectorBehavior()
        self.sf_behavior = self.behavior.snowflake
        self._connection = None
        # Session-level connection reuse (P2a): when a session is open,
        # deploy() reuses the same connection instead of reconnecting.
        self._session_conn = None
        # Table-existence cache (P2b): avoids repeated SHOW TABLES queries
        # across models that share source tables.
        self._verified_tables: set[str] = set()
        # Unified identifier sanitizer (Mandate 1: Strict Identifier Hygiene)
        self._id = IdentifierSanitizer(
            force_uppercase=self.behavior.compatibility.force_uppercase,
            always_quote=self.sf_behavior.quote_identifiers,
            suppress_reserved=self.behavior.compatibility.suppress_reserved_words,
            additional_reserved=set(getattr(self.behavior.compatibility, 'additional_reserved_words', []) or []),
        )
        try:
            self._dup_name_repo = DuplicateNameMappingRepository()
        except Exception as exc:
            self._dup_name_repo = None
            logger.warning(
                "Duplicate-name mapping repository unavailable; falling back to in-memory numbering: %s",
                exc,
            )

    def _duplicate_namespace_key(self, model_name: Optional[str] = None) -> str:
        parts = [
            self._sanitize_alias(self.config.database or "DB"),
            self._sanitize_alias(self.config.schema_name or "SCHEMA"),
        ]
        if model_name:
            parts.append(self._sanitize_alias(model_name))
        return ".".join(parts)

    @staticmethod
    def _build_duplicate_signature_seed(
        *,
        source_name: str,
        source_expression: Optional[str],
        data_type: Optional[str],
        aggregation: Optional[str] = None,
    ) -> str:
        return "|".join(
            [
                source_name or "",
                source_expression or "",
                data_type or "",
                aggregation or "",
            ]
        )

    def _resolve_persistent_duplicate_name(
        self,
        *,
        scope_type: str,
        namespace_key: str,
        dataset_key: str,
        normalized_base: str,
        source_name: str,
        source_signature: str,
        preferred_name: str,
    ) -> str:
        if not self._dup_name_repo:
            return preferred_name
        try:
            return self._dup_name_repo.get_or_create_assigned_name(
                scope_type=scope_type,
                namespace_key=namespace_key,
                dataset_key=dataset_key,
                normalized_base=normalized_base,
                source_name=source_name,
                source_signature=source_signature,
                preferred_name=preferred_name,
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist duplicate-name mapping for '%s'/'%s'; using '%s': %s",
                dataset_key,
                source_name,
                preferred_name,
                exc,
            )
            return preferred_name

    def _precompute_duplicate_mappings_for_sml(self, sml: SMLModel) -> None:
        """Persist duplicate mappings for all SML datasets/metrics before emit.

        This strengthens duplicate coverage by ensuring mappings are written for
        every detected collision even if later generation branches are skipped.
        """
        for dataset in sml.datasets:
            self._collect_physical_source_columns(dataset)

        metric_base_totals: dict[str, int] = {}
        metric_label_totals: dict[str, int] = {}
        for metric in sml.metrics:
            base_alias = self._sanitize_alias(metric.unique_name)
            metric_base_totals[base_alias] = metric_base_totals.get(base_alias, 0) + 1
            label_alias = self._sanitize_alias(getattr(metric, "label", None) or metric.unique_name)
            metric_label_totals[label_alias] = metric_label_totals.get(label_alias, 0) + 1

        metric_namespace = self._duplicate_namespace_key(sml.unique_name or sml.label)
        metric_base_seen: dict[str, int] = {}
        metric_signature_seen: dict[str, int] = {}
        metric_label_seen: dict[str, int] = {}
        for metric in sml.metrics:
            metric_base_alias = self._sanitize_alias(metric.unique_name)
            if metric_base_totals.get(metric_base_alias, 0) <= 1:
                pass
            else:
                metric_seen_idx = metric_base_seen.get(metric_base_alias, 0) + 1
                metric_base_seen[metric_base_alias] = metric_seen_idx
                preferred_name = f"{metric_base_alias}_{metric_seen_idx}"

                metric_signature_seed = self._build_duplicate_signature_seed(
                    source_name=metric.unique_name,
                    source_expression=metric.sql_expression or metric.expression,
                    data_type=None,
                    aggregation=metric.aggregation.value if metric.aggregation else None,
                )
                sig_idx = metric_signature_seen.get(metric_signature_seed, 0) + 1
                metric_signature_seen[metric_signature_seed] = sig_idx
                metric_signature = f"{metric_signature_seed}::occ{sig_idx}"

                self._resolve_persistent_duplicate_name(
                    scope_type="metric",
                    namespace_key=metric_namespace,
                    dataset_key=self._sanitize_alias(metric.dataset),
                    normalized_base=metric_base_alias,
                    source_name=metric.unique_name,
                    source_signature=metric_signature,
                    preferred_name=preferred_name,
                )

            label_alias = self._sanitize_alias(getattr(metric, "label", None) or metric.unique_name)
            if metric_label_totals.get(label_alias, 0) > 1:
                label_idx = metric_label_seen.get(label_alias, 0) + 1
                metric_label_seen[label_alias] = label_idx
                label_signature_seed = self._build_duplicate_signature_seed(
                    source_name=getattr(metric, "label", None) or metric.unique_name,
                    source_expression=metric.sql_expression or metric.expression,
                    data_type=None,
                    aggregation=metric.aggregation.value if metric.aggregation else None,
                )
                label_signature = f"{label_signature_seed}::occ{label_idx}"
                self._resolve_persistent_duplicate_name(
                    scope_type="metric_label",
                    namespace_key=metric_namespace,
                    dataset_key=self._sanitize_alias(metric.dataset),
                    normalized_base=label_alias,
                    source_name=metric.unique_name,
                    source_signature=label_signature,
                    preferred_name=f"{label_alias}_{label_idx}",
                )

    def _precompute_duplicate_mappings_for_osi(self, osi: OSIModel) -> None:
        """Persist duplicate mappings for all OSI datasets/metrics before emit."""
        for dataset in osi.datasets:
            self._collect_physical_source_columns_osi(dataset)

        metric_base_totals: dict[str, int] = {}
        metric_label_totals: dict[str, int] = {}
        for metric in osi.metrics:
            base_alias = self._sanitize_alias(metric.unique_name)
            metric_base_totals[base_alias] = metric_base_totals.get(base_alias, 0) + 1
            label_alias = self._sanitize_alias(getattr(metric, "label", None) or metric.unique_name)
            metric_label_totals[label_alias] = metric_label_totals.get(label_alias, 0) + 1

        metric_namespace = self._duplicate_namespace_key(osi.unique_name or osi.label)
        metric_base_seen: dict[str, int] = {}
        metric_signature_seen: dict[str, int] = {}
        metric_label_seen: dict[str, int] = {}
        for metric in osi.metrics:
            metric_base_alias = self._sanitize_alias(metric.unique_name)
            if metric_base_totals.get(metric_base_alias, 0) <= 1:
                pass
            else:
                metric_seen_idx = metric_base_seen.get(metric_base_alias, 0) + 1
                metric_base_seen[metric_base_alias] = metric_seen_idx
                preferred_name = f"{metric_base_alias}_{metric_seen_idx}"

                metric_signature_seed = self._build_duplicate_signature_seed(
                    source_name=metric.unique_name,
                    source_expression=metric.sql_expression or metric.expression,
                    data_type=None,
                    aggregation=metric.aggregation.value if metric.aggregation else None,
                )
                sig_idx = metric_signature_seen.get(metric_signature_seed, 0) + 1
                metric_signature_seen[metric_signature_seed] = sig_idx
                metric_signature = f"{metric_signature_seed}::occ{sig_idx}"

                self._resolve_persistent_duplicate_name(
                    scope_type="metric",
                    namespace_key=metric_namespace,
                    dataset_key=self._sanitize_alias(metric.dataset),
                    normalized_base=metric_base_alias,
                    source_name=metric.unique_name,
                    source_signature=metric_signature,
                    preferred_name=preferred_name,
                )

            label_alias = self._sanitize_alias(getattr(metric, "label", None) or metric.unique_name)
            if metric_label_totals.get(label_alias, 0) > 1:
                label_idx = metric_label_seen.get(label_alias, 0) + 1
                metric_label_seen[label_alias] = label_idx
                label_signature_seed = self._build_duplicate_signature_seed(
                    source_name=getattr(metric, "label", None) or metric.unique_name,
                    source_expression=metric.sql_expression or metric.expression,
                    data_type=None,
                    aggregation=metric.aggregation.value if metric.aggregation else None,
                )
                label_signature = f"{label_signature_seed}::occ{label_idx}"
                self._resolve_persistent_duplicate_name(
                    scope_type="metric_label",
                    namespace_key=metric_namespace,
                    dataset_key=self._sanitize_alias(metric.dataset),
                    normalized_base=label_alias,
                    source_name=metric.unique_name,
                    source_signature=label_signature,
                    preferred_name=f"{label_alias}_{label_idx}",
                )

    def _sanitize_sql_markdown(self, sql: str) -> str:
        """
        Remove markdown code blocks and formatting from SQL expressions.
        
        The LLM sometimes returns SQL wrapped in markdown code fences (```sql ... ```).
        This method ensures clean SQL without markdown artifacts that would break
        Snowflake syntax.
        
        Examples:
            Input:  "```sql\nSELECT * FROM table\n```"
            Output: "SELECT * FROM table"
            
            Input:  "```\nSUM(amount)\n```"
            Output: "SUM(amount)"
        """
        if not sql or not isinstance(sql, str):
            return sql
        
        import re
        
        # Remove opening markdown code fence (```sql, ```, ``` python, etc.)
        sql = re.sub(r'^\s*```(?:sql|python|javascript|js|\w*)?\s*\n?', '', sql, flags=re.MULTILINE | re.IGNORECASE)
        
        # Remove closing markdown code fence
        sql = re.sub(r'\n?\s*```\s*$', '', sql, flags=re.MULTILINE | re.IGNORECASE)
        
        # Strip leading/trailing whitespace
        sql = sql.strip()
        
        return sql

    def _try_basic_dax_metric_fallback_expression(
        self,
        metric: SMLMetric,
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
           model: Optional[SMLModel] = None,
           dataset_by_name: Optional[Dict[str, SMLDataset]] = None,
    ) -> Optional[str]:
        """Translate a small set of common DAX expressions without LLM.

        This is primarily used for Fabric -> Snowflake sync when metrics come
        from Fabric as DAX and no ``sql_expression`` is available.
       
           Args:
               metric: The metric to translate
               table_alias: The table alias for the metric's dataset
               dataset_col_lookup: Map of dataset name -> available columns
               model: Optional SML model for accessing relationships
               dataset_by_name: Optional map of dataset name -> SMLDataset for relationship lookup
        """
        raw_expr = (metric.expression or "").strip()
        if not raw_expr:
            return None

        # Normalize whitespace/newlines from Fabric list-style expressions.
        expr = " ".join(raw_expr.split())
        known_cols = dataset_col_lookup.get(metric.dataset, set())

        # COUNTROWS('Table') -> COUNT(*)
        if re.match(r"(?i)^COUNTROWS\(\s*'[^']+'\s*\)$", expr):
            return "COUNT(*)"

        # COUNTBLANK('Table'[Column]) -> COUNT_IF(alias."COLUMN" IS NULL)
        m_blank = re.match(
            r"(?i)^COUNTBLANK\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$",
            expr,
        )
        if m_blank:
            col_name = self._sanitize_col_name(m_blank.group(1))
            if known_cols and col_name not in known_cols:
                return None
            return f'COUNT_IF({table_alias}."{col_name}" IS NULL)'

        # SUM/AVERAGE/MIN/MAX/COUNT/DISTINCTCOUNT('Table'[Column])
        m_agg = re.match(
            r"(?i)^(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$",
            expr,
        )
        if m_agg:
            agg = m_agg.group(1).upper()
            col_name = self._sanitize_col_name(m_agg.group(2))
            if known_cols and col_name not in known_cols:
                return None

            if agg == "AVERAGE":
                return f'AVG({table_alias}."{col_name}")'
            if agg == "DISTINCTCOUNT":
                return f'COUNT(DISTINCT {table_alias}."{col_name}")'
            return f'{agg}({table_alias}."{col_name}")'

        # Deterministic DAX translation for safe dependency chains, static filters,
        # and period-to-date measures. Reuse the shared translator now that it
        # handles nested measure expansion correctly.
        if model is not None and getattr(model, "metrics", None):
            # Prefer semantic-metric windows for TOTALYTD([Metric], Date) because
            # Snowflake semantic metrics require window functions to operate over
            # same-entity metrics or metric-level aggregates.
            m_totalytd_metric_ref = re.match(
                r"(?i)^TOTALYTD\(\s*\[([^\]]+)\]\s*,\s*(?:'[^']+'\s*)?\[[^\]]+\]\s*\)$",
                expr,
            )
            if m_totalytd_metric_ref:
                ref_name = m_totalytd_metric_ref.group(1).strip()
                metrics_by_name = {
                    str(getattr(m, "unique_name", "")).strip().casefold(): m
                    for m in list(model.metrics)
                    if getattr(m, "unique_name", None)
                }
                ref_metric = metrics_by_name.get(ref_name.casefold())
                if ref_metric and str(getattr(ref_metric, "dataset", "")).casefold() == str(metric.dataset).casefold():
                    ref_metric_name = self._sanitize_semantic_name(str(getattr(ref_metric, "unique_name", ref_name)))
                    year_col = self._resolve_year_partition_column(known_cols)
                    order_col = self._resolve_ytd_order_column(known_cols)
                    if year_col and order_col:
                        return (
                            f'SUM({table_alias}."{ref_metric_name}") OVER '
                            f'(PARTITION BY {table_alias}."{year_col}" '
                            f'ORDER BY {table_alias}."{order_col}")'
                        )

            try:
                from semabridge.converter.dax_translator import DAXTranslator

                translated = DAXTranslator().translate(
                    raw_expr,
                    table_alias,
                    metric.dataset,
                    metric_name=metric.unique_name,
                    metrics_context=list(model.metrics),
                )
                if translated.is_success and translated.sql:
                    return translated.sql
            except Exception as exc:
                logger.debug(
                    "Deterministic DAX translation fallback failed for metric '%s': %s",
                    metric.unique_name,
                    exc,
                )

        # TOTALYTD(SUM([Value]), [DateTagOrColumn])
        m_totalytd = re.match(
            r"(?i)^TOTALYTD\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)\s*,\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$",
            expr,
        )
        if m_totalytd:
            agg = m_totalytd.group(1).upper()
            value_col = self._sanitize_col_name(m_totalytd.group(2))
            date_token = self._sanitize_col_name(m_totalytd.group(3))

            if known_cols and value_col not in known_cols:
                return None

            primary_date_col = self._resolve_primary_date_column(known_cols)
            # Treat PRIMARY_DATE/PRIMARYDATE as semantic tags, not physical names.
            if date_token in {"PRIMARY_DATE", "PRIMARYDATE"}:
                date_col = primary_date_col
            else:
                date_col = date_token if (not known_cols or date_token in known_cols) else primary_date_col

            if not date_col:
                return None

            agg_map = {
                "SUM": "SUM",
                "AVERAGE": "AVG",
                "COUNT": "COUNT",
                "MIN": "MIN",
                "MAX": "MAX",
            }
            sql_agg = agg_map.get(agg, "SUM")
            year_col = self._resolve_year_partition_column(known_cols)
            
            # For Snowflake semantic models, PARTITION BY and ORDER BY must reference
            # actual dimensions, not scalar functions like YEAR(). Only use the window
            # function if we have an actual YEAR dimension column.
            if year_col:
                date_ref = f'{table_alias}."{date_col}"'
                partition_expr = f'{table_alias}."{year_col}"'
                return (
                    f'{sql_agg}({table_alias}."{value_col}") OVER ('
                    f'PARTITION BY {partition_expr} '
                    f'ORDER BY {date_ref} '
                    'ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)'
                )

            logger.debug(
                "TOTALYTD window pattern for metric '%s' requires YEAR dimension column; falling back to NULL",
                metric.unique_name,
            )
            return None

        # CALCULATE(SUM([Value]), SAMEPERIODLASTYEAR([DateTagOrColumn]))
        m_sply = re.match(
            r"(?i)^CALCULATE\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)\s*,\s*SAMEPERIODLASTYEAR\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)\s*\)$",
            expr,
        )
        if m_sply:
            agg = m_sply.group(1).upper()
            value_col = self._sanitize_col_name(m_sply.group(2))
            date_token = self._sanitize_col_name(m_sply.group(3))

            if known_cols and value_col not in known_cols:
                return None

            primary_date_col = self._resolve_primary_date_column(known_cols)
            if date_token in {"PRIMARY_DATE", "PRIMARYDATE"}:
                date_col = primary_date_col
            else:
                date_col = date_token if (not known_cols or date_token in known_cols) else primary_date_col

            if not date_col:
                return None

            # Optional fast-path signal: only consider precomputed offset keys when
            # explicitly enabled. We still use the universal window fallback unless
            # runtime checksum validation is available.
            enable_offset_fastpath = str(
                os.getenv("SEMABRIDGE_SNOWFLAKE_ENABLE_SPLY_OFFSET_FASTPATH", "false")
            ).strip().lower() in {"1", "true", "yes", "on"}
            offset_key_col = self._resolve_sply_offset_key_column(known_cols)
            if enable_offset_fastpath and offset_key_col:
                logger.info(
                    "SPLY offset key '%s' detected for metric '%s', but deterministic translation "
                    "cannot run checksum validation here; using safe window fallback.",
                    offset_key_col,
                    metric.unique_name,
                )

            agg_map = {
                "SUM": "SUM",
                "AVERAGE": "AVG",
                "COUNT": "COUNT",
                "MIN": "MIN",
                "MAX": "MAX",
            }
            sql_agg = agg_map.get(agg, "SUM")
            
            # For Snowflake semantic models, PARTITION BY and ORDER BY must reference
            # actual dimensions, not scalar functions like MONTH(). Check if we have
            # dedicated MONTH and YEAR dimension columns; if not, we cannot use this pattern.
            month_col = self._resolve_month_dimension_column(known_cols)
            year_col = self._resolve_year_dimension_column(known_cols)
            
            if not month_col or not year_col:
                # Cannot generate valid Snowflake semantic model window without dimension columns
                logger.debug(
                    "SPLY window pattern for metric '%s' requires MONTH and YEAR dimension columns; "
                    "none found in model. Falling back to NULL (will require manual DAX or disabled metric).",
                    metric.unique_name,
                )
                return None
            
            date_ref = f'{table_alias}."{date_col}"'
            month_ref = f'{table_alias}."{month_col}"'
            year_ref = f'{table_alias}."{year_col}"'
            
            return (
                f'LAG({sql_agg}({table_alias}."{value_col}"), 12) OVER ('
                f'PARTITION BY {month_ref} '
                f'ORDER BY {year_ref}, {month_ref}'
                ')'
            )

        self._warn_non_sync_friendly_dax(metric, expr)
        return None

    @staticmethod
    def _warn_non_sync_friendly_dax(metric: SMLMetric, normalized_expr: str) -> None:
        """Emit warn-only guidance for high-risk DAX patterns that miss deterministic translation."""
        expr = (normalized_expr or "").upper()
        if not expr:
            return

        # Time-intelligence patterns that are typically easier to sync when modeled via flags/columns.
        if re.search(r"\b(TOTALYTD|DATESYTD|SAMEPERIODLASTYEAR|DATEADD|PARALLELPERIOD|PREVIOUSYEAR)\b", expr):
            logger.warning(
                "Metric '%s' uses high-risk time-intelligence DAX not deterministically translated. "
                "Prefer parser-safe modeling: Calendar flags (e.g., IS_YTD/IS_CURRENT_MONTH) "
                "or precomputed prior-year columns.",
                metric.unique_name,
            )
            return

        # Measure-on-measure variance expressions are harder for rule parsers than explicit filtered sums.
        bracket_refs = re.findall(r"\[[^\]]+\]", normalized_expr)
        if len(bracket_refs) >= 2 and "-" in normalized_expr:
            logger.warning(
                "Metric '%s' appears to be a measure dependency variance expression. "
                "Prefer explicit parser-safe formula: CALCULATE(SUM(...), filter) - CALCULATE(SUM(...), filter).",
                metric.unique_name,
            )

    @staticmethod
    def _resolve_primary_date_column(known_cols: set[str]) -> Optional[str]:
        """Resolve a canonical primary date column from available physical columns."""
        if not known_cols:
            return None

        preferred = [
            "PRIMARY_DATE",
            "PRIMARYDATE",
            "DATE",
            "CALENDAR_DATE",
            "FULL_DATE",
            "DATE_VALUE",
            "TRANSACTION_DATE",
        ]
        for candidate in preferred:
            if candidate in known_cols:
                return candidate
        return None

    @staticmethod
    def _resolve_year_partition_column(known_cols: set[str]) -> Optional[str]:
        """Resolve year partition column when present for stable YTD windows."""
        if not known_cols:
            return None

        for candidate in ["YEAR", "CALENDAR_YEAR", "FISCAL_YEAR"]:
            if candidate in known_cols:
                return candidate
        return None

    @staticmethod
    def _resolve_month_dimension_column(known_cols: set[str]) -> Optional[str]:
        """Resolve month dimension column for SPLY window partitioning.
        
        In Snowflake semantic models, PARTITION BY must reference actual dimensions,
        not scalar functions. This helper detects a dedicated MONTH dimension if available.
        """
        if not known_cols:
            return None

        for candidate in ["MONTH", "CALENDAR_MONTH", "MONTH_NUM", "MONTH_ID"]:
            if candidate in known_cols:
                return candidate
        return None

    @staticmethod
    def _resolve_year_dimension_column(known_cols: set[str]) -> Optional[str]:
        """Resolve year dimension column for SPLY window ordering.
        
        In Snowflake semantic models, ORDER BY must reference actual dimensions,
        not scalar functions. This helper detects a dedicated YEAR dimension if available.
        """
        if not known_cols:
            return None

        for candidate in ["YEAR", "CALENDAR_YEAR", "FISCAL_YEAR", "YEAR_NUM", "YEAR_ID"]:
            if candidate in known_cols:
                return candidate
        return None

    @staticmethod
    def _resolve_sply_offset_key_column(known_cols: set[str]) -> Optional[str]:
        """Detect common prior-year offset key columns for optional SPLY fast-paths."""
        if not known_cols:
            return None

        for candidate in [
            "SPLY_OFFSET_KEY",
            "PRIOR_YEAR_DATE_KEY",
            "PRIORYEARDATEKEY",
            "PRIOR_YEAR_KEY",
            "PY_DATE_KEY",
        ]:
            if candidate in known_cols:
                return candidate
        return None

    @staticmethod
    def _resolve_ytd_order_column(known_cols: set[str]) -> Optional[str]:
        """Resolve in-entity ordering column for YTD windows.

        Keep this conservative: only return physical dimension-like columns that
        can safely appear in semantic metric ORDER BY clauses.
        """
        if not known_cols:
            return None

        for candidate in ["PERIOD", "MONTH", "MONTH_NUM", "YEARPERIOD", "DATE", "PRIMARY_DATE", "PRIMARYDATE"]:
            if candidate in known_cols:
                return candidate
        return None

    @staticmethod
    def _try_generate_flattened_view_cte(
        relationship: Optional[SMLRelationship],
        fact_dataset: str,
        dimension_dataset: str,
        dataset_col_lookup: Dict[str, set[str]],
    ) -> Optional[Tuple[str, str]]:
        """Generate a CTE that joins FACT to CALENDAR dimension for time-intelligence metrics.
        
        When YEAR/MONTH columns don't exist on the FACT table directly, but a relationship
        exists to a CALENDAR/DATE dimension that has these columns, generate a flattened view
        CTE that pre-joins them. This allows window functions to reference dimensions without
        violating Snowflake semantic model constraints.
        
        Args:
            relationship: The relationship from fact to dimension (or None)
            fact_dataset: The fact table dataset name
            dimension_dataset: The dimension table dataset name
            dataset_col_lookup: Mapping of dataset -> available columns
            
        Returns:
            Tuple of (cte_source_sql, joined_table_alias) if successful, None otherwise
        """
        if not relationship:
            return None
        
        # Verify relationship connects fact to dimension
        if relationship.from_dataset != fact_dataset or relationship.to_dataset != dimension_dataset:
            return None
        
        # Check if dimension has the required YEAR and MONTH columns
        dim_cols = dataset_col_lookup.get(dimension_dataset, set())
        year_col = SnowflakeEmitter._resolve_year_dimension_column(dim_cols)
        month_col = SnowflakeEmitter._resolve_month_dimension_column(dim_cols)
        
        if not year_col or not month_col:
            # Dimension doesn't have required time columns
            return None
        
        # Build the CTE
        fact_alias = f"f"
        dim_alias = f"d"
        
        # Get join columns
        from_col = relationship.from_column
        to_col = relationship.to_column
        
        if not from_col or not to_col:
            return None
        
        # Generate CTE SQL
        fact_cols_str = ", ".join([f'f."{col}"' for col in sorted(dataset_col_lookup.get(fact_dataset, []))])
        dim_cols_str = f', d."{year_col}", d."{month_col}"'
        
        cte_sql = (
            f"WITH flattened_fact AS (\n"
            f"  SELECT {fact_cols_str}{dim_cols_str}\n"
            f"  FROM {fact_dataset} {fact_alias}\n"
            f"  INNER JOIN {dimension_dataset} {dim_alias}\n"
            f"    ON {fact_alias}.\"{from_col}\" = {dim_alias}.\"{to_col}\"\n"
            f")"
        )
        
        return (cte_sql, "flattened_fact")

    @staticmethod
    def _is_simple_dax_aggregation_expression(expression: Optional[str]) -> bool:
        """Return True for simple one-column DAX aggregations.

        We use this to keep Client Data from falling back to risky cross-dataset
        remaps when the expression is just ``SUM([Column])``-style shorthand.
        """
        expr = " ".join(str(expression or "").split()).strip()
        if not expr:
            return False

        return bool(
            re.match(
                r"(?i)^(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\(\s*\[[^\]]+\]\s*\)$",
                expr,
            )
        )

    def _should_use_direct_metric_aggregation(self, metric: Any) -> bool:
        """Prefer deterministic aggregation SQL for simple source-column metrics.

        Some auto-generated metrics carry both ``source_column`` and
        ``sql_expression``. The generic sql-expression rewrite path can mangle
        those simple measures when names contain spaces or symbols, so for
        low-complexity direct aggregations we intentionally rebuild the SQL as
        ``AGG(alias."COLUMN")`` from structured metadata.
        """
        if not getattr(metric, "source_column", None) or not getattr(metric, "aggregation", None):
            return False

        complexity_tier = getattr(metric, "complexity_tier", 1) or 1
        if complexity_tier > 1:
            return False

        expr = (getattr(metric, "expression", None) or "").strip().upper()
        if not expr:
            return True

        simple_patterns = (
            "SUM(",
            "COUNT(",
            "DISTINCTCOUNT(",
            "AVERAGE(",
            "MIN(",
            "MAX(",
        )
        return expr.startswith(simple_patterns)

    def _build_schema_validation_map(self, sml: SMLModel) -> Dict[str, set[str]]:
        """
        Build a schema validation map from SML model.
        
        Maps each dataset name to the set of physical column names that exist
        for that dataset. Used to validate metric SQL expressions reference
        only columns that actually exist in Snowflake.
        
        Returns:
            Dict[dataset_name, set[column_names]]
            
        Example:
            {
                'salesfact': {'REVENUE', 'UNITS', 'DATE_ID', ...},
                'date': {'DATE_ID', 'YEAR', 'MONTH', ...},
                'product': {'PRODUCT_ID', 'NAME', 'CATEGORY', ...}
            }
        """
        schema_map: Dict[str, set[str]] = {}
        
        for dataset in sml.datasets:
            columns = set()
            
            # Add all non-calculated columns
            for col in dataset.columns:
                # Skip internal columns
                if col.unique_name.startswith("_") or col.unique_name.startswith("RowNumber"):
                    continue
                
                # Skip calculated columns (they don't exist physically)
                source_expr = getattr(col, 'source_expression', None)
                if source_expr and not self._is_physical_source_column(source_expr):
                    logger.debug(
                        f"Excluding calculated column '{col.unique_name}' from schema validation"
                    )
                    continue
                
                # Sanitize to match Snowflake physical column names
                sanitized = self._sanitize_col_name(col.unique_name)
                columns.add(sanitized)
            
            schema_map[dataset.unique_name] = columns
            logger.debug(f"Schema validation map for '{dataset.unique_name}': {columns}")
        
        return schema_map

    def _try_llm_metric_fallback_expression(
        self,
        *,
        metric: SMLMetric,
        metric_name: str,
        table_alias: str,
        alias_by_raw: Dict[str, str],
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_name_set: set[str],
        all_physical_col_names: set[str],
        emittable_metric_name_set: set[str],
        skipped_metric_names: set[str],
    ) -> Optional[str]:
        """Attempt LLM translation for a metric when deterministic handling fails.

        This is a last-resort, generic recovery path and does not use
        model/domain-specific hardcoded logic.
        """
        dax_expression = (getattr(metric, "expression", None) or "").strip()
        if not dax_expression:
            return None

        candidate_expressions: list[str] = []

        # First try local deterministic translation for simple DAX to avoid
        # unnecessary LLM dependence and confidence gating.
        try:
            from semabridge.converter.dax_rule_translator import (
                is_simple_metric,
                rule_based_translation,
            )
            if is_simple_metric(dax_expression):
                local_expr = rule_based_translation(
                    dax_expression,
                    table_alias.lower(),
                )
                if local_expr:
                    candidate_expressions.append(local_expr)
        except Exception as ex:
            logger.debug(
                f"Local fallback unavailable for metric '{metric.unique_name}': {ex}"
            )

        try:
            from semabridge.converter.gemini_dax_translator import get_gemini_translator
            translator = get_gemini_translator()
        except Exception as ex:
            logger.debug(f"LLM fallback unavailable for metric '{metric.unique_name}': {ex}")
            translator = None

        if translator and getattr(translator, "use_gemini", False) and getattr(translator, "api_key", None):
            schema_context = {
                ds_name: sorted(list(cols))
                for ds_name, cols in dataset_col_lookup.items()
            }

            llm_result = translator.translate(
                dax=dax_expression,
                table_alias=table_alias.lower(),
                dataset_name=metric.dataset,
                metric_name=metric.unique_name,
                schema_context=schema_context,
            )

            if llm_result and llm_result.is_valid and llm_result.sql:
                candidate_expressions.append(llm_result.sql)
            else:
                logger.debug(
                    f"LLM fallback failed for metric '{metric.unique_name}': "
                    f"{getattr(llm_result, 'error', 'invalid translation')}"
                )

        for candidate_sql in candidate_expressions:
            expr = self._sanitize_sql_markdown(candidate_sql)
            if not expr or "SELECT" in expr.upper():
                continue

            expr = self._id.resolve_dot_notation(
                expr,
                alias_by_raw,
                sanitize_col_fn=self._sanitize_col_name,
            )
            expr = self._normalize_metric_column_references(
                expr,
                metric.unique_name,
                dataset_col_lookup,
                dataset_aliases,
                metric_names=metric_name_set,
                preferred_table_alias=table_alias,
            )

            is_valid, _ = self._validate_metric_column_references(
                expr,
                metric.unique_name,
                dataset_col_lookup,
                dataset_aliases,
                metric_names=metric_name_set,
            )
            if not is_valid:
                continue

            if not expr.strip():
                continue
            expr_upper = expr.upper().strip()
            if expr_upper == 'SUM(*)' or expr_upper.endswith('SUM(*)'):
                continue

            unresolved_metric_refs = [
                r for r in re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr)
                if r in metric_name_set
                and r not in all_physical_col_names
                and (
                    r not in emittable_metric_name_set
                    or r in skipped_metric_names
                )
                and r != metric_name
            ]
            if unresolved_metric_refs:
                continue

            logger.info(f"Recovered metric '{metric.unique_name}' via fallback translation")
            return expr

        return None

    def _validate_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that metric SQL references only columns that exist in the schema.
        
        This is a CRITICAL SAFETY CHECK to prevent "invalid identifier" errors
        when the metric SQL is deployed to Snowflake. It verifies that every 
        TABLE.COLUMN reference in the SQL actually corresponds to a column that
        exists in the Snowflake physical schema.
        
        Args:
            metric_sql: The metric SQL expression to validate
            metric_name: Name of the metric (for logging)
            dataset_col_lookup: Dict[dataset_name, set[column_names]] 
                                Map of datasets to their physical columns
            dataset_aliases: Dict[dataset_name, alias] mapping for cross-table refs
            
        Returns:
            Tuple[is_valid, error_message]
            - is_valid: True if all column references are valid
            - error_message: Description of any validation failures (or None if valid)
        """
        import re
        
        # Create reverse alias map: alias -> dataset_name
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        
        # Find all TABLE.COLUMN patterns
        # Matches: table."COLUMN", table.COLUMN, etc.
        patterns = [
            r'(\w+)\."([^"]+)"',                 # table."ColumnName"
            r'(\w+)\.([A-Za-z_][A-Za-z0-9_]*)',    # table.ColumnName
        ]
        
        all_refs = []
        for pattern in patterns:
            matches = re.findall(pattern, metric_sql)
            all_refs.extend(matches)
        
        if not all_refs:
            # No TABLE.COLUMN refs found — simple aggregation, should be OK
            logger.debug(f"No cross-table references found in metric '{metric_name}'")
            return True, None
        
        # Validate each TABLE.COLUMN reference
        for table_alias, col_name in all_refs:
            # Map alias back to dataset
            dataset_name = alias_to_dataset.get(table_alias)
            
            if not dataset_name:
                error = f"Alias '{table_alias}' not found in dataset mapping"
                logger.debug(f"Metric '{metric_name}': {error}")
                return False, error
            
            # Check if column exists in this dataset
            # CRITICAL: Sanitize col_name to match how known_columns are stored
            # (which are already sanitized, e.g. "PRODUCT" not "Product")
            known_columns = dataset_col_lookup.get(dataset_name, set())
            sanitized_col_name = self._sanitize_col_name(col_name)
            resolved_metric_ref = self._resolve_metric_reference_name(
                metric_names,
                sanitized_col_name,
                allow_fuzzy=False,
            )
            if resolved_metric_ref:
                # Allow references to previously defined semantic metrics.
                continue

            # If strict metric resolution failed, allow fuzzy semantic-metric
            # matching as a validation fallback. This avoids dropping valid
            # derived expressions that reference metric names with minor drift.
            fuzzy_metric_ref = self._resolve_metric_reference_name(
                metric_names,
                sanitized_col_name,
                allow_fuzzy=True,
            )
            if fuzzy_metric_ref:
                continue
            resolved_col = self._resolve_column_name_for_dataset(
                known_columns,
                sanitized_col_name,
            )
            if not resolved_col:
                error = (
                    f"Column '{col_name}' (sanitized: '{sanitized_col_name}') not found in dataset '{dataset_name}'. "
                    f"Available columns: {sorted(known_columns)}"
                )
                logger.debug(f"Metric '{metric_name}': {error}")
                return False, error
        
        # All references validated successfully
        logger.debug(f"Metric '{metric_name}': All column references valid")
        return True, None

    def _normalize_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
        preferred_table_alias: Optional[str] = None,
    ) -> str:
        """
        Normalize column references in metric SQL to use unquoted uppercase identifiers.
        
        Converts patterns like:
        - TABLE."ColumnName" → TABLE.COLUMN_NAME
        - TABLE."Product" → TABLE.PRODUCT
        
        This ensures Snowflake can find the physical columns without quote confusion.
        
        Args:
            metric_sql: The metric SQL expression to normalize
            metric_name: Name of the metric (for logging)
            dataset_col_lookup: Dict[dataset_name, set[column_names]]
            dataset_aliases: Dict[dataset_name, alias] mapping
            
        Returns:
            Normalized SQL expression with unquoted uppercase column references
        """
        import re
        
        # Create reverse alias map: alias -> dataset_name
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        
        normalized_sql = metric_sql

        def _format_metric_ref(table_alias: str, col_name: str) -> str:
            """Keep dollar-sign columns quoted so Snowflake parses them reliably."""
            if "$" in col_name:
                return f'{table_alias}."{col_name}"'
            return f"{table_alias}.{col_name}"

        # Pattern 1: Match quoted column references: alias."ColumnName" or alias.'ColumnName'
        # This handles LLM-generated SQL with mixed case like PRODUCT."Product"
        quoted_pattern = r'(\w+)\.(["\'])([^"\']+)\2'
        
        for match in re.finditer(quoted_pattern, normalized_sql):
            table_alias = match.group(1)
            col_name = match.group(3)  # content inside quotes
            
            dataset_name = alias_to_dataset.get(table_alias)
            if not dataset_name:
                continue
            
            # Sanitize to uppercase: Product → PRODUCT
            sanitized_col_name = self._sanitize_col_name(col_name)

            # If this token is actually another metric reference, remove table
            # qualification so it can be resolved as a semantic metric.
            resolved_metric_ref = self._resolve_metric_reference_name(
                metric_names,
                sanitized_col_name,
                allow_fuzzy=False,
            )
            if resolved_metric_ref:
                old_ref = match.group(0)
                new_ref = resolved_metric_ref
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue

            # If the column does not exist on this alias table, try remapping
            # to the unique dataset that owns this column.
            known_columns = dataset_col_lookup.get(dataset_name, set())
            resolved_col = self._resolve_column_name_for_dataset(
                known_columns,
                sanitized_col_name,
            )
            if not resolved_col:
                fuzzy_metric_ref = self._resolve_metric_reference_name(
                    metric_names,
                    sanitized_col_name,
                    allow_fuzzy=True,
                )
                if fuzzy_metric_ref:
                    old_ref = match.group(0)
                    normalized_sql = normalized_sql.replace(old_ref, fuzzy_metric_ref)
                    logger.debug(
                        f"Normalized metric '{metric_name}': remapped {old_ref} → {fuzzy_metric_ref}"
                    )
                    continue

                owners = [
                    ds for ds, cols in dataset_col_lookup.items()
                    if self._resolve_column_name_for_dataset(cols, sanitized_col_name)
                ]
                if len(owners) == 1:
                    owner_alias = dataset_aliases.get(owners[0])
                    if owner_alias:
                        owner_col = self._resolve_column_name_for_dataset(
                            dataset_col_lookup.get(owners[0], set()),
                            sanitized_col_name,
                        ) or sanitized_col_name
                        old_ref = match.group(0)
                        new_ref = _format_metric_ref(owner_alias, owner_col)
                        normalized_sql = normalized_sql.replace(old_ref, new_ref)
                        logger.debug(
                            f"Normalized metric '{metric_name}': remapped {old_ref} → {new_ref}"
                        )
                        continue
            elif resolved_col != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, resolved_col)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue
            
            # Replace: alias."ColumnName" → alias.COLUMN_NAME (unquoted)
            old_ref = match.group(0)
            new_ref = _format_metric_ref(table_alias, sanitized_col_name)
            normalized_sql = normalized_sql.replace(old_ref, new_ref)
            
            logger.debug(
                f"Normalized metric '{metric_name}': {old_ref} → {new_ref}"
            )
        
        # Pattern 2: Match unquoted references: alias.ColumnName / alias.COLUMN_NAME
        # Convert/resolve to physical names and aliases as needed.
        # Allow `$` in unquoted identifiers as Snowflake physical columns can
        # legitimately contain it after sanitization (for example `FOO_$`).
        # Without this, a reference like `alias.FOO_$` gets partially matched
        # as `alias.FOO_`, and normalization leaves behind a stray `$`.
        unquoted_pattern = r'(\w+)\.([A-Za-z_][A-Za-z0-9_$]*)'
        
        for match in re.finditer(unquoted_pattern, normalized_sql):
            table_alias = match.group(1)
            col_name = match.group(2)
            
            dataset_name = alias_to_dataset.get(table_alias)
            if not dataset_name:
                continue
            
            # Sanitize to uppercase
            sanitized_col_name = self._sanitize_col_name(col_name)

            resolved_metric_ref = self._resolve_metric_reference_name(
                metric_names,
                sanitized_col_name,
                allow_fuzzy=False,
            )
            if resolved_metric_ref:
                old_ref = match.group(0)
                new_ref = resolved_metric_ref
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue

            known_columns = dataset_col_lookup.get(dataset_name, set())
            resolved_col = self._resolve_column_name_for_dataset(
                known_columns,
                sanitized_col_name,
            )
            if not resolved_col:
                fuzzy_metric_ref = self._resolve_metric_reference_name(
                    metric_names,
                    sanitized_col_name,
                    allow_fuzzy=True,
                )
                if fuzzy_metric_ref:
                    old_ref = match.group(0)
                    normalized_sql = normalized_sql.replace(old_ref, fuzzy_metric_ref)
                    logger.debug(
                        f"Normalized metric '{metric_name}': remapped {old_ref} → {fuzzy_metric_ref}"
                    )
                    continue

                owners = [
                    ds for ds, cols in dataset_col_lookup.items()
                    if self._resolve_column_name_for_dataset(cols, sanitized_col_name)
                ]
                if len(owners) == 1:
                    owner_alias = dataset_aliases.get(owners[0])
                    if owner_alias:
                        owner_col = self._resolve_column_name_for_dataset(
                            dataset_col_lookup.get(owners[0], set()),
                            sanitized_col_name,
                        ) or sanitized_col_name
                        old_ref = match.group(0)
                        new_ref = _format_metric_ref(owner_alias, owner_col)
                        normalized_sql = normalized_sql.replace(old_ref, new_ref)
                        logger.debug(
                            f"Normalized metric '{metric_name}': remapped {old_ref} → {new_ref}"
                        )
                        continue
            elif resolved_col != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, resolved_col)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue
            
            if col_name != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, sanitized_col_name)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                
                logger.debug(
                    f"Normalized metric '{metric_name}': {old_ref} → {new_ref}"
                )
        
        # Pattern 3: quote bare metric references to avoid parser ambiguity
        # when a metric name collides with a table/alias token (e.g., SENTIMENT).
        normalized_sql = self._quote_bare_metric_references(
            normalized_sql,
            metric_names,
        )

        normalized_sql = self._rewrite_metric_aggregate_wrappers(
            normalized_sql,
            metric_names,
        )

        normalized_sql = self._repair_bare_aggregate_identifiers(
            normalized_sql,
            metric_name,
            dataset_col_lookup,
            dataset_aliases,
            metric_names,
        )

        normalized_sql = self._normalize_date_part_arguments(normalized_sql)
        normalized_sql = self._qualify_bare_partition_identifiers(
            normalized_sql,
            dataset_col_lookup,
            dataset_aliases,
            preferred_table_alias=preferred_table_alias,
        )
        # Repair malformed chained identifiers occasionally produced by mixed
        # quoting rewrites, e.g. FACT."PRODUCT_KEY".PRODUCT_KEY.
        normalized_sql = self._dedupe_qualified_column_tokens(normalized_sql)
        normalized_sql = self._rewrite_window_metric_expression(
            normalized_sql,
            preferred_table_alias=preferred_table_alias,
        )
        normalized_sql = self._normalize_rolling_monthindex_max_predicates(normalized_sql)

        return normalized_sql

    @staticmethod
    def _dedupe_qualified_column_tokens(metric_sql: str) -> str:
        """Collapse duplicate chained column tokens on one table alias.

        Examples:
          FACT."PRODUCT_KEY".PRODUCT_KEY -> FACT."PRODUCT_KEY"
          FACT.PRODCODE.PRODCODE -> FACT.PRODCODE
        """
        import re

        if not metric_sql:
            return metric_sql

        repaired = metric_sql
        repaired = re.sub(
            r'(\b\w+\.)"([A-Z_][A-Z0-9_]*)"\.\2\b',
            r'\1"\2"',
            repaired,
        )
        repaired = re.sub(
            r'(\b\w+\.)([A-Z_][A-Z0-9_]*)\.\2\b',
            r'\1\2',
            repaired,
        )
        return repaired

    @staticmethod
    def _extract_referenced_table_aliases(metric_sql: str, valid_aliases: set[str]) -> set[str]:
        """Return table aliases referenced as ``ALIAS.COLUMN`` in a metric expression."""
        import re

        if not metric_sql:
            return set()

        referenced: set[str] = set()
        patterns = [
            r'(\w+)\."([^"]+)"',
            r'(\w+)\.([A-Za-z_][A-Za-z0-9_$]*)',
        ]
        for pattern in patterns:
            for alias, _ in re.findall(pattern, metric_sql):
                if alias in valid_aliases:
                    referenced.add(alias)
        return referenced

    def _resolve_metric_emission_alias(
        self,
        default_alias: str,
        metric_sql: str,
        dataset_aliases: Dict[str, str],
    ) -> str:
        """Select a semantic metric entity alias that avoids unrelated-entity errors.

        Snowflake semantic metrics can reject a metric defined under one entity
        when the SQL expression only references a different entity. If a metric
        expression clearly references exactly one table alias and it differs from
        the default metric alias, emit the metric under that referenced alias.
        """
        valid_aliases = set(dataset_aliases.values())
        referenced_aliases = self._extract_referenced_table_aliases(metric_sql, valid_aliases)
        if len(referenced_aliases) == 1:
            only_alias = next(iter(referenced_aliases))
            if only_alias != default_alias:
                return only_alias
        return default_alias

    @staticmethod
    def _rewrite_window_metric_expression(
        metric_sql: str,
        preferred_table_alias: Optional[str] = None,
    ) -> str:
        """Rewrite window-based metric SQL to semantic-safe aggregate SQL.

        Snowflake semantic metrics disallow expressions that embed window
                functions over related entities inside metric definitions.
                Convert common ratio form:
          DIV0(SUM(x), SUM(x) OVER (...))
        into:
          SUM(x)
        so BI/query clients can apply grouping context dynamically.

                Preserve supported time-intelligence windows used by SPLY/YTD
                synchronization. Unknown/unsafe window shapes still downgrade to
                ``NULL`` so deployment can succeed conservatively.
        """
        import re

        if not metric_sql or "OVER" not in metric_sql.upper():
            return metric_sql

        ratio_pattern = re.compile(
            r'(?is)^\s*DIV0\s*\(\s*SUM\((?P<num>[^\)]+)\)\s*,\s*'
            r'SUM\((?P<den>[^\)]+)\)\s+OVER\s*\([^\)]*\)\s*\)\s*$'
        )
        match = ratio_pattern.match(metric_sql.strip())
        if match:
            numerator = match.group("num").strip()
            denominator = match.group("den").strip()
            if numerator.upper() != denominator.upper():
                return "NULL"
            return f"SUM({numerator})"

        # Preserve common period-to-date aggregate windows, e.g.
        # SUM(x) OVER (PARTITION BY year ORDER BY date ...)
        ytd_like_pattern = re.compile(
            r'(?is)^\s*(SUM|AVG|COUNT|MIN|MAX)\s*\([^\)]+\)\s+OVER\s*\('
            r'\s*PARTITION\s+BY\s+.+?\s+ORDER\s+BY\s+.+?\)\s*$'
        )
        if ytd_like_pattern.match(metric_sql.strip()):
            if preferred_table_alias:
                window_alias_refs = set(
                    a.upper()
                    for a in re.findall(
                        r'(?i)\b(\w+)\s*\.',
                        metric_sql.split("OVER", 1)[1] if "OVER" in metric_sql.upper() else "",
                    )
                )
                if window_alias_refs and any(a != preferred_table_alias.upper() for a in window_alias_refs):
                    return "NULL"
            return metric_sql

        # Preserve SPLY-style lag/lead windows.
        sply_like_pattern = re.compile(
            r'(?is)^\s*(LAG|LEAD)\s*\(\s*.+?\)\s+OVER\s*\(.*\)\s*$'
        )
        if sply_like_pattern.match(metric_sql.strip()):
            return metric_sql

        return "NULL"

    def _build_known_metric_fallback_expression(
        self,
        metric_name: str,
        fact_alias: str,
        scenario_alias: Optional[str],
        calendar_alias: Optional[str],
        dataset_col_lookup: Optional[Dict[str, set[str]]] = None,
    ) -> Optional[str]:
        """Deprecated compatibility hook.

        Hardcoded metric formulas have been removed. Metrics should be emitted
        through the normal auto-translation path or left as NULL placeholders
        when no translated expression exists.
        """
        return None

    def _qualify_bare_partition_identifiers(
        self,
        metric_sql: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        preferred_table_alias: Optional[str] = None,
    ) -> str:
        """Qualify bare PARTITION BY identifiers.

        Prefer resolving identifiers against the current metric entity alias when
        provided, then fall back to unique owner lookup across all datasets.
        """
        import re

        if not metric_sql:
            return metric_sql

        alias_to_dataset = {alias: ds for ds, alias in dataset_aliases.items()}

        # Replace only simple bare identifiers used directly after PARTITION BY.
        # Example: PARTITION BY "FUNCTION" -> PARTITION BY COST_CENTER_HIERARCHY."FUNCTION"
        pattern = re.compile(r'(?i)(PARTITION\s+BY\s+)("?[A-Z_][A-Z0-9_]*"?)')

        def _replace(match: re.Match) -> str:
            prefix = match.group(1)
            raw_identifier = match.group(2)
            identifier = self._sanitize_col_name(raw_identifier.strip('"'))

            if preferred_table_alias:
                preferred_dataset = alias_to_dataset.get(preferred_table_alias)
                if preferred_dataset:
                    preferred_columns = dataset_col_lookup.get(preferred_dataset, set())
                    preferred_resolved = self._resolve_column_name_for_dataset(
                        preferred_columns,
                        identifier,
                    )
                    if preferred_resolved:
                        return f'{prefix}{preferred_table_alias}."{preferred_resolved}"'

            owners: list[str] = []
            for ds_name, cols in dataset_col_lookup.items():
                if identifier in cols:
                    owners.append(ds_name)

            if len(owners) != 1:
                return match.group(0)

            owner_alias = dataset_aliases.get(owners[0])
            if not owner_alias:
                return match.group(0)

            return f'{prefix}{owner_alias}."{identifier}"'

        return pattern.sub(_replace, metric_sql)

    def _repair_bare_aggregate_identifiers(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
    ) -> str:
        """Repair invalid aggregate forms like ``SUM(\"TABLE_ALIAS\")``.

        Some translated expressions accidentally keep only a dataset/table token
        inside an aggregate function, which yields Snowflake errors such as
        ``invalid identifier 'SPEND_FACT'``. When that happens, rewrite to a
        deterministic physical column from the referenced dataset.
        """
        import re

        if not metric_sql:
            return metric_sql

        alias_to_dataset = {alias: ds for ds, alias in dataset_aliases.items()}
        sanitized_ds_to_dataset = {
            self._sanitize_col_name(ds): ds for ds in dataset_aliases.keys()
        }

        agg_pattern = re.compile(
            r'\b(SUM|AVG|MIN|MAX|COUNT|DISTINCTCOUNT)\s*\(\s*"?([A-Z_][A-Z0-9_]*)"?\s*\)',
            flags=re.IGNORECASE,
        )

        def _replace(match: re.Match) -> str:
            agg_fn = match.group(1).upper()
            ident = self._sanitize_col_name(match.group(2))

            # Keep valid semantic-metric aggregates untouched.
            if metric_names and ident in metric_names:
                return match.group(0)

            dataset_name = alias_to_dataset.get(ident) or sanitized_ds_to_dataset.get(ident)
            if not dataset_name:
                return match.group(0)

            dataset_alias = dataset_aliases.get(dataset_name)
            known_columns = dataset_col_lookup.get(dataset_name, set())
            preferred_col = self._pick_preferred_aggregate_column(
                metric_name,
                known_columns,
            )
            if not dataset_alias or not preferred_col:
                return match.group(0)

            if agg_fn == "DISTINCTCOUNT":
                return f'COUNT(DISTINCT {dataset_alias}.{preferred_col})'
            return f'{agg_fn}({dataset_alias}.{preferred_col})'

        return agg_pattern.sub(_replace, metric_sql)

    def _pick_preferred_aggregate_column(
        self,
        metric_name: str,
        known_columns: set[str],
    ) -> Optional[str]:
        """Pick a deterministic physical column for aggregate repairs."""
        if not known_columns:
            return None

        metric_tokens = [t for t in self._sanitize_col_name(metric_name).split("_") if t]
        metric_token_set = set(metric_tokens)

        value_terms = {
            "AMOUNT", "REVENUE", "SALES", "SPEND", "VALUE", "COST", "PRICE",
            "TOTAL", "QTY", "QUANTITY", "UNITS", "USD",
        }
        categorical_terms = {
            "TYPE", "CATEGORY", "STATUS", "FLAG", "NAME", "DESC", "DESCRIPTION",
            "CODE", "GROUP", "CLASS", "SEGMENT",
        }
        excluded_suffixes = ("_CK", "_ID", "_KEY", "_DATE")

        scored: list[tuple[int, str]] = []
        for col in sorted(known_columns):
            tokens = [t for t in col.split("_") if t]
            token_set = set(tokens)
            score = 0

            overlap = len(metric_token_set.intersection(token_set))
            score += overlap * 10

            if token_set.intersection(value_terms):
                score += 8

            if token_set.intersection(categorical_terms):
                score -= 18

            if col.endswith(excluded_suffixes):
                score -= 20

            if "AMOUNT" in token_set:
                score += 4

            scored.append((score, col))

        if not scored:
            return None

        scored.sort(key=lambda item: (item[0], -len(item[1])), reverse=True)
        best_score = scored[0][0]
        if best_score < 1:
            return None

        best = [col for score, col in scored if score == best_score]
        return sorted(best, key=lambda c: (len(c), c))[0]

    @staticmethod
    def _resolve_column_name_for_dataset(
        known_columns: set[str],
        candidate: str,
    ) -> Optional[str]:
        """Resolve LLM-drifted column names to an actual dataset column."""
        if not known_columns:
            return None

        if candidate in known_columns:
            return candidate

        # underscore-insensitive match: IS_VAN_ARSDEL -> ISVANARSDEL
        compact = candidate.replace("_", "")
        for col in known_columns:
            if col.replace("_", "") == compact:
                return col

        # common LLM drift: TOTAL_UNITS -> UNITS
        if candidate.startswith("TOTAL_"):
            base = candidate[len("TOTAL_"):]
            if base in known_columns:
                return base

        # lightweight stemming for token overlap: UNITS ~= UNIT, CATEGORIES ~= CATEGORY
        def _stem(token: str) -> str:
            t = token.upper()
            if len(t) > 4 and t.endswith("IES"):
                return t[:-3] + "Y"
            if len(t) > 3 and t.endswith("S"):
                return t[:-1]
            return t

        # Generic token-based fallback for LLM drift without domain-specific
        # hardcoding. Pick a unique best overlap candidate when available.
        candidate_tokens = [t for t in candidate.split("_") if t]
        if candidate_tokens:
            scored: list[tuple[int, str]] = []
            candidate_token_set = {_stem(t) for t in candidate_tokens}
            for col in known_columns:
                col_tokens = [t for t in col.split("_") if t]
                if not col_tokens:
                    continue
                col_token_set = {_stem(t) for t in col_tokens}
                overlap = len(candidate_token_set.intersection(col_token_set))
                if overlap == 0:
                    continue
                # Prefer higher overlap and shorter distance in token length.
                score = overlap * 10 - abs(len(candidate_tokens) - len(col_tokens))
                scored.append((score, col))

            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                best_score = scored[0][0]
                best = [col for score, col in scored if score == best_score]
                if len(best) == 1:
                    return best[0]

        # Generic semantic synonym fallback for common business-measure drift.
        # Only apply when it yields exactly one deterministic match.
        synonym_groups = [
            {"AMOUNT", "REVENUE", "SALES", "VALUE"},
            {"UNIT", "UNITS", "QUANTITY", "QTY", "COUNT", "VOLUME"},
        ]
        compact_candidate_tokens = {_stem(t) for t in candidate.split("_") if t}
        for group in synonym_groups:
            normalized_group = {_stem(t) for t in group}
            if not compact_candidate_tokens.intersection(normalized_group):
                continue
            candidates_in_group = []
            for col in known_columns:
                col_tokens = {_stem(t) for t in col.split("_") if t}
                if col_tokens.intersection(normalized_group):
                    candidates_in_group.append(col)
            if len(candidates_in_group) == 1:
                return candidates_in_group[0]

        # common drift: SALES_DATE -> DATE
        if candidate.endswith("_DATE") and "DATE" in known_columns:
            return "DATE"

        # Metric partition-key fallback: DAX "Function" often corresponds to
        # a cost-center attribute in fact tables.
        if candidate == "FUNCTION":
            for fallback in ("COST_CENTER", "SOURCE_COST_CENTER", "SUB_FUNCTION"):
                if fallback in known_columns:
                    return fallback

        return None

    @staticmethod
    def _resolve_metric_reference_name(
        metric_names: Optional[set[str]],
        candidate: str,
        *,
        allow_fuzzy: bool = True,
    ) -> Optional[str]:
        """Resolve a possibly drifted identifier to a known semantic metric name."""
        if not metric_names:
            return None

        if candidate in metric_names:
            return candidate

        compact_candidate = candidate.replace("_", "")
        compact_matches = [
            m for m in metric_names
            if m.replace("_", "") == compact_candidate
        ]
        if len(compact_matches) == 1:
            return compact_matches[0]

        if not allow_fuzzy:
            # For qualified TABLE.COLUMN references we should be strict.
            # Fuzzy matching can incorrectly reinterpret physical columns as
            # semantic metrics and trigger avoidable drops.
            return None

        suffix_matches = [
            m for m in metric_names
            if m.endswith(f"_{candidate}") or m.startswith(f"{candidate}_")
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        contains_matches = [
            m for m in metric_names
            if candidate in m
        ]
        if len(contains_matches) == 1:
            return contains_matches[0]

        return None

    @staticmethod
    def _quote_bare_metric_references(
        metric_sql: str,
        metric_names: Optional[set[str]],
    ) -> str:
        """Quote bare metric references so they are treated as metric identifiers."""
        if not metric_names:
            return metric_sql

        import re

        normalized = metric_sql
        for metric_name in sorted(metric_names, key=len, reverse=True):
            pattern = rf'(?<![\w\.\"])\b{re.escape(metric_name)}\b(?![\w\."])'
            normalized = re.sub(pattern, f'"{metric_name}"', normalized)

        return normalized

    @staticmethod
    def _rewrite_metric_aggregate_wrappers(
        metric_sql: str,
        metric_names: Optional[set[str]],
    ) -> str:
        """Rewrite invalid AGG("METRIC") forms to direct metric references."""
        if not metric_names:
            return metric_sql

        import re

        normalized = metric_sql
        agg_pattern = r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)(?!\s+OVER\b)'

        def _replace(match: re.Match) -> str:
            metric_name = match.group(2)
            if metric_name in metric_names:
                return f'"{metric_name}"'
            return match.group(0)

        normalized = re.sub(agg_pattern, _replace, normalized)

        # Snowflake semantic metrics reject wrappers like
        # SUM("M1" - "M2") where the argument is already metric-level arithmetic.
        # Unwrap these to "M1" - "M2" while leaving physical-column aggregates intact.
        composite_agg_pattern = r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*((?:"[A-Z_][A-Z0-9_]*"\s*[+\-*/]\s*)+"[A-Z_][A-Z0-9_]*")\s*\)'

        def _replace_composite(match: re.Match) -> str:
            expr = match.group(2)
            metric_refs = set(re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr))
            if metric_refs and all(ref in metric_names for ref in metric_refs):
                return expr
            return match.group(0)

        return re.sub(composite_agg_pattern, _replace_composite, normalized)

    @staticmethod
    def _prune_unresolved_metric_lines(
        metrics_lines: list[str],
        metric_name_set: Optional[set[str]],
    ) -> list[str]:
        """Drop metrics that still reference unresolved semantic metrics."""
        if not metrics_lines or not metric_name_set:
            return metrics_lines

        import re

        current = list(metrics_lines)
        while True:
            defined: set[str] = set()
            window_metrics: set[str] = set()
            parsed: list[tuple[str, Optional[str], Optional[str], str]] = []
            expr_by_name: dict[str, str] = {}
            for line in current:
                m = re.search(r'([A-Z_][A-Z0-9_]*)\."([^\"]+)"\s+AS\s+(.+?)\s*$', line.strip().rstrip(','))
                if not m:
                    parsed.append((line, None, None, ""))
                    continue
                alias = m.group(1)
                name = m.group(2)
                expr = m.group(3)
                defined.add(name)
                expr_by_name[name] = expr
                if re.search(r'\bOVER\b', expr, flags=re.IGNORECASE):
                    window_metrics.add(name)
                parsed.append((line, alias, name, expr))

            removed = False
            rewritten = False
            next_lines: list[str] = []
            for line, alias, name, expr in parsed:
                if not name:
                    next_lines.append(line)
                    continue

                refs = set(re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr))
                unresolved = [
                    r for r in refs
                    if r in metric_name_set and r not in defined and r != name
                ]
                if unresolved:
                    logger.warning(
                        "Dropping metric '%s' due unresolved metric refs %s",
                        name,
                        sorted(unresolved),
                    )
                    removed = True
                    continue

                window_refs = [
                    r for r in refs
                    if r in window_metrics and r != name
                ]
                if window_refs:
                    expanded_expr = expr
                    substituted = False
                    for ref_name in sorted(set(window_refs)):
                        ref_expr = expr_by_name.get(ref_name)
                        if not ref_expr:
                            continue
                        # Inline the referenced window metric expression so the
                        # dependent metric does not directly reference a window metric.
                        expanded_expr = re.sub(
                            rf'"{re.escape(ref_name)}"',
                            f'({ref_expr})',
                            expanded_expr,
                        )
                        substituted = True

                    if substituted:
                        next_lines.append(f'  {alias}."{name}" AS {expanded_expr}')
                        rewritten = True
                        continue

                    logger.warning(
                        "Dropping metric '%s' because Snowflake disallows using window metrics in derived expressions: %s",
                        name,
                        sorted(window_refs),
                    )
                    removed = True
                    continue

                next_lines.append(line)

            current = next_lines
            if not removed and not rewritten:
                return current

    @staticmethod
    def _normalize_date_part_arguments(metric_sql: str) -> str:
        """Cast date-like identifiers in date-part functions to DATE."""
        import re

        normalized = metric_sql

        def _is_date_like(identifier: str) -> bool:
            upper = identifier.upper()
            return upper.endswith('.DATE') or upper.endswith('_DATE')

        def _wrap_try_to_date(match: re.Match) -> str:
            fn = match.group(1)
            arg = match.group(2).strip()
            if _is_date_like(arg) and 'TRY_TO_DATE(' not in arg.upper():
                return f"{fn}(TRY_TO_DATE({arg}))"
            return match.group(0)

        normalized = re.sub(
            r'\b(YEAR|MONTH|DAY|WEEK|QUARTER)\s*\(\s*([^\)]+)\)',
            _wrap_try_to_date,
            normalized,
            flags=re.IGNORECASE,
        )

        def _wrap_extract(match: re.Match) -> str:
            part = match.group(1)
            arg = match.group(2).strip()
            if _is_date_like(arg) and 'TRY_TO_DATE(' not in arg.upper():
                return f"EXTRACT({part} FROM TRY_TO_DATE({arg}))"
            return match.group(0)

        normalized = re.sub(
            r'\bEXTRACT\s*\(\s*([A-Z_]+)\s+FROM\s+([^\)]+)\)',
            _wrap_extract,
            normalized,
            flags=re.IGNORECASE,
        )

        return normalized

    @staticmethod
    def _normalize_rolling_monthindex_max_predicates(metric_sql: str) -> str:
        """Rewrite MAX-based rolling month predicates into row-level predicates.

        Snowflake semantic metrics require a single aggregate over a row-level
        expression. Predicates like
        `MONTHINDEX <= MAX(MONTHINDEX) AND MONTHINDEX > MAX(MONTHINDEX)-12`
        embed nested aggregates and fail compilation.
        """
        import re

        normalized = metric_sql

        pattern = (
            r'(?P<id>[A-Z_][A-Z0-9_\.]+)\s*<=\s*MAX\(\s*(?P=id)\s*\)\s*'
            r'AND\s*(?P=id)\s*>\s*MAX\(\s*(?P=id)\s*\)\s*-\s*12'
        )

        def _replace(match: re.Match) -> str:
            month_index_id = match.group('id')
            return (
                f"{month_index_id} > ((YEAR(CURRENT_DATE()) * 12) + "
                "MONTH(CURRENT_DATE()) - 12)"
            )

        return re.sub(pattern, _replace, normalized, flags=re.IGNORECASE)

    def authenticate(self) -> None:
        """Establish connection to Snowflake."""
        import snowflake.connector
        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
        kwargs = get_snowflake_connect_kwargs(self.config)
        self._connection = snowflake.connector.connect(**kwargs)

    def discover(self) -> Dict[str, Any]:
        """List tables and views in the schema."""
        if not self._connection:
            self.authenticate()
        
        cur = self._connection.cursor()
        self._execute_sql(cur, f"SHOW TABLES IN SCHEMA {self.config.schema_name}", context="SHOW TABLES")
        tables = [row[1] for row in cur.fetchall()]
        
        self._execute_sql(cur, f"SHOW VIEWS IN SCHEMA {self.config.schema_name}", context="SHOW VIEWS")
        views = [row[1] for row in cur.fetchall()]
        
        return {"tables": tables, "views": views}

    def validate_permissions(self) -> List[str]:
        """
        Validate Snowflake RBAC permissions.
        Required: USAGE on DB, USAGE on SCHEMA, CREATE SEMANTIC VIEW on SCHEMA.
        """
        warnings = []
        if not self._connection:
            self.authenticate()
            
        cur = self._connection.cursor()
        try:
            # Check USAGE on Schema
            self._execute_sql(cur, f"USE SCHEMA {self.config.database}.{self.config.schema_name}", context="USE SCHEMA")
            
            # Check CREATE SEMANTIC VIEW privilege (may use a proxy check like SHOW GRANTS)
            # For simplicity, we try a no-op check or rely on explicit GRANT verification
            self._execute_sql(cur, "SELECT current_role()", context="SELECT current_role()")
            role = cur.fetchone()[0]
            logger.info(f"Validating permissions for role: {role}")
            
        except Exception as e:
            logger.error(f"Snowflake RBAC check failed: {e}")
            raise ConnectorError(f"Snowflake RBAC validation failed: {e}")
            
        return warnings

    def emit(self, sml: Any) -> Dict[str, Any]:
        """Emit SML model to Snowflake."""
        success = self.deploy(sml)
        return {"success": success}

    def validate_target(self) -> bool:
        """Check if Snowflake is reachable."""
        try:
            self.authenticate()
            return True
        except Exception:
            return False

    @property
    def max_concurrency(self) -> int:
        """Snowflake DDL operations should be serialized more strictly."""
        return 3

    def _execute_with_retry(
        self,
        cursor,
        sql: str,
        *,
        max_retries: int = 3,
        base_delay: float = 2.0,
        retryable_codes: tuple = (),
    ) -> Any:
        """Execute a SQL statement with exponential-backoff retry.

        Retries on transient Snowflake errors such as timeout / load-shedding
        or warehouse-suspended states.  Non-transient errors (syntax,
        missing objects) are raised immediately.
        """
        import snowflake.connector

        # Snowflake error codes considered transient:
        #   000625 – Statement timed out
        #   000707 – Warehouse load shedding
        #   390114 – Authentication token expired (can happen on long sessions)
        _TRANSIENT_CODES = {
            "000625", "000707", "390114",
            *retryable_codes,
        }
        # Also retry generic DatabaseError containing these phrases
        _TRANSIENT_PHRASES = (
            "timeout",
            "load shedding",
            "semaphore",
            "warehouse",
            "connection reset",
            "broken pipe",
        )

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                if attempt == 1:
                    logger.info("Executing SQL via retry wrapper:\n%s", self._ddl_preview(sql))
                return cursor.execute(sql)
            except snowflake.connector.errors.ProgrammingError as e:
                # Non-transient SQL issues: log statement context before failing.
                sql_preview = "\\n".join(sql.splitlines()[:40])
                logger.error(
                    "Snowflake ProgrammingError during SQL execution "
                    f"(errno={getattr(e, 'errno', 'n/a')}, sqlstate={getattr(e, 'sqlstate', 'n/a')}, "
                    f"sfqid={getattr(e, 'sfqid', 'n/a')}): {e}"
                )
                logger.error(f"Failing SQL preview (first 40 lines):\\n{sql_preview}")
                raise
            except snowflake.connector.errors.DatabaseError as e:
                err_msg = str(e).lower()
                err_code = getattr(e, "errno", None) or getattr(e, "sfqid", "")
                is_transient = (
                    str(err_code) in _TRANSIENT_CODES
                    or any(p in err_msg for p in _TRANSIENT_PHRASES)
                )
                if not is_transient or attempt == max_retries:
                    raise
                last_exc = e
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"Transient Snowflake error (attempt {attempt}/{max_retries}), "
                    f"retrying in {delay:.0f}s: {e}"
                )
                time.sleep(delay)
            except Exception as e:
                err_msg = str(e).lower()
                if any(p in err_msg for p in _TRANSIENT_PHRASES) and attempt < max_retries:
                    last_exc = e
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"Transient error (attempt {attempt}/{max_retries}), "
                        f"retrying in {delay:.0f}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc  # pragma: no cover

    @staticmethod
    def _ddl_preview(sql: str, *, max_lines: int = 30) -> str:
        """Return a compact preview of a DDL statement for terminal tracing."""
        lines = sql.splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        preview = "\n".join(lines[:max_lines])
        return f"{preview}\n... ({len(lines) - max_lines} more lines)"

    @staticmethod
    def _is_client_data_model(model_name: Optional[str]) -> bool:
        """Return True only for the legacy Client Data model compatibility path."""
        return str(model_name or "").strip().lower() == "client data"

    def _format_physical_column_ref(
        self,
        alias: str,
        phys_col: str,
        *,
        model_name: Optional[str] = None,
    ) -> str:
        """Format a physical column reference for semantic-view emission.

        Most models keep the current quoted form. Client Data gets a narrow
        compatibility path for dollar-sign columns because Snowflake semantic
        view compilation is sensitive to those identifiers in this model only.
        """
        if alias:
            if self._is_client_data_model(model_name) and "$" in phys_col:
                return f"{alias}.{phys_col}"
            return f'{alias}."{phys_col}"'
        return f'"{phys_col}"'

    def _execute_sql(
        self,
        cursor,
        sql: str,
        params: Optional[tuple[Any, ...]] = None,
        *,
        context: str = "SQL",
    ) -> Any:
        """Log and execute a Snowflake statement in one place."""
        logger.info("%s:\n%s", context, self._ddl_preview(sql))
        if params is None:
            return cursor.execute(sql)
        return cursor.execute(sql, params)

    
    # -----------------------------------------------------------------
    # Session management (P2a): keeps a single Snowflake connection open
    # across multiple deploy() calls so we pay the auth handshake once.
    # -----------------------------------------------------------------
    def _resolve_warehouse(self, operation: str = "default") -> str:
        """Mandate 5: Resolve warehouse name based on operation type.

        Uses ``behavior.snowflake.warehouse_mapping`` when available,
        falling back to ``config.warehouse``.

        Parameters
        ----------
        operation
            Logical operation name, e.g. ``"ddl"``, ``"data_sync"``,
            ``"semantic_view"``.  Matched against mapping keys.
        """
        mapping = getattr(self.sf_behavior, "warehouse_mapping", None) or {}
        return mapping.get(operation, self.config.warehouse)

    def open_session(self, operation: str = "default") -> None:
        """Open a shared Snowflake session for batch deployments.

        When a session is active, ``deploy()`` reuses the same connection
        instead of opening (and closing) a new one per model.  Call
        ``close_session()`` when the batch is complete.
        """
        if self._session_conn is not None:
            return  # already open
        import snowflake.connector

        logger.info(f"Opening Snowflake session for batch deployment: {self.config.account}")
        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
        kwargs = get_snowflake_connect_kwargs(self.config)
        # Override warehouse if operation-specific mapping exists
        resolved_wh = self._resolve_warehouse(operation)
        if resolved_wh:
            kwargs["warehouse"] = resolved_wh
        kwargs["session_parameters"] = {
            "QUERY_TAG": self.sf_behavior.query_tag or "Semabridge_Connector"
        }
        self._session_conn = snowflake.connector.connect(**kwargs)

    def close_session(self) -> None:
        """Close the shared Snowflake session and reset caches."""
        if self._session_conn is not None:
            try:
                self._session_conn.close()
            except Exception as exc:
                logger.warning(f"Error closing Snowflake session: {exc}")
            finally:
                self._session_conn = None
                self._verified_tables.clear()

    # -----------------------------------------------------------------
    # Module 1: Pre-deployment Snowflake metadata introspection
    # -----------------------------------------------------------------

    def _fetch_schema_metadata(self, cursor) -> Dict[str, set]:
        """Query INFORMATION_SCHEMA for all table/column metadata in the
        configured schema.

        Returns a mapping suitable for ``GlobalValidator.snowflake_metadata``::

            { "TABLE_NAME": {"COL_A", "COL_B", …}, … }

        Uses the *existing* cursor so no extra connection is opened.
        Errors are logged and swallowed — the caller falls back to
        validation without metadata (Tiers 1-5 only).
        """
        try:
            query = (
                "SELECT TABLE_NAME, COLUMN_NAME "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_CATALOG = %s AND TABLE_SCHEMA = %s "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION"
            )
            self._execute_sql(
                cursor,
                query,
                (self.config.database.upper(), self.config.schema_name.upper()),
                context="INFORMATION_SCHEMA metadata",
            )
            rows = cursor.fetchall()

            result: Dict[str, set] = {}
            for table_name, col_name in rows:
                key = table_name.upper()
                if key not in result:
                    result[key] = set()
                result[key].add(col_name.upper())

            logger.info(
                f"Fetched INFORMATION_SCHEMA metadata for "
                f"{len(result)} tables in "
                f"{self.config.database}.{self.config.schema_name}"
            )
            return result

        except Exception as exc:
            logger.warning(
                f"Could not fetch INFORMATION_SCHEMA metadata "
                f"(Tier 6 validation will be skipped): {exc}"
            )
            return {}

    def _check_semantic_view_exists(self, cursor, view_name: str) -> bool:
        """Check whether a Semantic View already exists in Snowflake.

        Uses ``SHOW SEMANTIC VIEWS LIKE '…'`` which is the only reliable
        discovery mechanism for Semantic Views (they don't appear in
        INFORMATION_SCHEMA.VIEWS).

        Returns ``True`` if the view exists, ``False`` otherwise.
        """
        try:
            # Sanitize the view name for LIKE pattern matching
            # Handle both quoted and unquoted names
            check_name = view_name.strip('"').upper()
            safe_name = re.sub(r"[^A-Za-z0-9_]", "_", check_name).upper()
            
            # Also try with and without the semantic suffix variations
            patterns = [
                safe_name,  # Exact name (already sanitized)
                safe_name + "_SEMANTIC" if not safe_name.upper().endswith("_SEMANTIC") else safe_name,  # With SEMANTIC suffix
                safe_name + "_semantic" if not safe_name.lower().endswith("_semantic") else safe_name,  # With lowercase suffix
            ]
            
            for pattern in patterns:
                self._execute_sql(cursor, f"SHOW SEMANTIC VIEWS LIKE '{pattern}'", context="SHOW SEMANTIC VIEWS")
                rows = cursor.fetchall()
                if len(rows) > 0:
                    return True
            return False
        except Exception as exc:
            logger.debug(
                f"SHOW SEMANTIC VIEWS check failed for '{view_name}': {exc}"
            )
            return False

    def deploy(self, sml: SMLModel, parallel: bool = False, max_workers: int = 4) -> bool:
        """
        Deploy the SML model to Snowflake.
        
        Args:
            sml: The SML model to deploy
            parallel: Enable parallel processing (unused, for API compatibility)
            max_workers: Maximum number of worker threads (unused, for API compatibility)
        
        1. Check for missing source tables and create them.
        2. Generate Semantic View DDL.
        3. Execute DDLs.
        4. Generate Cortex YAML.

        If ``open_session()`` was called beforehand the shared connection
        is reused; otherwise a per-call connection is created (backward
        compatible).
        """
        try:
            deploy_started_at = time.perf_counter()
            model_label = sml.unique_name or sml.label or "<unnamed_sml_model>"
            logger.info(
                "[deploy] start model=%s datasets=%s metrics=%s",
                model_label,
                len(getattr(sml, "datasets", []) or []),
                len(getattr(sml, "metrics", []) or []),
            )

            # Decide connection strategy: session vs per-call
            if self._session_conn is not None:
                conn = self._session_conn
                owns_conn = False
                logger.debug("Reusing shared Snowflake session connection")
            else:
                import snowflake.connector
                from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
                logger.info(f"Connecting to Snowflake: {self.config.account}")
                kwargs = get_snowflake_connect_kwargs(self.config)
                kwargs["session_parameters"] = {
                    "QUERY_TAG": self.sf_behavior.query_tag or "Semabridge_Connector"
                }
                conn = snowflake.connector.connect(**kwargs)
                owns_conn = True
            
            logger.info("Starting deployment with STRICT sanitization rules")
            
            try:
                cur = conn.cursor()
                
                # Step 0: Legacy Cleanup (if enabled)
                step_started_at = time.perf_counter()
                if self.behavior.legacy.drop_deprecated_views:
                    logger.info("[deploy] step0 legacy cleanup start")
                    self._drop_deprecated_views(cur, sml)
                logger.info("[deploy] step0 complete in %.2fs", time.perf_counter() - step_started_at)

                # Step 1: Auto-create missing source tables
                step_started_at = time.perf_counter()
                logger.info("[deploy] step1 source-table bootstrap start")
                if self.sf_behavior.create_missing_tables:
                    self._ensure_source_tables_exist(cur, sml)
                else:
                    logger.info("Skipping table creation (create_missing_tables=False)")
                logger.info("[deploy] step1 complete in %.2fs", time.perf_counter() - step_started_at)

                # Step 1.25: Optional physical type-fix via CTAS + SWAP.
                step_started_at = time.perf_counter()
                logger.info("[deploy] step1.25 inferred-type pass start")
                if self.sf_behavior.apply_inferred_types:
                    self._apply_inferred_types_ctas_sml(cur, sml)
                else:
                    logger.info("Skipping inferred datatype CTAS fix (apply_inferred_types=False)")
                logger.info("[deploy] step1.25 complete in %.2fs", time.perf_counter() - step_started_at)
                
                # Step 1.5: Pre-deployment validation gate
                # Catches PK, identifier, and relationship issues BEFORE SQL.
                # Module 1: Fetch live Snowflake metadata so Tier 6 can
                # verify that every referenced source table/column exists.
                try:
                    step_started_at = time.perf_counter()
                    logger.info("[deploy] step1.5 validation start")
                    from semabridge.core.validation.global_validator import GlobalValidator
                    sf_meta = self._fetch_schema_metadata(cur)
                    validator = GlobalValidator(
                        self._id, self.sf_behavior,
                        snowflake_metadata=sf_meta or None,
                    )
                    val_report = validator.validate(sml, halt_on_error=True)
                    if val_report.warning_count > 0:
                        logger.warning(
                            f"Pre-deployment validation passed with "
                            f"{val_report.warning_count} warning(s)"
                        )
                except ImportError:
                    logger.debug("Global validator not available — falling back")
                    try:
                        from semabridge.core.validation.validate_semantic_model import (
                            validate_pre_deployment,
                        )
                        validate_pre_deployment(sml, self.sf_behavior, self._id)
                    except ImportError:
                        logger.debug("Pre-deployment validator not available — skipping")
                # Note: SemaBridgeValidationError propagates up intentionally
                    logger.info("[deploy] step1.5 validation complete in %.2fs", time.perf_counter() - step_started_at)

                # Step 2: Generate and execute DDLs
                step_started_at = time.perf_counter()
                logger.info("[deploy] step2 DDL generation start")
                ddls = self.generate_ddls(sml)
                logger.info("Generated Snowflake DDLs")
                logger.info("[deploy] step2 DDL generation complete in %.2fs", time.perf_counter() - step_started_at)

                if ddls:
                    self._guard_relationship_clause(
                        getattr(sml, "unique_name", None)
                        or getattr(sml, "label", None)
                        or "<unnamed_sml_model>",
                        getattr(sml, "relationships", []),
                        ddls[0],
                        fail_on_missing=True,
                    )
                
                for i, ddl in enumerate(ddls):
                    logger.info(f"Executing DDL statement {i+1}/{len(ddls)}...")
                    logger.info(
                        "DDL preview for statement %s/%s:\n%s",
                        i + 1,
                        len(ddls),
                        self._ddl_preview(ddl),
                    )
                    try:
                        ddl_started_at = time.perf_counter()
                        self._execute_with_retry(cur, ddl)
                        logger.info(
                            "[deploy] statement %s/%s executed in %.2fs",
                            i + 1,
                            len(ddls),
                            time.perf_counter() - ddl_started_at,
                        )
                    except Exception as ddl_ex:
                        ddl_preview = "\\n".join(ddl.splitlines()[:60])
                        logger.error(
                            f"DDL statement {i+1}/{len(ddls)} failed "
                            f"(length={len(ddl)} chars): {ddl_ex}"
                        )
                        logger.error(
                            f"DDL statement {i+1} preview (first 60 lines):\\n{ddl_preview}"
                        )
                        raise
                
                # Step 3: Generate and Save Cortex YAML
                try:
                    yaml_content = self.generate_cortex_yaml(sml)
                    
                    project_root = Path(__file__).resolve().parents[3]
                    safe_name = re.sub(r'[^\w\-.]', '_', sml.unique_name or sml.label or "model")
                    output_dir = project_root / "output" / "reverse" / safe_name
                    output_dir.mkdir(parents=True, exist_ok=True)
                    
                    yaml_path = output_dir / "cortex_analyst.yaml"
                    with open(yaml_path, "w") as f:
                        f.write(yaml_content)
                    logger.info(f"Cortex Analyst YAML saved to {yaml_path}")
                except Exception as ex:
                    logger.warning(f"Failed to save Cortex YAML: {ex}")

                # Step 3b: Save semantic view DDL as YAML for auditing
                try:
                    from datetime import datetime, timezone
                    ddl_output = {
                        "metadata": {
                            "model_name": sml.unique_name or sml.label or "model",
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "ddl_count": len(ddls),
                            "path": "SML",
                        },
                        "relationships": [
                            {
                                "name": rel.unique_name,
                                "from_dataset": rel.from_dataset,
                                "from_columns": rel.from_columns,
                                "to_dataset": rel.to_dataset,
                                "to_columns": rel.to_columns,
                                "cardinality": str(rel.cardinality) if rel.cardinality else None,
                                "is_active": rel.is_active,
                            }
                            for rel in (sml.relationships or [])
                        ],
                        "ddl_statements": ddls,
                    }
                    ddl_yaml_path = output_dir / "semantic_view_ddl.yaml"
                    with open(ddl_yaml_path, "w") as f:
                        yaml.dump(ddl_output, f, default_flow_style=False, sort_keys=False, allow_unicode=True, width=200)
                    logger.info(f"Semantic View DDL YAML saved to {ddl_yaml_path}")
                except Exception as ex:
                    logger.warning(f"Failed to save DDL YAML: {ex}")
                
                logger.info(
                    "Semantic View deployed successfully in %.2fs",
                    time.perf_counter() - deploy_started_at,
                )
                
            finally:
                if owns_conn:
                    conn.close()
                
            return True
            
        except Exception as e:
            logger.error(f"Deployment failed: {e}")
            raise e
    
    def _ensure_source_tables_exist(self, cursor, sml: SMLModel) -> None:
        """
        Check if source tables exist in Snowflake, create them if missing.
        Also verify column structure matches and handle discrepancies:
        - Missing columns: recreate table
        - Extra columns: drop them using ALTER TABLE DROP COLUMN (preserves data)
        Tables are created with structure based on SML column definitions.

        Uses ``_verified_tables`` cache (P2b) to skip redundant SHOW TABLES
        queries for source tables already confirmed by a prior model deploy
        within the same session.
        """
        # Collect which datasets actually need checking (skip cached ones)
        datasets_to_check = []
        for dataset in sml.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            if safe_table_name in self._verified_tables:
                logger.debug(f"Table '{safe_table_name}' already verified this session — skipping")
                continue
            datasets_to_check.append(dataset)

        if not datasets_to_check:
            logger.debug("All source tables already verified — skipping SHOW TABLES")
            return

        # Only query Snowflake if we have datasets to check
        self._execute_sql(cursor, f"SHOW TABLES IN SCHEMA {self.config.schema_name}", context="SHOW TABLES")
        existing_tables = {row[1].upper() for row in cursor.fetchall()}
        
        # Get list of existing views (to avoid collision)
        self._execute_sql(cursor, f"SHOW VIEWS IN SCHEMA {self.config.schema_name}", context="SHOW VIEWS")
        existing_views = {row[1].upper() for row in cursor.fetchall()}
        
        all_existing = existing_tables | existing_views
        
        # Check each dataset's source table
        for dataset in datasets_to_check:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            quoted_table = f'"{safe_table_name}"'
            
            needs_creation = False
            
            if safe_table_name not in all_existing:
                needs_creation = True
                logger.info(f"Source table '{safe_table_name}' not found, creating...")
            else:
                # Table exists - verify columns match
                if self.sf_behavior.validate_column_schema:
                    missing_columns, extra_columns = self._verify_table_columns(cursor, safe_table_name, dataset)
                else:
                    missing_columns = set()
                    extra_columns = set()
                
                # Use EVOLVE (additive) approach for shared source tables
                if missing_columns:
                    logger.info(f"Table '{safe_table_name}' missing columns: {missing_columns}. Adding them...")
                    physical_cols = self._collect_physical_source_columns(dataset)
                    for col_name in missing_columns:
                        # Find canonical source column from the resolved physical map
                        orig_col = physical_cols.get(col_name)
                        if orig_col:
                            type_map = {
                                "STRING": "VARCHAR(500)", "INTEGER": "INTEGER", "FLOAT": "FLOAT",
                                "DECIMAL": "DECIMAL(18,2)", "BOOLEAN": "BOOLEAN", "DATETIME": "TIMESTAMP_NTZ",
                                "DATE": "DATE", "BINARY": "BINARY",
                            }
                            sf_type = type_map.get(orig_col.data_type.value, "VARCHAR(500)")
                            try:
                                self._execute_sql(
                                    cursor,
                                    f'ALTER TABLE {self.config.schema_name}.{quoted_table} ADD COLUMN "{col_name}" {sf_type}',
                                    context=f"ALTER TABLE ADD COLUMN {safe_table_name}.{col_name}",
                                )
                            except Exception as e:
                                logger.warning(f"Could not add column {col_name} to {safe_table_name}: {e}")
                                # Fallback: if ALTER fails (e.g. constraints), we might need recreation
                                # but for now we try to stay additive.
                
                # Skip dropping extra columns by default to allow sharing tables across models
                # self._drop_extra_columns(cursor, safe_table_name, extra_columns, dataset=dataset)

            
            if needs_creation:
                # Generate CREATE TABLE DDL
                if source_table.upper() == "DIM_DATE":
                    create_ddl = self._generate_date_dim_ddl(source_table)
                else:
                    create_ddl = self._generate_create_table_ddl(dataset, source_table)
                
                try:
                    self._execute_sql(cursor, create_ddl, context=f"CREATE TABLE {safe_table_name}")
                    logger.info(f"Created table: {safe_table_name}")

                    # Belt-and-suspenders: after CREATE TABLE IF NOT EXISTS
                    # the table may have already existed (created by a prior
                    # model's deployment or an external process) with a
                    # different column set. Verify columns match and
                    # rebuild if they don't.  This prevents the subsequent
                    # INSERT from hitting "invalid identifier" errors.
                    if source_table.upper() != "DIM_DATE":
                        missing_post, _ = self._verify_table_columns(
                            cursor, safe_table_name, dataset
                        )
                        if missing_post:
                            physical_cols = self._collect_physical_source_columns(dataset)
                            for col_name in missing_post:
                                # Find canonical source column from the resolved physical map
                                orig_col = physical_cols.get(col_name)
                                if orig_col:
                                    type_map = {
                                        "STRING": "VARCHAR(500)", "INTEGER": "INTEGER", "FLOAT": "FLOAT",
                                        "DECIMAL": "DECIMAL(18,2)", "BOOLEAN": "BOOLEAN", "DATETIME": "TIMESTAMP_NTZ",
                                        "DATE": "DATE", "BINARY": "BINARY",
                                    }
                                    sf_type = type_map.get(orig_col.data_type.value, "VARCHAR(500)")
                                    self._execute_sql(
                                        cursor,
                                        f'ALTER TABLE {self.config.schema_name}.{quoted_table} ADD COLUMN "{col_name}" {sf_type}',
                                        context=f"ALTER TABLE ADD COLUMN {safe_table_name}.{col_name}",
                                    )

                    
                    if source_table.upper() == "DIM_DATE":
                         logger.info("Populated DIM_DATE with generated data")
                    else:
                        # Insert sample data if this is an imported model
                        sample_insert = self._generate_sample_insert(dataset, source_table)
                        if sample_insert:
                            self._execute_sql(cursor, sample_insert, context=f"INSERT SAMPLE ROWS {safe_table_name}")
                            logger.info(f"Inserted sample data into: {safe_table_name}")
                        
                except Exception as e:
                    logger.error(
                        f"Failed to create required source table '{safe_table_name}': {e}. "
                        f"Downstream semantic view DDL will fail."
                    )
                    raise ConnectorError(
                        f"Cannot create source table '{safe_table_name}' in "
                        f"{self.config.database}.{self.config.schema_name}: {e}"
                    ) from e

            # Mark table as verified for this session (P2b cache)
            self._verified_tables.add(safe_table_name)
    
    def _verify_table_columns(self, cursor, table_name: str, dataset: SMLDataset) -> tuple:
        """
        Verify that a table's columns match the expected SML columns.
        
        Returns:
            tuple: (missing_columns: set, extra_columns: set)
                - missing_columns: Set of column names that are expected in SML but missing in Snowflake
                - extra_columns: Set of column names that exist in Snowflake but not in SML
        """
        try:
            self._execute_sql(cursor, f'DESC TABLE {self.config.schema_name}."{table_name}"', context=f"DESC TABLE {table_name}")
            existing_cols = {row[0] for row in cursor.fetchall()}
            
            # Check if model expects columns that don't exist
            expected_cols = set(self._collect_physical_source_columns(dataset).keys())
            
            missing = expected_cols - existing_cols
            if missing:
                logger.debug(f"Table {table_name} missing columns: {missing}")
            
            internal_cols = {'METADATA$ROW_ID', 'METADATA$IS_DELETED', 'METADATA$FILE_NAME', 
                           'METADATA$START_SCAN_TIME', 'METADATA$ACTION', 'METADATA$ROW_VERSION'}
            extra_cols = existing_cols - expected_cols - internal_cols
            
            return (missing, extra_cols)
        except Exception as e:
            logger.warning(f"Could not verify table {table_name}: {e}")
            # If table doesn't exist or error occurs, return all columns as missing
            all_expected = set(self._collect_physical_source_columns(dataset).keys())
            return (all_expected, set())


    
    def _drop_extra_columns(self, cursor, table_name: str, extra_columns: set,
                            dataset: Optional[SMLDataset] = None) -> None:
        """
        Drop extra columns from a table that are not in the SML model.
        
        When the DDL strategy is 'idempotent' (Mandate 2) and the guard
        triggers (would drop all columns), automatically recreates the
        table using CREATE OR REPLACE instead of silently returning.
        
        Args:
            cursor: Snowflake cursor
            table_name: Name of the table
            extra_columns: Set of column names to drop (uppercase)
            dataset: Optional SMLDataset for full recreation when needed
        """
        if not extra_columns:
            return
        
        # Guard: Snowflake forbids dropping ALL columns from a table.
        try:
            self._execute_sql(cursor, f'DESC TABLE {self.config.schema_name}."{table_name}"', context=f"DESC TABLE {table_name}")
            total_cols = {row[0] for row in cursor.fetchall()}
            if extra_columns >= total_cols:
                # Mandate 2: Idempotent DDL — auto-recreate instead of silent return
                if self.sf_behavior.ddl_strategy.value == "idempotent" and dataset:
                    logger.info(
                        f"Idempotent DDL: recreating {table_name} via CREATE OR REPLACE "
                        f"(would drop all {len(total_cols)} columns)"
                    )
                    create_ddl = self._generate_create_or_replace_table_ddl(dataset, table_name)
                    try:
                        self._execute_sql(cursor, create_ddl, context=f"CREATE OR REPLACE TABLE {table_name}")
                        logger.info(f"Successfully recreated table: {table_name}")
                    except Exception as e:
                        logger.error(f"Failed to recreate table {table_name}: {e}")
                    return
                else:
                    logger.warning(
                        f"Skipping column drops for {table_name}: would drop "
                        f"all {len(total_cols)} columns. Table needs full recreation."
                    )
                    return
        except Exception as e:
            logger.warning(f"Could not check column count for {table_name}: {e}")
        
        for col_name in extra_columns:
            try:
                ddl = f'ALTER TABLE {self.config.schema_name}."{table_name}" DROP COLUMN "{col_name}"'
                logger.info(f"Dropping extra column: {table_name}.{col_name}")
                self._execute_sql(cursor, ddl, context=f"DROP COLUMN {table_name}.{col_name}")
                logger.info(f"Successfully dropped column: {col_name}")
            except Exception as e:
                logger.warning(f"Could not drop column {col_name} from {table_name}: {e}")

    def _generate_create_or_replace_table_ddl(self, dataset: SMLDataset, table_name: str) -> str:
        """Generate CREATE OR REPLACE TABLE DDL for idempotent deployment (Mandate 2).

        This bypasses Snowflake's restrictive metadata rules regarding
        single-column drops by atomically replacing the entire table definition.
        """
        type_map = {
            "STRING": "VARCHAR(500)",
            "INTEGER": "INTEGER",
            "FLOAT": "FLOAT",
            "DECIMAL": "DECIMAL(18,2)",
            "BOOLEAN": "BOOLEAN",
            "DATETIME": "TIMESTAMP_NTZ",
            "DATE": "DATE",
            "BINARY": "BINARY",
        }

        safe_table = self._safe_table_name(table_name)
        schema = self.config.schema_name

        col_defs = []
        for col_name, col in self._collect_physical_source_columns(dataset).items():
            sf_type = type_map.get(col.data_type.value, "VARCHAR(500)")
            col_defs.append(f'    "{col_name}" {sf_type}')

        if not col_defs:
            col_defs.append('    "ID" VARCHAR')

        cols_block = ",\n".join(col_defs)
        return f'CREATE OR REPLACE TABLE {schema}."{safe_table}" (\n{cols_block}\n);'
    
    def _generate_date_dim_ddl(self, table_name: str) -> str:
        """
        Generate DDL for a Date Dimension using Snowflake Generator.
        Creates 20 years of data (10 past, 10 future).
        """
        safe_table = self._safe_table_name(table_name)
        schema = self.config.schema_name
        
        return f"""
        CREATE TABLE IF NOT EXISTS {schema}."{safe_table}" AS
        SELECT
          DATEADD(DAY, SEQ4(), DATEADD(YEAR, -10, CURRENT_DATE())) AS DATE,
          YEAR(DATE) AS YEAR,
          QUARTER(DATE) AS QUARTER,
          MONTH(DATE) AS MONTH,
          MONTHNAME(DATE) AS MONTHNAME,
          DAYOFWEEK(DATE) AS DAYOFWEEK,
          DAYNAME(DATE) AS DAYNAME
        FROM TABLE(GENERATOR(ROWCOUNT => 7300));
        """

    def _generate_create_table_ddl(self, dataset: SMLDataset, table_name: str) -> str:
        """Generate CREATE TABLE DDL from SML dataset definition."""
        # Data type mapping from SML/TMSL to Snowflake
        type_map = {
            "STRING": "VARCHAR(500)",
            "INTEGER": "INTEGER",
            "FLOAT": "FLOAT",
            "DECIMAL": "DECIMAL(18,2)",
            "BOOLEAN": "BOOLEAN",
            "DATETIME": "TIMESTAMP_NTZ",
            "DATE": "DATE",
            "BINARY": "BINARY"
        }
        
        col_defs = []
        for safe_name, col in self._collect_physical_source_columns(dataset).items():
            sf_type = type_map.get(col.data_type.value, "VARCHAR(500)")
            col_defs.append(f'    "{safe_name}" {sf_type}')
        
        if not col_defs:
            # Fallback: create with a single ID column
            col_defs.append("    ID INTEGER")
        
        safe_table = self._safe_table_name(table_name)
        quoted_table = f'"{safe_table}"'
        ddl = f"CREATE TABLE IF NOT EXISTS {self.config.schema_name}.{quoted_table} (\n"
        ddl += ",\n".join(col_defs)
        ddl += "\n);"
        
        return ddl

    def _collect_physical_source_columns(self, dataset: SMLDataset) -> dict[str, Any]:
        """Return ordered map of physical Snowflake column name -> SML column.

        All valid semantic columns are preserved. When multiple semantic columns
        normalize to the same Snowflake identifier, deterministic suffixes are
        appended (``_1``, ``_2``, ...) so deployment remains lossless.
        """
        valid_columns: list[Any] = []
        base_totals: dict[str, int] = {}
        for col in dataset.columns:
            col_name = col.unique_name

            if col_name.startswith("RowNumber") or col_name.startswith("_"):
                continue

            source_expr = getattr(col, 'source_expression', None)
            if source_expr and not self._is_physical_source_column(source_expr):
                logger.debug(
                    "Skipping calculated column '%s' from physical source columns",
                    col_name,
                )
                continue

            valid_columns.append(col)
            safe_base = self._sanitize_col_name(col_name)
            base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

        selected: dict[str, Any] = {}
        base_seen: dict[str, int] = {}
        signature_seen: dict[str, int] = {}
        namespace_key = self._duplicate_namespace_key()
        dataset_key = self._sanitize_alias(dataset.source_table or dataset.unique_name)
        for col in valid_columns:
            safe_base = self._sanitize_col_name(col.unique_name)
            next_idx = base_seen.get(safe_base, 0) + 1
            base_seen[safe_base] = next_idx

            if base_totals.get(safe_base, 0) > 1:
                signature_seed = self._build_duplicate_signature_seed(
                    source_name=col.unique_name,
                    source_expression=getattr(col, "source_expression", None),
                    data_type=str(getattr(col, "data_type", "")),
                )
                sig_idx = signature_seen.get(signature_seed, 0) + 1
                signature_seen[signature_seed] = sig_idx
                source_signature = f"{signature_seed}::occ{sig_idx}"
                preferred_name = f"{safe_base}_{next_idx}"
                safe_name = self._resolve_persistent_duplicate_name(
                    scope_type="column",
                    namespace_key=namespace_key,
                    dataset_key=dataset_key,
                    normalized_base=safe_base,
                    source_name=col.unique_name,
                    source_signature=source_signature,
                    preferred_name=preferred_name,
                )
            else:
                safe_name = safe_base

            while safe_name in selected:
                next_idx += 1
                base_seen[safe_base] = next_idx
                safe_name = f"{safe_base}_{next_idx}"

            if base_totals.get(safe_base, 0) > 1:
                logger.warning(
                    "Resolved physical column collision on table '%s': '%s' normalized to '%s'; using '%s'",
                    dataset.source_table or dataset.unique_name,
                    col.unique_name,
                    safe_base,
                    safe_name,
                )

            selected[safe_name] = col

        return selected

    def _resolve_physical_column_name(self, dataset: SMLDataset, raw_col_name: str) -> str:
        """Resolve semantic/raw column name to canonical physical column name."""
        physical_cols = self._collect_physical_source_columns(dataset)

        for phys_name, col in physical_cols.items():
            if col.unique_name == raw_col_name:
                return phys_name

        base = self._sanitize_col_name(raw_col_name)
        if base in physical_cols:
            return base

        for phys_name, col in physical_cols.items():
            if self._sanitize_col_name(col.unique_name) == base:
                return phys_name

        return base

    def generate_ctas_sql(
        self,
        table_name: str,
        columns: list[dict[str, str]],
        schema_name: Optional[str] = None,
        source_types: Optional[dict[str, str]] = None,
    ) -> str:
        """Generate CTAS SQL that applies safe datatype conversions.

        Args:
            table_name: Physical table name (unqualified).
            columns: List of {"name": <col_name>, "type": <normalized_type>}.
            schema_name: Optional schema override.
            source_types: Optional mapping of column name -> Snowflake source DATA_TYPE.
        """
        if not columns:
            raise ValueError("No columns inferred")

        varchar_types = {"VARCHAR", "STRING", "TEXT", "UNKNOWN", "VARIANT"}
        if all((c.get("type", "").upper() in varchar_types) for c in columns):
            logger.warning(
                "All columns are VARCHAR for %s. Generating pass-through CTAS.",
                table_name,
            )

        select_parts: list[str] = []
        source_types = source_types or {}
        for col in columns:
            name = col["name"]
            dtype = col["type"].upper()
            quoted_name = f'"{name}"'
            source_type = (source_types.get(name) or source_types.get(name.upper()) or "").upper()

            cast_expr = self._build_cast_expression(
                quoted_name=quoted_name,
                target_type=dtype,
                source_type=source_type,
                col_name=name,
            )
            select_parts.append(f"{cast_expr} AS {quoted_name}")

        select_sql = ",\n    ".join(select_parts)

        schema = schema_name or self.config.schema_name
        quoted_source = f'{schema}."{table_name}"'
        quoted_fixed = f'{schema}."{table_name}__FIXED"'
        return (
            f"CREATE OR REPLACE TABLE {quoted_fixed} AS\n"
            f"SELECT\n    {select_sql}\n"
            f"FROM {quoted_source};"
        )

    def _dataset_columns_for_ctas_sml(self, dataset: SMLDataset) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for col_name, col in self._collect_physical_source_columns(dataset).items():
            columns.append(
                {
                    "name": col_name,
                    "type": col.data_type.value.upper(),
                }
            )
        return columns

    @staticmethod
    def _infer_type_from_values(values: list[Any]) -> str:
        """Infer normalized type from sampled Python/Snowflake values."""
        if not values:
            return "VARCHAR"

        def _kind(v: Any) -> str:
            if isinstance(v, bool):
                return "BOOLEAN"
            if isinstance(v, int) and not isinstance(v, bool):
                return "INTEGER"
            if isinstance(v, (float, Decimal)):
                return "FLOAT"
            if isinstance(v, datetime):
                return "TIMESTAMP"
            if isinstance(v, date):
                return "DATE"

            s = str(v).strip()
            if not s:
                return "NULL"

            sl = s.lower()
            if sl in {"true", "false", "yes", "no", "y", "n", "t", "f"}:
                return "BOOLEAN"
            if re.fullmatch(r"[+-]?\d+", s):
                return "INTEGER"
            if re.fullmatch(r"[+-]?(?:\d+\.\d+|\d+\.\d*|\.\d+)", s):
                return "FLOAT"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return "DATE"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", s):
                return "TIMESTAMP"
            return "VARCHAR"

        kinds = {_kind(v) for v in values if v is not None}
        kinds.discard("NULL")
        if not kinds:
            return "VARCHAR"
        if kinds == {"BOOLEAN"}:
            return "BOOLEAN"
        if kinds == {"INTEGER"}:
            return "INTEGER"
        if kinds.issubset({"INTEGER", "FLOAT"}):
            return "FLOAT"
        if kinds == {"DATE"}:
            return "DATE"
        if kinds.issubset({"DATE", "TIMESTAMP"}):
            return "TIMESTAMP"
        return "VARCHAR"

    @staticmethod
    def _fallback_type_from_name(col_name: str) -> str:
        """Fallback inference for all-VARCHAR scenarios."""
        n = col_name.lower()
        tokens = [t for t in re.split(r"[^a-z0-9$]+", n.replace("_", " ")) if t]
        token_set = set(tokens)

        if any(k in n for k in ("timestamp", "datetime", "_ts")) or "ts" in token_set:
            return "TIMESTAMP"
        if "date" in n:
            return "DATE"
        # Duration-like columns (lead_time, processing_time, time_spent) should
        # default to numeric in fallback, not timestamp.
        if "time" in token_set:
            return "FLOAT"
        if any(k in n for k in ("amount", "price", "cost", "rate", "pct", "percent", "score", "revenue", "total", "qty", "quantity")):
            return "FLOAT"
        if any(k in n for k in ("is_", "has_", "flag", "active", "enabled", "deleted", "valid", "bool", "boolean")):
            return "BOOLEAN"
        # Use token-based numeric hints so words like ACCOUNT/COUNTRY do not
        # accidentally match "count".
        if any(k in token_set for k in ("count", "num", "year", "month", "day")):
            return "INTEGER"
        if any(k in token_set for k in ("id", "key")) or n.endswith("_id"):
            return "VARCHAR"
        return "VARCHAR"

    def _build_cast_expression(
        self,
        quoted_name: str,
        target_type: str,
        source_type: str,
        col_name: str,
    ) -> str:
        """Build a safe cast expression for CTAS based on source and target types."""
        t = (target_type or "").upper()
        s = (source_type or "").upper()

        numeric_targets = {"INTEGER", "FLOAT", "DECIMAL", "NUMBER"}
        timestamp_targets = {"TIMESTAMP", "DATETIME", "TIME", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}
        boolean_targets = {"BOOLEAN", "BOOL"}

        is_source_number = any(k in s for k in ("NUMBER", "DECIMAL", "NUMERIC", "INT", "FLOAT", "DOUBLE", "REAL"))
        is_source_timestamp = "TIMESTAMP" in s
        is_source_date = s.startswith("DATE")
        is_source_varchar = any(k in s for k in ("VARCHAR", "TEXT", "STRING", "CHAR"))
        is_source_boolean = "BOOLEAN" in s or s == "BOOL"

        # Same type -> no cast.
        if s == t:
            return quoted_name

        # Guard unsafe conversions that error in Snowflake.
        if is_source_number and (t == "DATE" or t in timestamp_targets):
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name
        if is_source_timestamp and t in numeric_targets:
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name
        if is_source_date and t in numeric_targets:
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name

        if t in boolean_targets:
            if is_source_boolean:
                return quoted_name
            if is_source_number:
                return f"IFF({quoted_name} IS NULL, NULL, IFF({quoted_name} = 0, FALSE, TRUE))"
            if is_source_varchar:
                return (
                    f"IFF({quoted_name} IS NULL, NULL, "
                    f"IFF(LOWER(TRIM({quoted_name})) IN ('true','1','t','yes','y'), TRUE, "
                    f"IFF(LOWER(TRIM({quoted_name})) IN ('false','0','f','no','n'), FALSE, NULL)))"
                )
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name

        # Safe conversions.
        if is_source_timestamp and t == "DATE":
            return f"CAST({quoted_name} AS DATE)"
        if is_source_date and t in timestamp_targets:
            return f"TO_TIMESTAMP({quoted_name})"
        if is_source_varchar and t == "DATE":
            return f"TRY_TO_DATE({quoted_name})"
        if is_source_varchar and t in timestamp_targets:
            return f"TRY_TO_TIMESTAMP({quoted_name})"

        if t in numeric_targets:
            if is_source_number:
                return quoted_name
            return f"TRY_TO_NUMBER({quoted_name})"
        if t == "DATE":
            if is_source_timestamp:
                return f"CAST({quoted_name} AS DATE)"
            if is_source_date:
                return quoted_name
            return f"TRY_TO_DATE({quoted_name})"
        if t in timestamp_targets:
            if is_source_timestamp:
                return quoted_name
            if is_source_date:
                return f"TO_TIMESTAMP({quoted_name})"
            return f"TRY_TO_TIMESTAMP({quoted_name})"

        return quoted_name

    def _get_source_column_types(self, cursor, safe_table_name: str) -> dict[str, str]:
        """Return Snowflake DATA_TYPE by column for the given physical table."""
        query = (
            "SELECT COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_CATALOG = %s AND TABLE_SCHEMA = %s AND TABLE_NAME = %s"
        )
        self._execute_sql(
            cursor,
            query,
            (
                self.config.database.upper(),
                self.config.schema_name.upper(),
                safe_table_name.upper(),
            ),
            context=f"INFORMATION_SCHEMA.COLUMNS {safe_table_name}",
        )
        rows = cursor.fetchall() or []
        col_types = {str(name).upper(): str(dtype).upper() for name, dtype in rows}
        logger.debug("Source column types for %s: %s", safe_table_name, col_types)
        return col_types

    def _infer_columns_from_table_samples(
        self,
        cursor,
        safe_table_name: str,
        columns: list[dict[str, str]],
        sample_limit: int = 200,
    ) -> tuple[list[dict[str, str]], list[str]]:
        """Resolve per-column types using model metadata first, sampling second."""
        if not columns:
            raise ValueError("No columns inferred")

        source_types = self._get_source_column_types(cursor, safe_table_name)
        source_names = list(source_types.keys())

        def _norm(name: str) -> str:
            return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())

        resolved_pairs: list[tuple[str, str]] = []
        fallback_logs: list[str] = []
        for c in columns:
            requested = str(c["name"])
            requested_upper = requested.upper()
            source_name = None

            if requested_upper in source_types:
                source_name = requested_upper
            else:
                requested_norm = _norm(requested)
                matches = [src for src in source_names if _norm(src) == requested_norm]
                if len(matches) == 1:
                    source_name = matches[0]

            if source_name:
                resolved_pairs.append((requested, source_name))
            else:
                fallback_logs.append(
                    f"Skipped non-physical column during sampling: {safe_table_name}.{requested}"
                )

        if not resolved_pairs:
            logger.warning(
                "Skipping sample type inference for %s: no model columns matched physical source columns",
                safe_table_name,
            )
            return [], fallback_logs

        def _q(ident: str) -> str:
            escaped = str(ident).replace('"', '""')
            return f'"{escaped}"'

        col_refs = ", ".join([f"{_q(src)} AS {_q(req)}" for req, src in resolved_pairs])
        sample_sql = (
            f'SELECT {col_refs} FROM {self.config.schema_name}."{safe_table_name}" '
            f'LIMIT {sample_limit}'
        )
        self._execute_sql(cursor, sample_sql, context=f"SAMPLE QUERY {safe_table_name}")
        rows = cursor.fetchall() or []
        logger.info("Sample rows fetched for %s: %s", safe_table_name, len(rows))

        inferred: list[dict[str, str]] = []
        varchar_types = {"VARCHAR", "STRING", "TEXT", "UNKNOWN", "VARIANT"}

        sampled_columns = [
            c for c in columns if any(req == c["name"] for req, _src in resolved_pairs)
        ]

        for idx, col in enumerate(sampled_columns):
            declared_type = self._normalize_declared_type(col.get("type", ""))
            values = [r[idx] for r in rows if len(r) > idx and r[idx] is not None]
            logger.debug("%s.%s sample values: %s", safe_table_name, col["name"], values[:5])
            sampled_type = self._infer_type_from_values(values)
            business_type = self._business_rule_type(safe_table_name, col["name"])

            if business_type is not None:
                resolved_type = business_type
            elif declared_type not in varchar_types:
                resolved_type = declared_type
                if sampled_type != "VARCHAR" and sampled_type != declared_type:
                    logger.warning(
                        "Type mismatch for %s.%s (model=%s sample=%s); using model type",
                        safe_table_name,
                        col["name"],
                        declared_type,
                        sampled_type,
                    )
            else:
                resolved_type = sampled_type

            inferred.append({"name": col["name"], "type": resolved_type})
            logger.info(
                "Resolved datatype: %s.%s -> %s (model=%s sample=%s sampled_rows=%s non_null=%s)",
                safe_table_name,
                col["name"],
                resolved_type,
                declared_type,
                sampled_type,
                len(rows),
                len(values),
            )

        logger.info("Inferred types for %s:", safe_table_name)
        for col in inferred:
            logger.info("- %s -> %s", col["name"], col["type"])

        if all(c["type"] == "VARCHAR" for c in inferred):
            logger.warning(
                "All sampled columns resolved to VARCHAR for %s. Applying fallback name-pattern inference.",
                safe_table_name,
            )
            for c in inferred:
                fb = self._fallback_type_from_name(c["name"])
                if fb != "VARCHAR":
                    fallback_logs.append(f"{c['name']}: VARCHAR -> {fb} (name-pattern fallback)")
                    c["type"] = fb

        for entry in fallback_logs:
            logger.info("Fallback decision: %s", entry)

        if all(c["type"] == "VARCHAR" for c in inferred):
            logger.warning(
                "%s: only text-like columns detected after sampling/fallback; keeping VARCHAR types",
                safe_table_name,
            )

        return inferred, fallback_logs

    @staticmethod
    def _business_rule_type(
        table_name: str,
        col_name: str,
    ) -> Optional[str]:
        """Business-rule layer for known Salesforce semantic fields."""
        t = (table_name or "").upper()
        c = (col_name or "").strip().upper()
        c_norm = re.sub(r"[^A-Z0-9]", "", c)

        if c_norm == "FIRMNESSOFFIRSTDELIVERYDATE":
            return "VARCHAR"
        if c == "DELETED":
            return "BOOLEAN"
        if "MODSTAMP" in c_norm:
            return "TIMESTAMP"
        if "VOLUME" in c_norm or "AMOUNT" in c_norm:
            return "FLOAT"
        if "DATE" in c_norm and not t.endswith("FIELDHISTORY"):
            return "DATE"

        return None

    @staticmethod
    def _normalize_declared_type(raw_type: str) -> str:
        """Normalize model-declared types to CTAS inference type family."""
        t = (raw_type or "").upper()
        mapping = {
            "INT64": "INTEGER",
            "INT": "INTEGER",
            "INTEGER": "INTEGER",
            "DOUBLE": "FLOAT",
            "FLOAT": "FLOAT",
            "DECIMAL": "DECIMAL",
            "NUMBER": "NUMBER",
            "BOOLEAN": "BOOLEAN",
            "BOOL": "BOOLEAN",
            "DATE": "DATE",
            "DATETIME": "TIMESTAMP",
            "TIMESTAMP": "TIMESTAMP",
            "TIMESTAMP_NTZ": "TIMESTAMP",
            "TIMESTAMP_LTZ": "TIMESTAMP",
            "TIMESTAMP_TZ": "TIMESTAMP",
            "STRING": "VARCHAR",
            "TEXT": "VARCHAR",
            "VARCHAR": "VARCHAR",
            "VARIANT": "VARCHAR",
        }
        return mapping.get(t, "VARCHAR")

    @staticmethod
    def _to_sml_datatype(inferred_type: str) -> DataType:
        mapping = {
            "INTEGER": DataType.INTEGER,
            "FLOAT": DataType.FLOAT,
            "DECIMAL": DataType.DECIMAL,
            "DATE": DataType.DATE,
            "TIMESTAMP": DataType.DATETIME,
            "BOOLEAN": DataType.BOOLEAN,
            "VARCHAR": DataType.STRING,
        }
        return mapping.get(inferred_type, DataType.STRING)

    @staticmethod
    def _to_osi_datatype(inferred_type: str):
        if OSIDataType is None:
            return None
        mapping = {
            "INTEGER": OSIDataType.INTEGER,
            "FLOAT": OSIDataType.FLOAT,
            "DECIMAL": OSIDataType.DECIMAL,
            "DATE": OSIDataType.DATE,
            "TIMESTAMP": OSIDataType.DATETIME,
            "BOOLEAN": OSIDataType.BOOLEAN,
            "VARCHAR": OSIDataType.STRING,
        }
        return mapping.get(inferred_type, OSIDataType.STRING)

    def _dataset_columns_for_ctas_osi(self, dataset: OSIDataset) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for col_name, col in self._collect_physical_source_columns_osi(dataset).items():
            columns.append(
                {
                    "name": col_name,
                    "type": col.data_type.value.upper(),
                }
            )
        return columns

    def _apply_inferred_types_ctas_sml(self, cursor, sml: SMLModel) -> None:
        """Apply inferred datatypes to physical SML source tables via CTAS + SWAP."""
        for dataset in sml.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            full_table = f'{self.config.schema_name}."{safe_table_name}"'
            fixed_table = f'{self.config.schema_name}."{safe_table_name}__FIXED"'
            base_columns = self._dataset_columns_for_ctas_sml(dataset)
            if not base_columns:
                logger.info(
                    "Skipping CTAS type inference for dataset '%s': no physical columns inferred",
                    dataset.unique_name,
                )
                continue

            columns, _fallbacks = self._infer_columns_from_table_samples(
                cursor,
                safe_table_name,
                base_columns,
            )

            by_name = {c["name"]: c["type"] for c in columns}
            for col in dataset.columns:
                resolved = self._resolve_physical_column_name(dataset, col.unique_name)
                if resolved in by_name:
                    col.data_type = self._to_sml_datatype(by_name[resolved])

            if all(c["type"] == "VARCHAR" for c in columns):
                logger.warning(
                    "Skipping CTAS for %s: all inferred types are VARCHAR (valid text table)",
                    safe_table_name,
                )
                continue

            source_types = self._get_source_column_types(cursor, safe_table_name)
            ctas_sql = self.generate_ctas_sql(
                safe_table_name,
                columns,
                self.config.schema_name,
                source_types=source_types,
            )
            logger.info(f"Applying inferred datatypes via CTAS for table: {safe_table_name}")
            logger.debug(f"Generated CTAS SQL:\n{ctas_sql}")
            self._execute_sql(cursor, ctas_sql, context=f"CTAS {safe_table_name}")
            self._execute_sql(cursor, f"ALTER TABLE {full_table} SWAP WITH {fixed_table}", context=f"SWAP TABLE {safe_table_name}")
            logger.info("Table swapped successfully: %s", safe_table_name)
            self._execute_sql(cursor, f"DROP TABLE IF EXISTS {fixed_table}", context=f"DROP TABLE {safe_table_name}__FIXED")

    def _apply_inferred_types_ctas_osi(self, cursor, osi: OSIModel) -> None:
        """Apply inferred datatypes to physical OSI source tables via CTAS + SWAP."""
        for dataset in osi.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            full_table = f'{self.config.schema_name}."{safe_table_name}"'
            fixed_table = f'{self.config.schema_name}."{safe_table_name}__FIXED"'
            physical_cols = self._collect_physical_source_columns_osi(dataset)
            base_columns = self._dataset_columns_for_ctas_osi(dataset)
            if not base_columns:
                logger.info(
                    "Skipping CTAS type inference for dataset '%s': no physical columns inferred",
                    dataset.unique_name,
                )
                continue

            columns, _fallbacks = self._infer_columns_from_table_samples(
                cursor,
                safe_table_name,
                base_columns,
            )

            by_name = {c["name"]: c["type"] for c in columns}
            for phys_name, col in physical_cols.items():
                if phys_name in by_name:
                    mapped = self._to_osi_datatype(by_name[phys_name])
                    if mapped is not None:
                        col.data_type = mapped

            if all(c["type"] == "VARCHAR" for c in columns):
                logger.warning(
                    "Skipping CTAS for %s: all inferred types are VARCHAR (valid text table)",
                    safe_table_name,
                )
                continue

            source_types = self._get_source_column_types(cursor, safe_table_name)
            ctas_sql = self.generate_ctas_sql(
                safe_table_name,
                columns,
                self.config.schema_name,
                source_types=source_types,
            )
            logger.info(f"Applying inferred datatypes via CTAS for table: {safe_table_name}")
            logger.debug(f"Generated CTAS SQL:\n{ctas_sql}")
            self._execute_sql(cursor, ctas_sql, context=f"CTAS {safe_table_name}")
            self._execute_sql(cursor, f"ALTER TABLE {full_table} SWAP WITH {fixed_table}", context=f"SWAP TABLE {safe_table_name}")
            logger.info("Table swapped successfully: %s", safe_table_name)
            self._execute_sql(cursor, f"DROP TABLE IF EXISTS {fixed_table}", context=f"DROP TABLE {safe_table_name}__FIXED")

    def _collect_physical_source_columns_osi(self, dataset: OSIDataset) -> dict[str, Any]:
        """Return ordered map of physical Snowflake column name -> OSI column.

        Uses the same collision policy as SML physical columns: keep all by
        suffixing repeated sanitized names with ``_1``, ``_2``, ...
        """
        valid_columns: list[Any] = []
        base_totals: dict[str, int] = {}
        for col in dataset.columns:
            col_name = col.unique_name
            if col_name.startswith("RowNumber") or col_name.startswith("_"):
                continue
            source_expr = getattr(col, "source_expression", None)
            if source_expr and not self._is_physical_source_column(source_expr):
                continue

            valid_columns.append(col)
            safe_base = self._sanitize_col_name(col_name)
            base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

        selected: dict[str, Any] = {}
        base_seen: dict[str, int] = {}
        signature_seen: dict[str, int] = {}
        namespace_key = self._duplicate_namespace_key()
        dataset_key = self._sanitize_alias(dataset.source_table or dataset.unique_name)
        for col in valid_columns:
            safe_base = self._sanitize_col_name(col.unique_name)
            next_idx = base_seen.get(safe_base, 0) + 1
            base_seen[safe_base] = next_idx

            if base_totals.get(safe_base, 0) == 1:
                safe_name = safe_base
            else:
                signature_seed = self._build_duplicate_signature_seed(
                    source_name=col.unique_name,
                    source_expression=getattr(col, "source_expression", None),
                    data_type=str(getattr(col, "data_type", "")),
                )
                sig_idx = signature_seen.get(signature_seed, 0) + 1
                signature_seen[signature_seed] = sig_idx
                source_signature = f"{signature_seed}::occ{sig_idx}"
                preferred_name = f"{safe_base}_{next_idx}"
                safe_name = self._resolve_persistent_duplicate_name(
                    scope_type="column",
                    namespace_key=namespace_key,
                    dataset_key=dataset_key,
                    normalized_base=safe_base,
                    source_name=col.unique_name,
                    source_signature=source_signature,
                    preferred_name=preferred_name,
                )
            while safe_name in selected:
                next_idx += 1
                base_seen[safe_base] = next_idx
                safe_name = f"{safe_base}_{next_idx}"

            selected[safe_name] = col

        return selected
    
    def _generate_sample_insert(self, dataset: SMLDataset, table_name: str) -> Optional[str]:
        """Generate INSERT statement with sample data for testing."""
        physical_cols = self._collect_physical_source_columns(dataset)
        if not physical_cols:
            return None

        columns = list(physical_cols.values())
        col_names = [f'"{col_name}"' for col_name in physical_cols.keys()]
        
        # Generate sample values based on data types
        sample_values = []
        for col in columns:
            dtype = col.data_type.value
            if dtype in ("INTEGER", "FLOAT", "DECIMAL"):
                sample_values.append("1")
            elif dtype == "BOOLEAN":
                sample_values.append("TRUE")
            elif dtype in ("DATETIME", "DATE"):
                sample_values.append("CURRENT_DATE()")
            else:
                sample_values.append(f"'Sample_{col.unique_name[:20]}'")
        
        safe_table = self._safe_table_name(table_name)
        cols_str = ", ".join(col_names)
        vals_str = ", ".join(sample_values)
        insert = f'INSERT INTO {self.config.schema_name}."{safe_table}" ({cols_str})\n'
        insert += f"SELECT {vals_str}\n"
        insert += f'WHERE NOT EXISTS (SELECT 1 FROM {self.config.schema_name}."{safe_table}" LIMIT 1);'
        
        return insert

    def generate_ddls(self, sml: SMLModel) -> List[str]:
        """
        Generate all execution DDLs for the model.
        Returns a list of SQL statements:
        1. CREATE SEMANTIC VIEW (single unified view for the entire model)
        """
        if not sml.datasets:
            return []
        
        # Generate single Snowflake Semantic View for the entire model
        logger.info(
            "generate_ddls: building semantic view for model=%s datasets=%s metrics=%s",
            sml.unique_name or sml.label or "<unnamed_sml_model>",
            len(getattr(sml, "datasets", []) or []),
            len(getattr(sml, "metrics", []) or []),
        )
        semantic_ddl = self._generate_semantic_view(sml)
        
        return [semantic_ddl]

    def _guard_relationship_clause(
        self,
        model_name: str,
        relationships: list[Any],
        semantic_ddl: str,
        *,
        fail_on_missing: bool,
    ) -> None:
        """Detect and block relationship loss between model and emitted DDL."""
        active_relationships = 0
        for rel in relationships or []:
            if not getattr(rel, "is_active", True):
                continue
            if (
                getattr(rel, "from_dataset", None)
                and getattr(rel, "to_dataset", None)
                and (getattr(rel, "from_columns", None) or [])
                and (getattr(rel, "to_columns", None) or [])
            ):
                active_relationships += 1

        if active_relationships == 0:
            return

        has_relationship_clause = bool(
            re.search(r"\bRELATIONSHIPS\s*\(", semantic_ddl or "", re.IGNORECASE)
        )
        if has_relationship_clause:
            return

        msg = (
            f"Model '{model_name}' has {active_relationships} active relationship(s), "
            "but generated semantic-view DDL has no RELATIONSHIPS clause. "
            "Aborting deploy to prevent relationship loss in Snowflake."
        )
        if fail_on_missing:
            logger.error(msg)
            raise ValueError(msg)
        logger.warning(msg)
    
    def _generate_semantic_view(self, sml: SMLModel) -> str:
        """
        Generate proper Snowflake Semantic View DDL.
        
        Valid Syntax Structure:
        CREATE OR REPLACE SEMANTIC VIEW <name>
        TABLES (
            <alias> AS <physical_table> PRIMARY KEY (<cols>),
            ...
        )
        RELATIONSHIPS (
            <alias_from> (<fk_cols>) REFERENCES <alias_to> (<ref_cols>),
            ...
        )
        DIMENSIONS (
            <alias>.<semantic_name> AS <alias>.<col>,
            ...
        )
        MEASURES (
            <alias>.<measure_name> AS <expr>,
            ...
        )
        """
        import re as _re

        # One-time safety migration: Snowflake semantic identifiers cannot
        # reliably start with digits in Horizon object explorer parsing.
        # Normalize model identifiers before DDL assembly.
        self._migrate_numeric_leading_identifiers(sml)
        self._precompute_duplicate_mappings_for_sml(sml)

        view_name = self._get_safe_object_name(sml.unique_name or sml.label)
        model_name = sml.unique_name or sml.label
        suffix = self.behavior.semantic_model.view_suffix or "_SEMANTIC"
        
        # Ensure suffix is consistently uppercase and doesn't get applied twice
        suffix_upper = suffix.upper() if suffix else "_SEMANTIC"
        if not suffix_upper.startswith("_"):
            suffix_upper = "_" + suffix_upper
        
        # Prevent duplicate suffixes
        if view_name.upper().endswith(suffix_upper):
            safe_view_name = view_name
        else:
            safe_view_name = view_name + suffix_upper
        
        full_view_name = (
            f'"{self.config.database}"."{self.config.schema_name}"'
            f'."{safe_view_name}"'
        )
        
        lines = [f"CREATE OR REPLACE SEMANTIC VIEW {full_view_name}"]
        definitions = []
        
        # =====================================================================
        # TABLES clause
        # =====================================================================
        tables_lines = []
        dataset_aliases = {}
        used_table_aliases: set[str] = set()
        relationship_target_alias: dict[tuple[str, str], str] = {}
        
        # Build a map of which columns each table uses as PK based on relationships
        # A table's PK should be the column(s) referenced by FKs pointing TO it
        relationship_pk_map = {}  # dataset_name -> list of to_columns
        for rel in sml.relationships:
            if rel.is_active and rel.to_dataset and rel.to_columns:
                if rel.to_dataset not in relationship_pk_map:
                    relationship_pk_map[rel.to_dataset] = []
                for col in rel.to_columns:
                    if col not in relationship_pk_map[rel.to_dataset]:
                        relationship_pk_map[rel.to_dataset].append(col)
        metric_counts_by_dataset: dict[str, int] = {}
        related_datasets: set[str] = set()
        for metric in sml.metrics:
            ds_name = getattr(metric, "dataset", None)
            if ds_name:
                metric_counts_by_dataset[ds_name] = metric_counts_by_dataset.get(ds_name, 0) + 1
        for rel in sml.relationships:
            if getattr(rel, "is_active", True):
                if getattr(rel, "from_dataset", None):
                    related_datasets.add(rel.from_dataset)
                if getattr(rel, "to_dataset", None):
                    related_datasets.add(rel.to_dataset)
        declared_pk_by_alias: dict[str, list[str]] = {}
        # Build sanitized physical-column lookup per dataset.
        # Used by TABLES (PK validation), RELATIONSHIPS, DIMENSIONS and METRICS
        # to ensure we don't reference non-existent physical columns.
        dataset_col_lookup: dict[str, set[str]] = {}
        dataset_by_name: dict[str, SMLDataset] = {d.unique_name: d for d in sml.datasets}
        for dataset in sml.datasets:
            dataset_col_lookup[dataset.unique_name] = set(
                self._collect_physical_source_columns(dataset).keys()
            )

        for dataset in sml.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table = self._safe_table_name(source_table)
            full_table = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'
            
            alias = self._resolve_unique_table_alias(
                self._sanitize_alias(dataset.unique_name),
                used_table_aliases,
            )
            dataset_aliases[dataset.unique_name] = alias
            
            # Determine PK columns:
            # 1. If this table is a relationship target, use the to_columns as PK
            # 2. Otherwise, use the first is_key column (singular, to avoid composite PKs)
            # 3. Fallback to first column
            #
            # Mandate 4: PK Resolution Mode
            #   STRICT  → abort if no explicit key found (relationship or is_key)
            #   PERMISSIVE → fall back to first column (legacy default)
            pk_resolution_mode = getattr(
                self.sf_behavior, "pk_resolution_mode", None
            )
            known_phys = dataset_col_lookup.get(dataset.unique_name, set())
            relationship_pk_cols: list[str] = []
            if dataset.unique_name in relationship_pk_map:
                # Preserve all relationships by creating additional aliases
                # for each distinct referenced PK column on this dataset.
                for rel_col in relationship_pk_map[dataset.unique_name]:
                    resolved = self._resolve_physical_column_name(dataset, rel_col)
                    if known_phys and resolved not in known_phys:
                        continue
                    if resolved not in relationship_pk_cols:
                        relationship_pk_cols.append(resolved)

            is_measure_only_dataset = (
                metric_counts_by_dataset.get(dataset.unique_name, 0) > 0
                and dataset.unique_name not in related_datasets
                and not any(getattr(c, "is_key", False) for c in dataset.columns)
            )

            if is_measure_only_dataset:
                pk_cols = []
            elif relationship_pk_cols:
                pk_cols = [f'"{relationship_pk_cols[0]}"']
            else:
                # Find first key column only (avoid composite PKs that don't match relationships)
                key_cols = [c for c in dataset.columns if c.is_key]
                if key_cols:
                    pk_cols = [f'"{self._resolve_physical_column_name(dataset, key_cols[0].unique_name)}"'
                              ]
                else:
                    if pk_resolution_mode and pk_resolution_mode.value == "strict":
                        logger.error(
                            f"PK resolution STRICT: dataset '{dataset.unique_name}' "
                            f"has no is_key column and no inbound relationship PK. "
                            f"Aborting semantic view generation."
                        )
                        raise ValueError(
                            f"No primary key found for dataset '{dataset.unique_name}' "
                            f"(pk_resolution_mode=strict). Mark a column as is_key or "
                            f"define a relationship pointing to this table."
                        )
                    # PERMISSIVE: fall back to first column
                    col_name = dataset.columns[0].unique_name if dataset.columns else "ID"
                    pk_cols = [f'"{self._sanitize_col_name(col_name)}"'
                              ]
            
            # Final Validation: Filter PK columns against physical columns
            # to prevent "invalid identifier" errors if a model marks a 
            # calculated column as a key.
            known_phys = dataset_col_lookup.get(dataset.unique_name, set())
            verified_pk = []
            for pk_quoted in pk_cols:
                pk_unquoted = pk_quoted.strip('"')
                if pk_unquoted in known_phys:
                    verified_pk.append(pk_quoted)
                else:
                    logger.warning(
                        f"Excluding PK column '{pk_unquoted}' from view "
                        f"'{view_name}': not a physical column in Snowflake."
                    )
            
            # If all PK columns were filtered out, fall back to FIRST physical column
            # to satisfy Snowflake's requirement for a Primary Key.
            if not verified_pk and known_phys:
                fallback_pk = sorted(list(known_phys))[0]
                logger.info(f"Using fallback PK '{fallback_pk}' for view '{view_name}'")
                verified_pk = [f'"{fallback_pk}"']

            # Snowflake REFERENCES must target the declared PK/unique key columns.
            # Keep relationship validation aligned to the exact TABLES PK emitted.
            if verified_pk:
                declared_pk_by_alias[alias] = [c.strip('"') for c in verified_pk]
                relationship_target_alias[(dataset.unique_name, verified_pk[0].strip('"').upper())] = alias
            
            pk_clause = f"PRIMARY KEY ({', '.join(verified_pk)})" if verified_pk else ""
            tables_lines.append(f'  {alias} AS {full_table} {pk_clause}')

            # Emit additional table aliases for extra relationship target keys
            # so Snowflake REFERENCES can point to a matching declared PK.
            for rel_pk in relationship_pk_cols[1:]:
                alias_seed = self._sanitize_alias(f"{dataset.unique_name}__BY_{rel_pk}")
                rel_alias = self._resolve_unique_table_alias(alias_seed, used_table_aliases)
                tables_lines.append(
                    f'  {rel_alias} AS {full_table} PRIMARY KEY ("{rel_pk}")'
                )
                declared_pk_by_alias[rel_alias] = [rel_pk]
                relationship_target_alias[(dataset.unique_name, rel_pk.upper())] = rel_alias
        
        if tables_lines:
            definitions.append("TABLES (\n" + ",\n".join(tables_lines) + "\n)")

        # =====================================================================
        # Build reverse alias lookup for expression rewriting.
        # Maps every plausible raw name variant to the sanitized alias so that
        # DAX-style cross-table references (e.g. Table.Column, L_Date.MonthIndex)
        # can be resolved correctly.
        # Uses IdentifierNormalizer for comprehensive mapping including
        # reserved-word-prefixed aliases (e.g. L_TABLE → L_TABLE).
        # =====================================================================
        from semabridge.utils.identifier_normalizer import IdentifierNormalizer
        _normalizer = IdentifierNormalizer(self._id)
        _alias_by_raw = _normalizer.build_alias_lookup(sml.datasets, dataset_aliases)


        # =====================================================================
        # RELATIONSHIPS clause — validate FK columns exist as physical columns
        # =====================================================================
        rel_lines = []
        for rel in sml.relationships:
            if not rel.is_active: continue
            
            from_alias = dataset_aliases.get(rel.from_dataset)
            to_alias = dataset_aliases.get(rel.to_dataset)
            
            # Guard: Skip relationship if aliases are unresolvable
            if not from_alias or not to_alias or not rel.from_columns:
                logger.warning(
                    f"Skipping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"Unresolvable aliases (from={from_alias}, to={to_alias}) or missing from_columns."
                )
                continue
            
            from_ds = dataset_by_name.get(rel.from_dataset)
            to_ds = dataset_by_name.get(rel.to_dataset)
            # Sanitize FK column back to underscores to match physical table
            from_col = self._resolve_physical_column_name(from_ds, rel.from_columns[0]) if from_ds else self._sanitize_col_name(rel.from_columns[0])
            # Sanitize referenced (PK) column on the target side
            to_col = (
                self._resolve_physical_column_name(to_ds, rel.to_columns[0])
                if (to_ds and rel.to_columns)
                else (self._sanitize_col_name(rel.to_columns[0]) if rel.to_columns else "")
            )
            
            # Guard: Skip if from_col is empty
            if not from_col:
                logger.warning(
                    f"Skipping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"from_col is empty after resolution."
                )
                continue
            
            # Validate FK column exists in the from-dataset's physical columns
            from_phys = dataset_col_lookup.get(rel.from_dataset, set())
            if from_phys and from_col not in from_phys:
                fallback_fk = sorted(from_phys)[0]
                logger.warning(
                    f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"FK column '{from_col}' is not physical in '{rel.from_dataset}'. "
                    f"Using '{fallback_fk}' to preserve relationship emission."
                )
                from_col = fallback_fk
            # Validate to_col is both a physical column AND the declared PK
            # for that dataset. Snowflake requires REFERENCES to point to a
            # primary or unique key — any mismatch causes a SQL compilation error.
            if to_col:
                to_phys = dataset_col_lookup.get(rel.to_dataset, set())
                mapped_to_alias = relationship_target_alias.get(
                    (rel.to_dataset, to_col.upper())
                )
                if mapped_to_alias:
                    to_alias = mapped_to_alias
                declared_pk_cols = declared_pk_by_alias.get(to_alias, [])
                # Case-insensitive check: sanitize both sides to uppercase
                to_phys_upper = {c.upper() for c in to_phys}
                if to_phys and to_col.upper() not in to_phys_upper:
                    if declared_pk_cols:
                        fallback_to = declared_pk_cols[0]
                    else:
                        fallback_to = sorted(to_phys)[0]
                    logger.warning(
                        f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                        f"referenced column '{to_col}' is not physical in '{rel.to_dataset}'. "
                        f"Using '{fallback_to}' to preserve relationship emission."
                    )
                    to_col = fallback_to
                    mapped_to_alias = relationship_target_alias.get(
                        (rel.to_dataset, to_col.upper())
                    )
                    if mapped_to_alias:
                        to_alias = mapped_to_alias
                    declared_pk_cols = declared_pk_by_alias.get(to_alias, declared_pk_cols)
                # Also check: to_col must be the PK declared in the TABLES clause.
                # Use the exact PK columns emitted in TABLES clause.
                if declared_pk_cols and to_col.upper() not in {c.upper() for c in declared_pk_cols}:
                    fallback_to = declared_pk_cols[0]
                    logger.warning(
                        f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                        f"referenced column '{to_col}' is not the declared PK {declared_pk_cols} "
                        f"for '{rel.to_dataset}'. Using '{fallback_to}' to preserve relationship emission."
                    )
                    to_col = fallback_to
                    mapped_to_alias = relationship_target_alias.get(
                        (rel.to_dataset, to_col.upper())
                    )
                    if mapped_to_alias:
                        to_alias = mapped_to_alias
            
            # Build REFERENCES clause with explicit target column
            ref_clause = f'{to_alias} ("{to_col}")' if to_col else to_alias
            rel_name = self._to_snowflake_relationship_name(getattr(rel, "unique_name", "") or "")
            from_ref = f'"{from_col}"'
            if rel_name:
                rel_lines.append(
                    f'  {rel_name} AS {from_alias} ({from_ref}) REFERENCES {ref_clause}'
                )
            else:
                rel_lines.append(
                    f'  {from_alias} ({from_ref}) REFERENCES {ref_clause}'
                )
        
        if rel_lines:
            definitions.append("RELATIONSHIPS (\n" + ",\n".join(rel_lines) + "\n)")

        # =====================================================================
        # DIMENSIONS clause (FR-01 & FR-02: Filter out measure candidates)
        # =====================================================================
        dims_lines = []
        added_dimensions = set()  # Track physical additions to avoid duplicates
        used_dimension_aliases: set[str] = set()
        
        # Collect all measure columns for exclusion
        measure_columns = {(m.dataset, m.source_column) for m in sml.metrics if m.source_column}
        
        # 1. Add explicitly defined dimensions
        for dim in sml.dimensions:
            for attr in dim.attributes:
                alias = dataset_aliases.get(attr.dataset)
                if not alias:
                    logger.warning(f"Alias not found for dataset '{attr.dataset}' - skipping dimension {attr.unique_name}")
                    continue

                # Cross-check: verify the backing column is a physical column.
                # Calculated columns (DAX expressions) don't exist in the
                # physical Snowflake table and cause "invalid identifier".
                # SMLAttribute uses 'dataset_column'; OSIAttribute uses 'source_column'.
                raw_col = (
                    getattr(attr, "dataset_column", None)
                    or getattr(attr, "source_column", None)
                    or attr.unique_name
                )
                dataset_obj = dataset_by_name.get(attr.dataset)
                if not dataset_obj:
                    logger.warning(f"Dataset not found for attribute '{attr.unique_name}' - skipping")
                    continue
                phys_col = self._resolve_physical_column_name(dataset_obj, raw_col)
                known_phys = dataset_col_lookup.get(attr.dataset, set())
                if known_phys and phys_col not in known_phys:
                    logger.debug(
                        f"Excluding dimension attribute '{attr.unique_name}' "
                        f"— column '{phys_col}' not in physical columns of "
                        f"'{attr.dataset}'"
                    )
                    continue
                    
                semantic_name = self._sanitize_semantic_name(attr.unique_name)
                dim_key = (alias, semantic_name, phys_col)
                
                if dim_key not in added_dimensions:
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic_name,
                        alias,
                        used_dimension_aliases,
                        attr.unique_name,
                    )
                    # Re-add quoting for semantic names to handle reserved words (KEY, COSTS, etc.)
                    dims_lines.append(
                        f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                    )
                    added_dimensions.add(dim_key)
                    
        # 2. Add raw attributes (excluding measure candidates)
        for dataset in sml.datasets:
            alias = dataset_aliases.get(dataset.unique_name)
            if not alias:
                logger.warning(f"Alias not found for dataset '{dataset.unique_name}' - skipping columns")
                continue
                
            for col in dataset.columns:
                # Skip internal columns
                if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                    continue
                
                # Skip calculated columns — these don't exist as physical
                # columns in Snowflake and cause "invalid identifier" errors
                source_expr = getattr(col, 'source_expression', None)
                if source_expr and not self._is_physical_source_column(source_expr):
                    logger.debug(
                        f"Excluding calculated column '{col.unique_name}' "
                        f"from DIMENSIONS"
                    )
                    continue
                
                semantic_name = self._sanitize_semantic_name(col.unique_name)
                # Physical column must use sanitized name (with underscores) to match Snowflake
                phys_col = self._resolve_physical_column_name(dataset, col.unique_name)
                dim_key = (alias, semantic_name, phys_col)
                
                # Skip if already added
                if dim_key in added_dimensions:
                    continue
                
                # FR-02: Skip if this column is marked as a measure candidate
                sync_all = self.behavior.semantic_model.sync_all_attributes
                if col.is_measure_candidate and not sync_all:
                    logger.debug(f"Excluding measure candidate '{col.unique_name}' from DIMENSIONS")
                    continue
                
                # Skip if this column is used as a metric source
                if (dataset.unique_name, col.unique_name) in measure_columns:
                    if dataset.is_fact:  # Only skip on fact tables
                        continue
                
                emitted_name = self._resolve_unique_dimension_alias(
                    semantic_name,
                    alias,
                    used_dimension_aliases,
                    col.unique_name,
                )
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                )
                added_dimensions.add(dim_key)

        # Ensure DIMENSIONS is not empty (Snowflake requires at least one dimension)
        if not dims_lines and tables_lines:
            first_ds = sml.datasets[0]
            alias = dataset_aliases.get(first_ds.unique_name)
            # Find a non-measure column
            known_phys = dataset_col_lookup.get(first_ds.unique_name, set())
            for col in first_ds.columns:
                phys = self._resolve_physical_column_name(first_ds, col.unique_name)
                if not col.is_measure_candidate and not col.unique_name.startswith("_") and phys in known_phys:
                    # Sanitize both sides of AS to ensure valid identifiers
                    semantic = self._sanitize_semantic_name(col.unique_name)
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic,
                        alias,
                        used_dimension_aliases,
                        col.unique_name,
                    )
                    dims_lines.append(
                        f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys, model_name=model_name)}'
                    )
                    break
            else:
                # Absolute fallback if no physical columns found (safest possible)
                for col in first_ds.columns:
                    phys = self._resolve_physical_column_name(first_ds, col.unique_name)
                    if phys in known_phys:
                        semantic = self._sanitize_semantic_name(col.unique_name)
                        emitted_name = self._resolve_unique_dimension_alias(
                            semantic,
                            alias,
                            used_dimension_aliases,
                            col.unique_name,
                        )
                        dims_lines.append(
                            f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys, model_name=model_name)}'
                        )
                        break
                else:
                    # If genuinely NO physical columns are known, fall back to the very first
                    # but this is a high-risk scenario that should have been caught by
                    # _ensure_source_tables_exist.
                    col = first_ds.columns[0]
                    semantic = self._sanitize_semantic_name(col.unique_name)
                    phys = self._resolve_physical_column_name(first_ds, col.unique_name)
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic,
                        alias,
                        used_dimension_aliases,
                        col.unique_name,
                    )
                    dims_lines.append(
                        f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys, model_name=model_name)}'
                    )

        if dims_lines:
            definitions.append("DIMENSIONS (\n" + ",\n".join(dims_lines) + "\n)")
            
        # =====================================================================
        # METRICS clause
        # =====================================================================
        metrics_lines = []
        used_metric_names: set[str] = set()
        skipped_metric_names: set[str] = set()
        expected_metrics: list[tuple[str, str, str]] = []
        # Filter out metrics with $ character (not supported in Snowflake semantic view)
        valid_metrics = [m for m in sml.metrics if "$" not in m.unique_name]
        if len(valid_metrics) < len(sml.metrics):
            skipped_count = len(sml.metrics) - len(valid_metrics)
            logger.warning(
                f"Skipping {skipped_count} metric(s) with '$' character "
                "from Snowflake semantic view (unsupported identifier)."
            )
        metric_name_set = {
            self._sanitize_alias(m.unique_name)
            for m in valid_metrics
        }
        all_physical_col_names: set[str] = set()
        for cols in dataset_col_lookup.values():
            all_physical_col_names.update(cols)
        emittable_metric_name_set = {
            self._sanitize_alias(m.unique_name)
            for m in valid_metrics
            if (m.source_column and m.aggregation) or m.sql_expression
        }
        metric_base_totals: dict[str, int] = {}
        for m in valid_metrics:
            base_alias = self._sanitize_alias(m.unique_name)
            metric_base_totals[base_alias] = metric_base_totals.get(base_alias, 0) + 1
        metric_base_seen: dict[str, int] = {}
        metric_signature_seen: dict[str, int] = {}
        metric_namespace = self._duplicate_namespace_key(sml.unique_name or sml.label)
        metric_by_unique_name = {m.unique_name: m for m in valid_metrics}

        for metric in valid_metrics:
            alias = dataset_aliases.get(metric.dataset)
            if not alias: continue
            metric_dataset_obj = dataset_by_name.get(metric.dataset)
            metric_base_alias = self._sanitize_alias(metric.unique_name)
            metric_seen_idx = metric_base_seen.get(metric_base_alias, 0) + 1
            metric_base_seen[metric_base_alias] = metric_seen_idx
            metric_alias_seed = (
                metric_base_alias
                if metric_base_totals.get(metric_base_alias, 0) == 1
                else f"{metric_base_alias}_{metric_seen_idx}"
            )
            if metric_base_totals.get(metric_base_alias, 0) > 1:
                metric_signature_seed = self._build_duplicate_signature_seed(
                    source_name=metric.unique_name,
                    source_expression=metric.sql_expression or metric.expression,
                    data_type=None,
                    aggregation=metric.aggregation.value if metric.aggregation else None,
                )
                sig_idx = metric_signature_seen.get(metric_signature_seed, 0) + 1
                metric_signature_seen[metric_signature_seed] = sig_idx
                metric_signature = f"{metric_signature_seed}::occ{sig_idx}"
                metric_alias_seed = self._resolve_persistent_duplicate_name(
                    scope_type="metric",
                    namespace_key=metric_namespace,
                    dataset_key=self._sanitize_alias(metric.dataset),
                    normalized_base=metric_base_alias,
                    source_name=metric.unique_name,
                    source_signature=metric_signature,
                    preferred_name=metric_alias_seed,
                )

            metric_name = self._resolve_unique_metric_alias(
                metric_alias_seed,
                used_metric_names,
                metric.unique_name,
            )
            expected_metrics.append((alias, metric_name, metric.unique_name))
            
            # Use source_column aggregation if available (safest for sanitization)
            if metric.source_column and metric.aggregation and (
                not metric.sql_expression or self._should_use_direct_metric_aggregation(metric)
            ):
                # Physical column must use sanitized name (underscores) to match Snowflake
                col_name = (
                    self._resolve_physical_column_name(metric_dataset_obj, metric.source_column)
                    if metric_dataset_obj else self._sanitize_col_name(metric.source_column)
                )
                agg = metric.aggregation.value.upper()

                owners = [
                    ds for ds, cols in dataset_col_lookup.items()
                    if self._resolve_column_name_for_dataset(cols, col_name)
                ]
                if owners:
                    preferred_owner = None
                    if self._is_client_data_model(model_name):
                        if metric.dataset in owners:
                            preferred_owner = metric.dataset
                        else:
                            logger.warning(
                                "Client Data: skipping ambiguous metric '%s' from dataset '%s' because source column '%s' is shared by %s",
                                metric.unique_name,
                                metric.dataset,
                                col_name,
                                sorted(owners),
                            )
                            continue
                    elif metric.dataset in owners and len(owners) > 1:
                        non_self = [o for o in owners if o != metric.dataset]
                        fact_owners = [
                            o for o in non_self
                            if getattr(dataset_by_name.get(o), "is_fact", False)
                        ]
                        preferred_owner = fact_owners[0] if fact_owners else non_self[0]
                    elif len(owners) == 1:
                        preferred_owner = owners[0]

                    if preferred_owner and preferred_owner != metric.dataset:
                        owner_alias = dataset_aliases.get(preferred_owner)
                        owner_col = self._resolve_column_name_for_dataset(
                            dataset_col_lookup.get(preferred_owner, set()),
                            col_name,
                        ) or col_name
                        if owner_alias:
                            if agg == "COUNT_DISTINCT":
                                expr = f'COUNT(DISTINCT {self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                            elif agg == "NONE":
                                expr = f'{self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)}'
                            else:
                                expr = f'{agg}({self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                            metrics_lines.append(f'  {alias}."{metric_name}" AS {expr}')
                            continue
                
                # Validate column exists in the physical table
                known_cols = dataset_col_lookup.get(metric.dataset, set())
                if col_name not in known_cols:
                    owners = [
                        ds for ds, cols in dataset_col_lookup.items()
                        if self._resolve_column_name_for_dataset(cols, col_name)
                    ]
                    if len(owners) == 1:
                        owner_ds = owners[0]
                        owner_alias = dataset_aliases.get(owner_ds)
                        owner_col = self._resolve_column_name_for_dataset(
                            dataset_col_lookup.get(owner_ds, set()),
                            col_name,
                        ) or col_name
                        if owner_alias:
                            if agg == "COUNT_DISTINCT":
                                expr = f'COUNT(DISTINCT {self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                            elif agg == "NONE":
                                expr = f'{self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)}'
                            else:
                                expr = f'{agg}({self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                            metrics_lines.append(f'  {alias}."{metric_name}" AS {expr}')
                            logger.info(
                                "Remapped metric '%s' source column '%s' from dataset '%s' to '%s.%s'",
                                metric.unique_name,
                                col_name,
                                metric.dataset,
                                owner_ds,
                                owner_col,
                            )
                            continue

                    llm_expr = self._try_llm_metric_fallback_expression(
                        metric=metric,
                        metric_name=metric_name,
                        table_alias=alias,
                        alias_by_raw=_alias_by_raw,
                        dataset_col_lookup=dataset_col_lookup,
                        dataset_aliases=dataset_aliases,
                        metric_name_set=metric_name_set,
                        all_physical_col_names=all_physical_col_names,
                        emittable_metric_name_set=emittable_metric_name_set,
                        skipped_metric_names=skipped_metric_names,
                    )
                    if llm_expr:
                        metrics_lines.append(f'  {alias}."{metric_name}" AS {llm_expr}')
                        continue
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': column "
                        f"'{col_name}' not in dataset '{metric.dataset}'"
                    )
                    continue

                if agg == "COUNT_DISTINCT":
                    expr = f'COUNT(DISTINCT {alias}."{col_name}")'
                elif agg == "NONE":
                    expr = f'{alias}."{col_name}"'
                else:
                    expr = f'{agg}({alias}."{col_name}")'
                    
                metrics_lines.append(f'  {alias}."{metric_name}" AS {expr}')
            
            # Fallback to expression if explicitly provided and not handled above
            elif metric.sql_expression:
                expr = metric.sql_expression
                # CRITICAL FIX: Strip markdown code blocks from SQL expression
                expr = self._sanitize_sql_markdown(expr)

                # CRITICAL SAFETY CHECK: Reject SELECT statements in metric expressions
                # These would cause "syntax error: unexpected SELECT" in METRICS clause
                if 'SELECT' in expr.upper():
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': "
                        f"sql_expression contains SELECT statement (invalid for METRICS clause). "
                        f"Expression: {expr[:80]}..."
                    )
                    continue

                # Check if this is a manual override measure (Tier 3/4)
                is_override = getattr(metric, 'complexity_tier', 0) >= 3

                if not is_override:
                    # Step A: Sanitize DAX-style [Column Name] → "COLUMN_NAME"
                    matches = _re.findall(r"\[(.+?)\]", expr)
                    for m in matches:
                        safe_m = self._sanitize_col_name(m)
                        expr = expr.replace(f"[{m}]", f'"{safe_m}"')

                    # Step B: Resolve cross-table references (TABLE.COLUMN
                    # or TABLE."COLUMN") via centralized dot-notation resolver
                    # (Module 2 — replaces inline regex closure).
                    expr = self._id.resolve_dot_notation(
                        expr, _alias_by_raw,
                        sanitize_col_fn=self._sanitize_col_name,
                    )

                    # Step C: Defence-in-depth — verify all TABLE.COL refs
                    # now use a valid alias from the TABLES clause.
                    valid_aliases = set(dataset_aliases.values())
                    invalid_table_refs = self._id.validate_table_refs(
                        expr, valid_aliases,
                    )
                    if invalid_table_refs:
                        logger.warning(
                            f"Metric '{metric.unique_name}': expression still "
                            f"references unknown table aliases {invalid_table_refs} "
                            f"after rewriting — skipping to avoid SQL compilation error"
                        )
                        continue

                    # Step C.5: CRITICAL VALIDATION — Verify TABLE.COLUMN cross-table refs
                    # actually reference columns that exist in that table's physical columns.
                    # Example: SALESFACT."SCORE" is invalid if SCORE is not in SALESFACT
                    # (it's in SENTIMENT instead).
                    alias_to_dataset = {}  # Map alias back to dataset name
                    for ds in sml.datasets:
                        alias_to_dataset[dataset_aliases[ds.unique_name]] = ds.unique_name
                    
                    # Find all TABLE.COLUMN patterns in the resolved expression
                    # CRITICAL: Must match both quoted and unquoted column references
                    # Quoted: SALESFACT."COL_NAME"
                    # Unquoted: SALESFACT.COL_NAME
                    table_col_refs = _re.findall(r'(\w+)\."([^"]+)"', expr)
                    table_col_refs_unquoted = _re.findall(
                        r'(\w+)\.([A-Za-z_][A-Za-z0-9_]*)(?!["\w])', expr
                    )
                    table_col_refs.extend(table_col_refs_unquoted)
                    
                    for table_alias, col_name in table_col_refs:
                        # Map alias back to dataset
                        ds_name = alias_to_dataset.get(table_alias)
                        if not ds_name:
                            logger.warning(
                                f"Metric '{metric.unique_name}': alias '{table_alias}' "
                                f"not mapped back to dataset"
                            )
                            continue
                        
                        # Check if column exists in this dataset's physical columns
                        # CRITICAL: Sanitize col_name to match known_cols which are sanitized
                        known_cols = dataset_col_lookup.get(ds_name, set())
                        sanitized_col_name = self._sanitize_col_name(col_name)
                        if sanitized_col_name not in known_cols:
                            logger.debug(
                                f"Skipping metric '{metric.unique_name}': "
                                f"references {table_alias}.\"{col_name}\" but column "
                                f"'{sanitized_col_name}' not found in {ds_name}."
                            )
                            # Mark for skipping
                            invalid_table_refs = [table_alias]
                            break
                    
                    if invalid_table_refs:
                        continue

                    # Step D: Verify quoted column refs exist in *some* dataset
                    # (relaxed from earlier per-dataset check — cross-table is valid)
                    all_known_cols: set[str] = set()
                    for ds_cols in dataset_col_lookup.values():
                        all_known_cols.update(ds_cols)
                    # Mandate 3: Include calculated column names so unspooled
                    # metric references are not rejected as "unknown columns".
                    for ds in sml.datasets:
                        for col in ds.columns:
                            if getattr(col, "is_calculated", False):
                                all_known_cols.add(col.unique_name.upper())
                    quoted_refs = _re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr)
                    invalid_refs = [
                        r for r in quoted_refs
                        if r not in all_known_cols
                        and r != metric_name
                        and r not in valid_aliases  # alias in quotes is OK
                    ]
                    if invalid_refs and all_known_cols:
                        logger.warning(
                            f"Skipping metric '{metric.unique_name}': "
                            f"sql_expression references unknown columns "
                            f"{invalid_refs}"
                        )
                        continue
                else:
                    # Manual override measures — rewrite table aliases via
                    # centralized resolver (Module 2).
                    expr = self._id.resolve_dot_notation(
                        expr, _alias_by_raw,
                        sanitize_col_fn=self._sanitize_col_name,
                    )
                    logger.info(
                        f"Including manual SQL override metric: {metric.unique_name}"
                    )
                
                # Normalize column/table references before validation so we can
                # fix common LLM alias drift (e.g., SALESFACT.IS_VAN_ARSDEL).
                expr = self._normalize_metric_column_references(
                    expr,
                    metric.unique_name,
                    dataset_col_lookup,
                    dataset_aliases,
                    metric_names=metric_name_set,
                    preferred_table_alias=alias,
                )

                # ===== NEW: Enhanced Column Reference Validation =====
                # Before adding to DDL, perform comprehensive validation that
                # the metric SQL only references columns that actually exist.
                # This prevents "invalid identifier" errors during Snowflake execution.
                is_valid, error_msg = self._validate_metric_column_references(
                    expr,
                    metric.unique_name,
                    dataset_col_lookup,
                    dataset_aliases,
                    metric_names=metric_name_set,
                )
                
                if not is_valid:
                    llm_expr = self._try_llm_metric_fallback_expression(
                        metric=metric,
                        metric_name=metric_name,
                        table_alias=alias,
                        alias_by_raw=_alias_by_raw,
                        dataset_col_lookup=dataset_col_lookup,
                        dataset_aliases=dataset_aliases,
                        metric_name_set=metric_name_set,
                        all_physical_col_names=all_physical_col_names,
                        emittable_metric_name_set=emittable_metric_name_set,
                        skipped_metric_names=skipped_metric_names,
                    )
                    if llm_expr:
                        expr = llm_expr
                    else:
                        logger.warning(
                            f"Skipping metric '{metric.unique_name}': {error_msg}"
                        )
                        continue

                unresolved_metric_refs = [
                    r for r in _re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr)
                    if r in metric_name_set
                    and r not in all_physical_col_names
                    and (
                        r not in emittable_metric_name_set
                        or r in skipped_metric_names
                    )
                    and r != metric_name
                ]
                if unresolved_metric_refs:
                    llm_expr = self._try_llm_metric_fallback_expression(
                        metric=metric,
                        metric_name=metric_name,
                        table_alias=alias,
                        alias_by_raw=_alias_by_raw,
                        dataset_col_lookup=dataset_col_lookup,
                        dataset_aliases=dataset_aliases,
                        metric_name_set=metric_name_set,
                        all_physical_col_names=all_physical_col_names,
                        emittable_metric_name_set=emittable_metric_name_set,
                        skipped_metric_names=skipped_metric_names,
                    )
                    if llm_expr:
                        expr = llm_expr
                    else:
                        logger.warning(
                            f"Skipping metric '{metric.unique_name}': unresolved metric "
                            f"dependencies {sorted(set(unresolved_metric_refs))}"
                        )
                        skipped_metric_names.add(metric_name)
                        continue
                
                # CRITICAL: Validate expression before appending to DDL
                # Prevent empty expressions and invalid patterns like SUM(*)
                if not expr or not expr.strip():
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': expression is empty"
                    )
                    continue

                self_ref_pattern = rf'(?<![\w\."])"{_re.escape(metric_name)}"(?![\w"])|(?<![\w\."])\b{_re.escape(metric_name)}\b(?![\w"])'
                if _re.search(self_ref_pattern, expr):
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': self-referential expression '{expr[:120]}'"
                    )
                    skipped_metric_names.add(metric_name)
                    continue
                
                # Check for invalid aggregation pattern SUM(*)
                expr_upper = expr.upper().strip()
                if expr_upper == 'SUM(*)' or expr_upper.endswith('SUM(*)'):
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': Invalid SUM(*) pattern detected"
                    )
                    continue
                
                metric_entity_alias = self._resolve_metric_emission_alias(
                    alias,
                    expr,
                    dataset_aliases,
                )
                logger.debug(
                    f"Adding metric to DDL: {metric_entity_alias}.{metric_name} = {expr}"
                )
                metrics_lines.append(f'  {metric_entity_alias}."{metric_name}" AS {expr}')

            elif metric.expression:
                basic_expr = self._try_basic_dax_metric_fallback_expression(
                    metric=metric,
                    table_alias=alias,
                    dataset_col_lookup=dataset_col_lookup,
                       model=sml,
                       dataset_by_name=dataset_by_name,
                )
                if basic_expr:
                    metrics_lines.append(f'  {alias}."{metric_name}" AS {basic_expr}')
                    continue

                if self._is_client_data_model(model_name) and self._is_simple_dax_aggregation_expression(metric.expression):
                    logger.warning(
                        "Client Data: skipping ambiguous simple metric '%s' from dataset '%s' to avoid cross-dataset remap",
                        metric.unique_name,
                        metric.dataset,
                    )
                    continue

                llm_expr = self._try_llm_metric_fallback_expression(
                    metric=metric,
                    metric_name=metric_name,
                    table_alias=alias,
                    alias_by_raw=_alias_by_raw,
                    dataset_col_lookup=dataset_col_lookup,
                    dataset_aliases=dataset_aliases,
                    metric_name_set=metric_name_set,
                    all_physical_col_names=all_physical_col_names,
                    emittable_metric_name_set=emittable_metric_name_set,
                    skipped_metric_names=skipped_metric_names,
                )
                if llm_expr:
                    metric_entity_alias = self._resolve_metric_emission_alias(
                        alias,
                        llm_expr,
                        dataset_aliases,
                    )
                    metrics_lines.append(f'  {metric_entity_alias}."{metric_name}" AS {llm_expr}')
                else:
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': no usable SQL expression and LLM fallback failed"
                    )
        
        metrics_lines = self._prune_unresolved_metric_lines(
            metrics_lines,
            metric_name_set,
        )

        def _extract_metric_name(metric_line: str) -> str | None:
            marker = '."'
            start = metric_line.find(marker)
            if start == -1:
                return None
            start += len(marker)
            end = metric_line.find('" AS ', start)
            if end == -1:
                return None
            return metric_line[start:end]

        emitted_metric_names: set[str] = set()
        for line in metrics_lines:
            metric_name = _extract_metric_name(line)
            if metric_name:
                emitted_metric_names.add(metric_name)

        scenario_alias = dataset_aliases.get("SCENARIO")
        if not scenario_alias:
            for ds_name, ds_alias in dataset_aliases.items():
                if self._sanitize_alias(ds_name) == "SCENARIO" or self._sanitize_alias(ds_alias) == "SCENARIO":
                    scenario_alias = ds_alias
                    break

        calendar_alias = dataset_aliases.get("CALENDAR")
        if not calendar_alias:
            for ds_name, ds_alias in dataset_aliases.items():
                if self._sanitize_alias(ds_name) == "CALENDAR" or self._sanitize_alias(ds_alias) == "CALENDAR":
                    calendar_alias = ds_alias
                    break

        for metric_alias, metric_name, metric_unique_name in expected_metrics:
            if metric_name in emitted_metric_names:
                continue
            metric = metric_by_unique_name.get(metric_unique_name)
            translated_expr = None
            if metric is not None:
                translated_expr = self._try_llm_metric_fallback_expression(
                    metric=metric,
                    metric_name=metric_name,
                    table_alias=metric_alias,
                    alias_by_raw=_alias_by_raw,
                    dataset_col_lookup=dataset_col_lookup,
                    dataset_aliases=dataset_aliases,
                    metric_name_set=metric_name_set,
                    all_physical_col_names=all_physical_col_names,
                    emittable_metric_name_set=emittable_metric_name_set,
                    skipped_metric_names=skipped_metric_names,
                )

            if translated_expr:
                metric_entity_alias = self._resolve_metric_emission_alias(
                    metric_alias,
                    translated_expr,
                    dataset_aliases,
                )
                metrics_lines.append(f'  {metric_entity_alias}."{metric_name}" AS {translated_expr}')
                emitted_metric_names.add(metric_name)
                logger.info(
                    "Metric '%s' used auto-translation during emission",
                    metric_unique_name,
                )
                continue
            logger.warning(
                "Metric '%s' could not be translated to SQL; emitting NULL placeholder to preserve sync",
                metric_unique_name,
            )
            metrics_lines.append(f'  {metric_alias}."{metric_name}" AS NULL')
            emitted_metric_names.add(metric_name)

        logger.info(f"=== METRICS GENERATION END (total lines: {len(metrics_lines)}) ===")
                
        if metrics_lines:
            definitions.append("METRICS (\n" + ",\n".join(metrics_lines) + "\n)")
        
        return lines[0] + "\n" + "\n".join(definitions) + ";"

    def _migrate_numeric_leading_identifiers(self, sml: SMLModel) -> None:
        """Prefix metric/dimension identifiers that begin with numeric tokens.

        This mutates the in-memory model only for the current emission cycle.
        It keeps labels untouched and updates internal references for renamed
        items to avoid broken hierarchy/measure dependencies.
        """
        def _starts_with_digit(name: str) -> bool:
            sanitized = self._id.sanitize_column(name)
            return bool(sanitized and sanitized[0].isdigit())

        metric_rename_map: dict[str, str] = {}
        attr_rename_map: dict[tuple[str, str], str] = {}
        metric_changes = 0
        attr_changes = 0

        # Metrics: 18_MONTH... -> L_18_MONTH...
        for metric in getattr(sml, "metrics", []):
            old_name = metric.unique_name
            if _starts_with_digit(old_name):
                new_name = old_name if old_name.startswith("L_") else f"L_{old_name}"
                if new_name != old_name:
                    metric.unique_name = new_name
                    metric_rename_map[old_name] = new_name
                    metric_changes += 1

        # Dimensions/attributes/hierarchy levels: 18_MONTH... -> N_18_MONTH...
        for dim in getattr(sml, "dimensions", []):
            dim_name = getattr(dim, "unique_name", "")

            for attr in getattr(dim, "attributes", []):
                old_attr = attr.unique_name
                if _starts_with_digit(old_attr):
                    new_attr = old_attr if old_attr.startswith("N_") else f"N_{old_attr}"
                    if new_attr != old_attr:
                        attr.unique_name = new_attr
                        attr_rename_map[(dim_name, old_attr)] = new_attr
                        attr_changes += 1

            for lvl in getattr(dim, "hierarchies", []):
                if _starts_with_digit(lvl.unique_name):
                    if not lvl.unique_name.startswith("N_"):
                        lvl.unique_name = f"N_{lvl.unique_name}"
                old_ref = lvl.attribute
                mapped_ref = attr_rename_map.get((dim_name, old_ref))
                if mapped_ref:
                    lvl.attribute = mapped_ref

        # Update metric dependency references if names were rewritten.
        if metric_rename_map:
            for metric in getattr(sml, "metrics", []):
                deps = list(getattr(metric, "depends_on_measures", []) or [])
                if deps:
                    metric.depends_on_measures = [metric_rename_map.get(d, d) for d in deps]

        if metric_changes or attr_changes:
            logger.warning(
                "Applied identifier migration before semantic view emit: "
                "%s metric(s), %s attribute(s) renamed to avoid numeric-leading identifiers",
                metric_changes,
                attr_changes,
            )
    
    def _sanitize_col_name(self, name: str) -> str:
        """Sanitize column name via unified IdentifierSanitizer (Mandate 1)."""
        return self._id.sanitize_column(name)

    @staticmethod
    def _is_physical_source_column(source_expression: str) -> bool:
        """Determine if a source_expression represents a plain physical column."""
        return IdentifierSanitizer.is_physical_source_column(source_expression)

    def _sanitize_semantic_name(self, name: str) -> str:
        """Sanitize semantic name and ensure it does not start with a digit."""
        sanitized = self._id.sanitize_column(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _to_snowflake_relationship_name(self, name: str) -> str:
        """Convert canonical relationship name to Snowflake-layer identifier.

        Canonical model names retain the REL_ prefix. Snowflake output removes
        only that leading REL_ for cleaner relationship identifiers.
        """
        rel_name = self._sanitize_semantic_name(name)
        if rel_name.startswith("REL_"):
            return rel_name[4:]
        return rel_name

    def _get_safe_object_name(self, name: str) -> str:
        """Sanitize object name for Snowflake."""
        return self._id.sanitize_column(name)

    def _sanitize(self, name: str) -> str:
        return self._id.sanitize_column(name)

    def _sanitize_alias(self, name: str) -> str:
        """Sanitize alias names and ensure they do not start with a digit."""
        sanitized = self._id.sanitize_alias(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _resolve_unique_table_alias(
        self,
        base_alias: str,
        used_aliases: set[str],
    ) -> str:
        """Ensure table alias is unique within a semantic view TABLES clause."""
        if base_alias not in used_aliases:
            used_aliases.add(base_alias)
            return base_alias

        idx = 2
        while True:
            candidate = f"{base_alias}_{idx}"
            if candidate not in used_aliases:
                used_aliases.add(candidate)
                return candidate
            idx += 1

    def _resolve_unique_metric_alias(
        self,
        base_alias: str,
        used_aliases: set[str],
        original_metric_name: str,
    ) -> str:
        """Ensure metric alias is unique within a single METRICS clause."""
        if base_alias not in used_aliases:
            used_aliases.add(base_alias)
            return base_alias

        idx = 2
        while True:
            candidate = f"{base_alias}_{idx}"
            if candidate not in used_aliases:
                used_aliases.add(candidate)
                logger.warning(
                    "Metric alias collision for '%s' (base '%s'); using '%s'",
                    original_metric_name,
                    base_alias,
                    candidate,
                )
                return candidate
            idx += 1

    def _resolve_unique_dimension_alias(
        self,
        base_alias: str,
        table_alias: str,
        used_aliases: set[str],
        original_dimension_name: str,
    ) -> str:
        """Ensure dimension alias is unique across a semantic view."""
        if base_alias not in used_aliases:
            used_aliases.add(base_alias)
            return base_alias

        idx = 2
        while True:
            candidate = self._sanitize_semantic_name(f"{base_alias}_{idx}")
            if candidate not in used_aliases:
                used_aliases.add(candidate)
                logger.warning(
                    "Dimension alias collision for '%s' (base '%s'); using '%s'",
                    original_dimension_name,
                    base_alias,
                    candidate,
                )
                return candidate
            idx += 1

    def _quote_if_needed(self, name: str) -> str:
        """Quote column names to preserve case."""
        return self._id.quote(name)

    def _safe_table_name(self, name: str) -> str:
        """Sanitize a physical table name via unified IdentifierSanitizer."""
        return self._id.sanitize_table_name(name)

    def generate_cortex_yaml(self, sml: SMLModel) -> str:
        """Generate YAML for Cortex Analyst."""
        # Convert SML to keys expected by Cortex
        # Schema:
        # name: ...
        # tables:
        #   - name: ...
        #     base_table: ...
        #     columns: ...
        #     measures: ...
        
        output = {
            "semantic_model": {
                "name": sml.unique_name,
                "node_type": "semantic_model" if self.behavior.features.enable_cortex_analyst else "unknown",
                "tables": []
            }
        }
        
        # Iterate datasets
        for ds in sml.datasets:
            # Point to the source table directly since _SV views are deprecated
            safe_table = self._safe_table_name(ds.source_table or ds.unique_name)
            
            table_def = {
                "name": ds.unique_name,
                "base_table": {
                    "database": self.config.database,
                    "schema": self.config.schema_name,
                    "table": safe_table
                },
                "dimensions": [],
                "measures": []
            }
            
            # Find dims for this dataset (from SML columns basically, or SML Dimensions)
            # SML Dimensions abstract away the table, so we look at attributes
            # attributes have 'dataset' field.
            
            # Find all attributes belonging to this dataset
            for dim in sml.dimensions:
                for attr in dim.attributes:
                    if attr.dataset == ds.unique_name:
                        # Skip if measure in FACT
                        attr_col = getattr(attr, 'dataset_column', None) or getattr(attr, 'source_column', None)
                        is_measure = any(m.dataset == ds.unique_name and m.source_column == attr_col for m in sml.metrics)
                        if is_measure and ds.is_fact:
                            continue
                            
                        table_def["dimensions"].append({
                            "name": attr.unique_name,
                            "expr": getattr(attr, 'dataset_column', None) or getattr(attr, 'source_column', attr.unique_name),
                            "description": getattr(attr, 'description', '') or ''
                        })
            
            # Find measures - include ALL measures, not just SQL-translatable ones
            for metric in sml.metrics:
                if metric.dataset == ds.unique_name:
                    measure_def = {
                        "name": metric.unique_name,
                        "description": metric.description or ""
                    }
                    
                    if metric.sql_expression:
                        # SQL expression available - use it - CRITICAL: strip markdown
                        measure_def["expr"] = self._sanitize_sql_markdown(metric.sql_expression)
                    elif metric.expression:
                        # DAX expression only - include as metadata for Cortex context
                        # Use a placeholder SQL that returns NULL (Cortex can still use the description)
                        measure_def["expr"] = "NULL"  # Placeholder - not computable in SQL
                        # Append DAX info to description for Cortex context
                        dax_note = f" [DAX: {metric.expression[:100]}{'...' if len(metric.expression) > 100 else ''}]"
                        measure_def["description"] = (measure_def["description"] + dax_note).strip()
                    else:
                        continue  # Skip measures with no expression at all
                    
                    # Add format string if available
                    if metric.format_string:
                        measure_def["sample_values"] = f"Format: {metric.format_string}"
                    
                    table_def["measures"].append(measure_def)
            
            output["semantic_model"]["tables"].append(table_def)
            
        return yaml.dump(output, sort_keys=False, Dumper=IndentDumper)

    def _drop_deprecated_views(self, cursor, sml: SMLModel) -> None:
        """
        Drop legacy semantic views ending in _SV.
        These are replaced by Cortex Analyst compatible views.
        """
        view_name = self._get_safe_object_name(sml.unique_name or sml.label)
        legacy_view = f"{self.config.database}.{self.config.schema_name}.{view_name}_SV"
        
        try:
            logger.info(f"Cleaning up legacy view: {legacy_view}")
            self._execute_sql(cursor, f"DROP VIEW IF EXISTS {legacy_view}", context=f"DROP VIEW {legacy_view}")
        except Exception as e:
            logger.warning(f"Failed to drop legacy view {legacy_view}: {e}")

    # =========================================================================
    # Naming Utilities (Universal Sync Protocol)
    # =========================================================================

    @staticmethod
    def _safe_table_name_static(name: str) -> str:
        """Sanitise a string for use as a Snowflake table/view name.

        Static fallback — use the instance method ``_safe_table_name``
        when a ``self`` reference is available for consistency with
        the configured ``IdentifierSanitizer``.

        Args:
            name: Raw model or metric name.

        Returns:
            Snowflake-safe uppercase identifier.
        """
        import re
        safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
        safe = re.sub(r"_+", "_", safe).strip("_")
        return safe.upper()



    # =========================================================================
    # Schema Evolution (Universal Sync Protocol)
    # =========================================================================

    def evolve_schema(
        self,
        cursor: Any,
        table_name: str,
        new_columns: list[tuple[str, str]],
    ) -> dict[str, str]:
        """Incrementally evolve a Snowflake table schema.

        Compares the existing table structure against the required columns
        from the latest Semantic Snapshot and applies non-destructive changes:
          - New columns → ALTER TABLE ADD COLUMN
          - Removed columns → Renamed with ``_DEPRECATED_`` prefix (Soft Delete)

        Args:
            cursor: Active Snowflake cursor.
            table_name: Fully qualified table name.
            new_columns: List of (column_name, snowflake_type) tuples
                representing the desired schema.

        Returns:
            Dict summarising actions taken, e.g.
            {"added": ["Col_A"], "deprecated": ["Col_B"]}.
        """
        actions: dict[str, list[str]] = {"added": [], "deprecated": []}

        # Fetch existing columns ─────────────────────────────────────────────
        try:
            self._execute_sql(cursor, f"DESC TABLE {table_name}", context=f"DESC TABLE {table_name}")
            existing = {row[0].upper(): row[1] for row in cursor.fetchall()}
        except Exception:
            logger.warning(
                f"Table {table_name} does not exist — cannot evolve schema"
            )
            return actions

        desired_upper = {col[0].upper(): col[1] for col in new_columns}

        # ADD new columns ─────────────────────────────────────────────────────
        for col_name, col_type in new_columns:
            if col_name.upper() not in existing:
                try:
                    ddl = (
                        f'ALTER TABLE {table_name} '
                        f'ADD COLUMN "{col_name.upper()}" {col_type}'
                    )
                    self._execute_sql(cursor, ddl, context=f"ALTER TABLE ADD COLUMN {table_name}.{col_name}")
                    actions["added"].append(col_name)
                    logger.info(f"Schema evolution: added column {col_name}")
                except Exception as e:
                    logger.error(f"Failed to add column {col_name}: {e}")

        # SOFT-DELETE removed columns ─────────────────────────────────────────
        for existing_col in existing:
            if (
                existing_col not in desired_upper
                and not existing_col.startswith("_DEPRECATED_")
            ):
                new_name = f"_DEPRECATED_{existing_col}"
                try:
                    ddl = (
                        f'ALTER TABLE {table_name} '
                        f'RENAME COLUMN "{existing_col}" TO "{new_name}"'
                    )
                    self._execute_sql(cursor, ddl, context=f"RENAME COLUMN {table_name}.{existing_col}")
                    actions["deprecated"].append(existing_col)
                    logger.info(
                        f"Schema evolution: soft-deleted {existing_col} → {new_name}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to deprecate column {existing_col}: {e}"
                    )

        if actions["added"] or actions["deprecated"]:
            logger.info(
                f"Schema evolution complete for {table_name}: "
                f"+{len(actions['added'])} / "
                f"-{len(actions['deprecated'])} columns"
            )
        return actions

    # =========================================================================
    # Tiered Semantic View Generation (Universal Sync Protocol)
    # =========================================================================

    def generate_semantic_view_tiered(
        self,
        model_name: str,
        shadow_table: str,
        triage_results: Dict[str, Any],
        grain_dimensions: list[str],
    ) -> str:
        """Generate a Snowflake VIEW that reconstructs measures per tier.

        The view never exposes the raw shadow table to users.  It provides
        business-friendly column aliases and reconstructs Tier 3 ratios.

        Args:
            model_name: Semantic model display name for view naming.
            shadow_table: Fully qualified shadow table reference.
            triage_results: Dict of metric_name → TriageResult.
            grain_dimensions: Dimension column names used in GROUP BY.

        Returns:
            A CREATE OR REPLACE VIEW DDL string.
        """
        view_name = f"V_{self._safe_table_name(model_name)}"
        full_view = (
            f"{self.config.database}.{self.config.schema_name}."
            f'"{view_name}"'
        )

        select_parts: list[str] = []
        group_by_parts: list[str] = []

        # Dimensions ─────────────────────────────────────────────────────────
        for dim in grain_dimensions:
            safe_dim = self._sanitize_col_name(dim)
            select_parts.append(f'    base."{safe_dim}"')
            group_by_parts.append(f'base."{safe_dim}"')

        # Measures ───────────────────────────────────────────────────────────
        for metric_name, triage in triage_results.items():
            safe = self._sanitize_col_name(metric_name)

            if triage.strategy.value == "passthrough":
                # Tier 1: simple pass-through aggregation
                select_parts.append(
                    f'    SUM(base."{safe}") AS "{safe}"'
                )

            elif triage.strategy.value == "aligned_history":
                # Tier 2: base value + companion columns
                select_parts.append(
                    f'    SUM(base."{safe}") AS "{safe}"'
                )
                for suffix in triage.aligned_measures:
                    alias = self._sanitize_col_name(f"{metric_name}{suffix}")
                    select_parts.append(
                        f'    SUM(base."{alias}") AS "{alias}"'
                    )

            elif triage.strategy.value == "decomposition":
                if triage.components:
                    # Tier 3: reconstruct ratio from components
                    num_col = self._sanitize_col_name(f"{metric_name}_Num")
                    den_col = self._sanitize_col_name(f"{metric_name}_Denom")
                    select_parts.append(
                        f'    SUM(base."{num_col}") / '
                        f'NULLIF(SUM(base."{den_col}"), 0) AS "{safe}"'
                    )
                else:
                    # Tier 3 without decomposition — pass-through
                    select_parts.append(
                        f'    SUM(base."{safe}") AS "{safe}"'
                    )

        select_block = ",\n".join(select_parts)
        group_block = ", ".join(group_by_parts)

        ddl = (
            f"CREATE OR REPLACE VIEW {full_view} AS\n"
            f"SELECT\n{select_block}\n"
            f"FROM {shadow_table} base\n"
            f"GROUP BY {group_block};"
        )

        logger.info(f"Generated tiered semantic view: {full_view}")
        return ddl

    # =========================================================================
    # Measure Data Sync Methods (Complex DAX Support)
    # =========================================================================
    
    def sync_measure_data(
        self,
        measure_name: str,
        data: list[dict],
        target_table: str = None,
        dimension_columns: list[str] = None,
        write_mode: str = "overwrite",
    ) -> bool:
        """
        Write materialized measure data to Snowflake.
        
        Creates a MEASURES_ prefixed table with the synced data from DAX query
        results. This allows complex measures to be pre-computed and queried
        directly in Snowflake.
        
        Args:
            measure_name: Original measure name (for metadata/logging)
            data: List of row dictionaries from DAX query
            target_table: Optional target table name (defaults to measure name)
            dimension_columns: Dimension column names for schema inference
            write_mode: "overwrite" to replace, "append" to add, "merge" for upsert
            
        Returns:
            True if successful
        """
        import snowflake.connector
        
        if not data:
            logger.warning(f"No data to sync for measure {measure_name}")
            return False
        
        # Generate table name from measure
        table_base = target_table or measure_name
        safe_table = f"MEASURES_{self._safe_table_name(table_base)}"
        full_table = f'{self.config.database}.{self.config.schema_name}."{safe_table}"'
        
        # Infer schema from first row
        first_row = data[0]
        columns = list(first_row.keys())
        
        # Map Python types to Snowflake types with smart inference
        type_map = []
        for col in columns:
            val = first_row[col]
            if isinstance(val, bool):
                type_map.append((col, "BOOLEAN"))
            elif isinstance(val, int):
                type_map.append((col, "INTEGER"))
            elif isinstance(val, float):
                type_map.append((col, "FLOAT"))
            elif isinstance(val, (list, dict)):
                type_map.append((col, "VARIANT"))
            else:
                # Check if it looks like a date
                str_val = str(val) if val else ""
                if len(str_val) == 10 and "-" in str_val:
                    type_map.append((col, "DATE"))
                else:
                    type_map.append((col, "VARCHAR(500)"))
        
        try:
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
            kwargs = get_snowflake_connect_kwargs(self.config)
            kwargs["session_parameters"] = {
                "QUERY_TAG": self.sf_behavior.query_tag or "Semabridge_MeasureSync"
            }
            conn = snowflake.connector.connect(**kwargs)
            cur = conn.cursor()
            
            try:
                # Create or replace table based on write mode
                if write_mode == "overwrite":
                    col_defs = ", ".join([f'"{self._sanitize_col_name(c[0])}" {c[1]}' for c in type_map])
                    create_ddl = f"CREATE OR REPLACE TABLE {full_table} ({col_defs})"
                    logger.debug(f"Creating table: {create_ddl}")
                    self._execute_sql(cur, create_ddl, context=f"CREATE TABLE {full_table}")
                elif write_mode == "append":
                    # Check if table exists, create if not
                    try:
                        self._execute_sql(cur, f"DESC TABLE {full_table}", context=f"DESC TABLE {full_table}")
                    except:
                        col_defs = ", ".join([f'"{self._sanitize_col_name(c[0])}" {c[1]}' for c in type_map])
                        self._execute_sql(cur, f"CREATE TABLE IF NOT EXISTS {full_table} ({col_defs})", context=f"CREATE TABLE IF NOT EXISTS {full_table}")
                
                # Insert data in batches for performance
                batch_size = 10000
                total_inserted = 0
                
                for i in range(0, len(data), batch_size):
                    batch = data[i:i + batch_size]
                    
                    # Build column list
                    col_list = ", ".join([f'"{self._sanitize_col_name(c)}"' for c in columns])
                    
                    # Build VALUES clause
                    value_rows = []
                    for row in batch:
                        vals = []
                        for col in columns:
                            val = row.get(col)
                            if val is None:
                                vals.append("NULL")
                            elif isinstance(val, bool):
                                vals.append("TRUE" if val else "FALSE")
                            elif isinstance(val, (int, float)):
                                vals.append(str(val))
                            elif isinstance(val, (list, dict)):
                                import json
                                vals.append(f"PARSE_JSON('{json.dumps(val)}')")
                            else:
                                # Escape single quotes in strings
                                escaped = str(val).replace("'", "''")
                                vals.append(f"'{escaped}'")
                        value_rows.append(f"({', '.join(vals)})")
                    
                    # Execute batch insert
                    insert_sql = f"INSERT INTO {full_table} ({col_list}) VALUES {', '.join(value_rows)}"
                    self._execute_sql(cur, insert_sql, context=f"INSERT INTO {full_table}")
                    total_inserted += len(batch)
                    
                    if len(data) > batch_size:
                        logger.debug(f"Inserted batch {i//batch_size + 1}: {len(batch)} rows")
                
                logger.info(f"Successfully synced {total_inserted} rows to {full_table}")
                
                # Add metadata about sync time
                try:
                    import datetime
                    sync_time = datetime.datetime.utcnow().isoformat()
                    self._execute_sql(
                        cur,
                        f"COMMENT ON TABLE {full_table} IS 'DAX Measure: {measure_name} | Synced: {sync_time}Z'",
                        context=f"COMMENT ON TABLE {full_table}",
                    )
                except:
                    pass
                    
                return True
                
            finally:
                cur.close()
                conn.close()
                
        except Exception as e:
            logger.error(f"Failed to sync measure data: {e}")
            raise
    
    def sync_all_measures(
        self,
        sml: SMLModel,
        fabric_extractor,  # FabricExtractor instance
        dataset_id: str,
        grain_dimensions: list[str] | None = None,
    ) -> dict:
        """Sync all syncable measures using the Universal Sync Protocol.

        Orchestrates the full triage → materialisation → evolution → view
        pipeline:

        1. Classify every metric via ``MeasureTriage``.
        2. Build a batch ``SUMMARIZECOLUMNS`` DAX query via
           ``MaterializationQueryBuilder``.
        3. Execute the query against Fabric.
        4. Write results to a shadow table via ``sync_measure_data``.
        5. Evolve the schema if the table already exists.
        6. Generate a tiered Semantic View.

        Falls back to per-metric sync when the batch path fails.

        Args:
            sml: The SML model containing metrics.
            fabric_extractor: Configured FabricExtractor instance.
            dataset_id: Fabric dataset ID for DAX queries.
            grain_dimensions: Override grain dimensions. Defaults to
                ``["'Date'[Year]"]``.

        Returns:
            Dict with sync results per measure.
        """
        from semabridge.converter.measure_triage import MeasureTriage
        from semabridge.converter.materialization_builder import (
            MaterializationQueryBuilder,
        )

        results: dict = {}
        grain = grain_dimensions or ["'Date'[Year]"]

        # Filter to syncable measures
        syncable = [m for m in sml.metrics if m.sync_enabled and not m.is_hidden]
        logger.info(f"Syncing {len(syncable)} measures (of {len(sml.metrics)} total)")

        if not syncable:
            logger.info("No syncable measures found — skipping")
            return results

        # ── Step 1: Triage ──────────────────────────────────────────────────
        triage = MeasureTriage()
        triage_results = triage.classify_all(syncable)

        # ── Step 2: Build batch DAX query ───────────────────────────────────
        builder = MaterializationQueryBuilder()
        try:
            batch_query = builder.build_query(
                metrics=syncable,
                triage_results=triage_results,
                grain_dimensions=grain,
            )
        except Exception as e:
            logger.warning(f"Batch query build failed: {e} — falling back to per-metric sync")
            batch_query = ""

        # ── Step 3: Execute & write ─────────────────────────────────────────
        if batch_query:
            try:
                data = fabric_extractor.execute_dax_query(dataset_id, batch_query)

                if data:
                    model_base = sml.unique_name or sml.label or "SEMABRIDGE"
                    self.sync_measure_data(
                        measure_name=model_base,
                        data=data,
                        target_table=f"SHADOW_{self._safe_table_name(model_base)}",
                        dimension_columns=grain,
                    )

                    # ── Step 4: Schema evolution ────────────────────────────
                    shadow_table = (
                        f"{self.config.database}.{self.config.schema_name}."
                        f'"MEASURES_SHADOW_{self._safe_table_name(model_base)}"'
                    )
                    # Determine desired columns from the first result row
                    if data:
                        new_cols = [
                            (self._sanitize_col_name(k), "VARCHAR(500)")
                            for k in data[0].keys()
                        ]
                        try:
                            import snowflake.connector
                            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
                            kwargs = get_snowflake_connect_kwargs(self.config)
                            conn = snowflake.connector.connect(**kwargs)
                            cur = conn.cursor()
                            try:
                                self.evolve_schema(cur, shadow_table, new_cols)
                            finally:
                                cur.close()
                                conn.close()
                        except Exception as evo_err:
                            logger.warning(f"Schema evolution skipped: {evo_err}")

                    # ── Step 5: Generate tiered view ────────────────────────
                    try:
                        view_ddl = self.generate_semantic_view_tiered(
                            model_name=model_base,
                            shadow_table=shadow_table,
                            triage_results=triage_results,
                            grain_dimensions=[
                                self._sanitize_col_name(d) for d in grain
                            ],
                        )
                        logger.debug(f"View DDL:\n{view_ddl}")
                    except Exception as view_err:
                        logger.warning(f"Tiered view generation failed: {view_err}")

                    for m in syncable:
                        results[m.unique_name] = {"status": "success", "rows": len(data)}
                else:
                    for m in syncable:
                        results[m.unique_name] = {"status": "empty", "rows": 0}

                return results

            except Exception as batch_err:
                logger.warning(
                    f"Batch sync failed: {batch_err} — falling back to per-metric sync"
                )

        # ── Fallback: per-metric sync (original behaviour) ──────────────────
        for metric in syncable:
            try:
                dimensions = metric.group_by_dimensions or grain

                if metric.requires_time_intel or metric.partition_dimension:
                    partition_col = metric.partition_dimension or "'Date'[Year]"
                    partition_vals = fabric_extractor.get_date_dimension_values(
                        dataset_id, partition_col
                    )

                    if partition_vals:
                        data = fabric_extractor.execute_paginated_measure_sync(
                            dataset_id=dataset_id,
                            measure_name=f"[{metric.unique_name}]",
                            group_by_dimensions=dimensions,
                            partition_column=partition_col,
                            partition_values=partition_vals,
                        )
                    else:
                        data = fabric_extractor.execute_measure_sync_query(
                            dataset_id=dataset_id,
                            measure_name=f"[{metric.unique_name}]",
                            group_by_dimensions=dimensions,
                        )
                else:
                    data = fabric_extractor.execute_measure_sync_query(
                        dataset_id=dataset_id,
                        measure_name=f"[{metric.unique_name}]",
                        group_by_dimensions=dimensions,
                    )

                if data:
                    self.sync_measure_data(
                        measure_name=metric.unique_name,
                        data=data,
                        dimension_columns=dimensions,
                    )
                    results[metric.unique_name] = {
                        "status": "success",
                        "rows": len(data),
                    }
                else:
                    results[metric.unique_name] = {"status": "empty", "rows": 0}

            except Exception as e:
                logger.error(f"Failed to sync measure {metric.unique_name}: {e}")
                results[metric.unique_name] = {"status": "failed", "error": str(e)}

        # Summary
        success = sum(1 for r in results.values() if r.get("status") == "success")
        failed = sum(1 for r in results.values() if r.get("status") == "failed")
        logger.info(f"Measure sync complete: {success} success, {failed} failed")

        return results

    def sync_all_measures_from_osi(
        self,
        osi: OSIModel,
        fabric_extractor,
        dataset_id: str,
        grain_dimensions: list[str] | None = None,
    ) -> dict:
        """Sync all syncable measures from an OSI model (no SML dependency).

        OSI-native counterpart of :meth:`sync_all_measures`.  Operates
        directly on ``OSIModel`` metrics which now carry ``sync_enabled``,
        ``group_by_dimensions``, ``requires_time_intel`` and
        ``partition_dimension`` fields set by the safety pipeline.

        Args:
            osi: The OSI model containing metrics.
            fabric_extractor: Configured FabricExtractor instance.
            dataset_id: Fabric dataset ID for DAX queries.
            grain_dimensions: Override grain dimensions.

        Returns:
            Dict with sync results per measure.
        """
        from semabridge.converter.measure_triage import MeasureTriage
        from semabridge.converter.materialization_builder import (
            MaterializationQueryBuilder,
        )

        results: dict = {}
        grain = grain_dimensions or ["'Date'[Year]"]

        # Filter to syncable metrics
        syncable = [m for m in osi.metrics if m.sync_enabled and not m.is_hidden]
        logger.info(f"[OSI] Syncing {len(syncable)} measures (of {len(osi.metrics)} total)")

        if not syncable:
            logger.info("No syncable measures found — skipping")
            return results

        # ── Step 1: Triage ──────────────────────────────────────────────────
        triage = MeasureTriage()
        triage_results = triage.classify_all(syncable)

        # ── Step 2: Build batch DAX query ───────────────────────────────────
        builder = MaterializationQueryBuilder()
        try:
            batch_query = builder.build_query(
                metrics=syncable,
                triage_results=triage_results,
                grain_dimensions=grain,
            )
        except Exception as e:
            logger.warning(f"Batch query build failed: {e} — falling back to per-metric sync")
            batch_query = ""

        # ── Step 3: Execute & write ─────────────────────────────────────────
        if batch_query:
            try:
                data = fabric_extractor.execute_dax_query(dataset_id, batch_query)

                if data:
                    model_base = osi.unique_name or osi.label or "SEMABRIDGE"
                    self.sync_measure_data(
                        measure_name=model_base,
                        data=data,
                        target_table=f"SHADOW_{self._safe_table_name(model_base)}",
                        dimension_columns=grain,
                    )

                    # ── Step 4: Schema evolution ────────────────────────────
                    shadow_table = (
                        f"{self.config.database}.{self.config.schema_name}."
                        f'"MEASURES_SHADOW_{self._safe_table_name(model_base)}"'
                    )
                    if data:
                        new_cols = [
                            (self._sanitize_col_name(k), "VARCHAR(500)")
                            for k in data[0].keys()
                        ]
                        try:
                            import snowflake.connector
                            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
                            kwargs = get_snowflake_connect_kwargs(self.config)
                            conn = snowflake.connector.connect(**kwargs)
                            cur = conn.cursor()
                            try:
                                self.evolve_schema(cur, shadow_table, new_cols)
                            finally:
                                cur.close()
                                conn.close()
                        except Exception as evo_err:
                            logger.warning(f"Schema evolution skipped: {evo_err}")

                    # ── Step 5: Generate tiered view ────────────────────────
                    try:
                        view_ddl = self.generate_semantic_view_tiered(
                            model_name=model_base,
                            shadow_table=shadow_table,
                            triage_results=triage_results,
                            grain_dimensions=[
                                self._sanitize_col_name(d) for d in grain
                            ],
                        )
                        logger.debug(f"View DDL:\n{view_ddl}")
                    except Exception as view_err:
                        logger.warning(f"Tiered view generation failed: {view_err}")

                    for m in syncable:
                        results[m.unique_name] = {"status": "success", "rows": len(data)}
                else:
                    for m in syncable:
                        results[m.unique_name] = {"status": "empty", "rows": 0}

                return results

            except Exception as batch_err:
                logger.warning(
                    f"Batch sync failed: {batch_err} — falling back to per-metric sync"
                )

        # ── Fallback: per-metric sync ───────────────────────────────────────
        for metric in syncable:
            try:
                dimensions = metric.group_by_dimensions or grain

                if metric.requires_time_intel or metric.partition_dimension:
                    partition_col = metric.partition_dimension or "'Date'[Year]"
                    partition_vals = fabric_extractor.get_date_dimension_values(
                        dataset_id, partition_col
                    )

                    if partition_vals:
                        data = fabric_extractor.execute_paginated_measure_sync(
                            dataset_id=dataset_id,
                            measure_name=f"[{metric.unique_name}]",
                            group_by_dimensions=dimensions,
                            partition_column=partition_col,
                            partition_values=partition_vals,
                        )
                    else:
                        data = fabric_extractor.execute_measure_sync_query(
                            dataset_id=dataset_id,
                            measure_name=f"[{metric.unique_name}]",
                            group_by_dimensions=dimensions,
                        )
                else:
                    data = fabric_extractor.execute_measure_sync_query(
                        dataset_id=dataset_id,
                        measure_name=f"[{metric.unique_name}]",
                        group_by_dimensions=dimensions,
                    )

                if data:
                    self.sync_measure_data(
                        measure_name=metric.unique_name,
                        data=data,
                        dimension_columns=dimensions,
                    )
                    results[metric.unique_name] = {
                        "status": "success",
                        "rows": len(data),
                    }
                else:
                    results[metric.unique_name] = {"status": "empty", "rows": 0}

            except Exception as e:
                logger.error(f"Failed to sync measure {metric.unique_name}: {e}")
                results[metric.unique_name] = {"status": "failed", "error": str(e)}

        # Summary
        success = sum(1 for r in results.values() if r.get("status") == "success")
        failed = sum(1 for r in results.values() if r.get("status") == "failed")
        logger.info(f"[OSI] Measure sync complete: {success} success, {failed} failed")

        return results

    # =========================================================================
    # OSI-Native Emission Methods (No SML Dependency)
    # =========================================================================

    def deploy_from_osi(self, osi: OSIModel, parallel: bool = False, max_workers: int = 4) -> bool:
        """Deploy an OSI model directly to Snowflake.

        Same lifecycle as :meth:`deploy` but operates on ``OSIModel`` without
        any SML conversion.

        Hardened with defensive checks for model attribute safety and a
        pre-validation gate that mirrors :meth:`deploy`.

        Args:
            osi: The OSI model to deploy.
            parallel: Enable parallel processing (unused, for API compatibility)
            max_workers: Maximum number of worker threads (unused, for API compatibility)

        Returns:
            True on successful deployment.

        Raises:
            ConnectorError: On deployment failure with context details.
        """
        model_name = (
            getattr(osi, "unique_name", None)
            or getattr(osi, "label", None)
            or "<unnamed_osi_model>"
        )
        logger.info(f"[deploy_from_osi] Starting deployment for '{model_name}'")

        # ── Defensive attribute checks ──────────────────────────────
        if not getattr(osi, "datasets", None):
            msg = (
                f"OSI model '{model_name}' has no datasets — "
                f"nothing to deploy."
            )
            logger.warning(msg)
            return True  # Vacuous success — no work to do

        for ds in osi.datasets:
            if not getattr(ds, "columns", None):
                logger.warning(
                    f"Dataset '{getattr(ds, 'unique_name', '?')}' in model "
                    f"'{model_name}' has no columns."
                )
        # ────────────────────────────────────────────────────────────

        try:
            import snowflake.connector

            # Decide connection strategy: session vs per-call
            if self._session_conn is not None:
                conn = self._session_conn
                owns_conn = False
                logger.debug("Reusing shared Snowflake session connection (OSI path)")
            else:
                from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
                logger.info(f"Connecting to Snowflake: {self.config.account}")
                kwargs = get_snowflake_connect_kwargs(self.config)
                kwargs["session_parameters"] = {
                    "QUERY_TAG": self.sf_behavior.query_tag or "Semabridge_Connector"
                }
                conn = snowflake.connector.connect(**kwargs)
                owns_conn = True

            logger.info("Starting OSI deployment with STRICT sanitization rules")

            try:
                cur = conn.cursor()

                # Step 0: Legacy Cleanup
                if self.behavior.legacy.drop_deprecated_views:
                    safe_view = self._get_safe_object_name(model_name)
                    legacy_view = (
                        f"{self.config.database}.{self.config.schema_name}"
                        f".{safe_view}_SV"
                    )
                    try:
                        logger.info(f"Cleaning up legacy view: {legacy_view}")
                        self._execute_sql(cur, f"DROP VIEW IF EXISTS {legacy_view}", context=f"DROP VIEW {legacy_view}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to drop legacy view {legacy_view}: {e}"
                        )

                # Step 1: Auto-create missing source tables
                if self.sf_behavior.create_missing_tables:
                    self._ensure_source_tables_exist(cur, osi)
                else:
                    logger.info(
                        "Skipping table creation (create_missing_tables=False)"
                    )

                # Step 1.25: Optional physical type-fix via CTAS + SWAP.
                if self.sf_behavior.apply_inferred_types:
                    self._apply_inferred_types_ctas_osi(cur, osi)
                else:
                    logger.info("Skipping inferred datatype CTAS fix (apply_inferred_types=False)")

                # Step 1.5: Pre-deployment validation gate (OSI path)
                # In STRICT mode, validation errors abort deployment.
                # In PERMISSIVE mode, validation errors are logged as warnings
                # and deployment continues (existing safety nets still apply).
                # Module 1: Fetch live Snowflake metadata so Tier 6 runs.
                try:
                    from semabridge.core.validation.global_validator import GlobalValidator
                    from semabridge.core.exceptions import (
                        ValidationError as SemaBridgeValidationError,
                    )
                    pk_mode = getattr(
                        self.sf_behavior, "pk_resolution_mode", None
                    )
                    is_strict = pk_mode and pk_mode.value == "strict"
                    sf_meta = self._fetch_schema_metadata(cur)
                    validator = GlobalValidator(
                        self._id, self.sf_behavior,
                        snowflake_metadata=sf_meta or None,
                    )
                    try:
                        val_report = validator.validate(
                            osi, halt_on_error=is_strict
                        )
                        if val_report.warning_count > 0:
                            logger.warning(
                                f"Pre-deployment validation passed with "
                                f"{val_report.warning_count} warning(s) "
                                f"for '{model_name}'"
                            )
                    except SemaBridgeValidationError as val_err:
                        if is_strict:
                            logger.error(
                                f"Pre-deployment validation BLOCKED "
                                f"'{model_name}' (strict): {val_err}"
                            )
                            raise ConnectorError(
                                f"Pre-deployment validation failed for "
                                f"'{model_name}': {val_err}",
                                connector_name="snowflake",
                            ) from val_err
                        else:
                            logger.warning(
                                f"Pre-deployment validation found issues "
                                f"for '{model_name}' (permissive): "
                                f"{val_err}. Proceeding."
                            )
                except ImportError:
                    logger.debug("Global validator not available — skipping")
                except ConnectorError:
                    raise
                except Exception as val_err:
                    logger.warning(
                        f"Pre-deployment validation crashed for "
                        f"'{model_name}': {val_err}. Proceeding."
                    )

                # Step 2: Pre-validate referenced tables exist
                self._preflight_check_osi(cur, osi)

                # Step 3: Generate and execute DDLs with retry
                ddls = self.generate_ddls_from_osi(osi)
                if not ddls:
                    logger.warning(
                        f"No DDLs generated for '{model_name}' — "
                        f"model may have no deployable datasets."
                    )
                    return True

                self._guard_relationship_clause(
                    model_name,
                    getattr(osi, "relationships", []),
                    ddls[0],
                    fail_on_missing=True,
                )
                logger.info(f"Generated {len(ddls)} Snowflake DDL(s) (OSI path)")

                for i, ddl in enumerate(ddls):
                    logger.info(f"Executing DDL statement {i+1}/{len(ddls)}...")
                    logger.info(
                        "DDL preview for statement %s/%s (OSI path):\n%s",
                        i + 1,
                        len(ddls),
                        self._ddl_preview(ddl),
                    )
                    self._execute_with_retry(cur, ddl)

                # Step 4: Generate and Save Cortex YAML
                try:
                    yaml_content = self.generate_cortex_yaml_from_osi(osi)
                    project_root = Path(__file__).resolve().parents[3]
                    safe_name = re.sub(r'[^\w\-.]', '_', model_name)
                    output_dir = project_root / "output" / "reverse" / safe_name
                    output_dir.mkdir(parents=True, exist_ok=True)

                    yaml_path = output_dir / "cortex_analyst.yaml"
                    with open(yaml_path, "w") as f:
                        f.write(yaml_content)
                    logger.info(f"Cortex Analyst YAML saved to {yaml_path}")
                except Exception as ex:
                    logger.warning(f"Failed to save Cortex YAML: {ex}")

                # Step 4b: Save semantic view DDL as YAML for auditing
                try:
                    from datetime import datetime, timezone
                    ddl_output = {
                        "metadata": {
                            "model_name": model_name,
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "ddl_count": len(ddls),
                            "path": "OSI",
                        },
                        "relationships": [
                            {
                                "name": rel.unique_name,
                                "from_dataset": rel.from_dataset,
                                "from_columns": list(rel.from_columns) if rel.from_columns else [],
                                "to_dataset": rel.to_dataset,
                                "to_columns": list(rel.to_columns) if rel.to_columns else [],
                                "cardinality": str(rel.cardinality) if rel.cardinality else None,
                                "is_active": rel.is_active,
                            }
                            for rel in (getattr(osi, 'relationships', None) or [])
                        ],
                        "ddl_statements": ddls,
                    }
                    ddl_yaml_path = output_dir / "semantic_view_ddl.yaml"
                    with open(ddl_yaml_path, "w") as f:
                        yaml.dump(ddl_output, f, default_flow_style=False, sort_keys=False, allow_unicode=True, width=200)
                    logger.info(f"Semantic View DDL YAML saved to {ddl_yaml_path}")
                except Exception as ex:
                    logger.warning(f"Failed to save DDL YAML: {ex}")

                logger.info(f"Semantic View deployed successfully for '{model_name}' (OSI path)")

            finally:
                if owns_conn:
                    conn.close()

            return True

        except ConnectorError:
            raise
        except Exception as e:
            msg = (
                f"OSI Deployment failed for '{model_name}': "
                f"{type(e).__name__}: {e}"
            )
            logger.error(msg)
            raise ConnectorError(msg, connector_name="snowflake") from e

    def _preflight_check_osi(self, cursor, osi: OSIModel) -> None:
        """Validate that all source tables referenced by the OSI model exist.

        Raises ``ConnectorError`` if any referenced source tables are missing
        after the table-creation step has already run.  This prevents the
        semantic view DDL from failing with opaque "invalid identifier" errors.
        """
        try:
            self._execute_sql(cursor, f"SHOW TABLES IN SCHEMA {self.config.schema_name}", context="SHOW TABLES")
            existing_tables = {row[1].upper() for row in cursor.fetchall()}

            self._execute_sql(cursor, f"SHOW VIEWS IN SCHEMA {self.config.schema_name}", context="SHOW VIEWS")
            existing_views = {row[1].upper() for row in cursor.fetchall()}

            all_existing = existing_tables | existing_views

            missing: list[str] = []
            for dataset in osi.datasets:
                source_table = dataset.source_table or dataset.unique_name
                safe_name = self._safe_table_name(source_table)
                if safe_name not in all_existing:
                    missing.append(safe_name)

            if missing:
                msg = (
                    f"Pre-flight check failed: {len(missing)} source table(s) "
                    f"missing in {self.config.database}.{self.config.schema_name}: "
                    f"{', '.join(missing[:10])}.  "
                    f"Aborting deployment to avoid invalid-identifier errors."
                )
                logger.error(msg)
                raise ConnectorError(msg)
            else:
                logger.info("Pre-flight check: all source tables present")
        except ConnectorError:
            raise
        except Exception as e:
            logger.warning(f"Pre-flight check skipped: {e}")

    def generate_ddls_from_osi(self, osi: OSIModel) -> List[str]:
        """Generate all Snowflake DDLs from an OSI model."""
        if not osi.datasets:
            return []

        semantic_ddl = self._generate_semantic_view_from_osi(osi)
        return [semantic_ddl]

    def _generate_semantic_view_from_osi(self, osi: OSIModel) -> str:
        """Generate Snowflake Semantic View DDL directly from an OSI model.

        Mirrors :meth:`_generate_semantic_view` but reads OSI field names
        (``source_column`` instead of ``dataset_column``, no
        ``is_measure_candidate``).
        """
        self._precompute_duplicate_mappings_for_osi(osi)

        view_name = self._get_safe_object_name(osi.unique_name or osi.label)
        model_name = osi.unique_name or osi.label
        suffix = self.behavior.semantic_model.view_suffix or "_SEMANTIC"
        
        # Ensure suffix is consistently uppercase and doesn't get applied twice
        suffix_upper = suffix.upper() if suffix else "_SEMANTIC"
        if not suffix_upper.startswith("_"):
            suffix_upper = "_" + suffix_upper
        
        # Prevent duplicate suffixes
        if view_name.upper().endswith(suffix_upper):
            safe_view_name = view_name
        else:
            safe_view_name = view_name + suffix_upper
        
        full_view_name = (
            f'"{self.config.database}"."{self.config.schema_name}"'
            f'."{safe_view_name}"'
        )

        lines = [f"CREATE OR REPLACE SEMANTIC VIEW {full_view_name}"]
        definitions: list[str] = []

        # -- Build set of metric source columns for dimension exclusion ------
        measure_columns: set[tuple[str, str]] = {
            (m.dataset, m.source_column) for m in osi.metrics if m.source_column
        }

        # -- Relationship PK map --------------------------------------------
        relationship_pk_map: dict[str, list[str]] = {}
        for rel in osi.relationships:
            if rel.is_active and rel.to_dataset and rel.to_columns:
                if rel.to_dataset not in relationship_pk_map:
                    relationship_pk_map[rel.to_dataset] = []
                for col in rel.to_columns:
                    if col not in relationship_pk_map[rel.to_dataset]:
                        relationship_pk_map[rel.to_dataset].append(col)
        metric_counts_by_dataset: dict[str, int] = {}
        related_datasets: set[str] = set()
        for metric in osi.metrics:
            ds_name = getattr(metric, "dataset", None)
            if ds_name:
                metric_counts_by_dataset[ds_name] = metric_counts_by_dataset.get(ds_name, 0) + 1
        for rel in osi.relationships:
            if getattr(rel, "is_active", True):
                if getattr(rel, "from_dataset", None):
                    related_datasets.add(rel.from_dataset)
                if getattr(rel, "to_dataset", None):
                    related_datasets.add(rel.to_dataset)

        # =================================================================
        # Build sanitized physical-column lookup per dataset.
        # Used by TABLES (PK validation), RELATIONSHIPS (FK validation),
        # DIMENSIONS and METRICS to exclude calculated columns that do
        # not exist as physical Snowflake columns.
        # =================================================================
        dataset_col_lookup: dict[str, set[str]] = {}
        dataset_by_name: dict[str, OSIDataset] = {d.unique_name: d for d in osi.datasets}
        for dataset in osi.datasets:
            dataset_col_lookup[dataset.unique_name] = {
                self._sanitize_col_name(c.unique_name)
                for c in dataset.columns
                if not c.unique_name.startswith("RowNumber")
                and not c.unique_name.startswith("_")
                and not (
                    getattr(c, 'source_expression', None)
                    and not self._is_physical_source_column(
                        getattr(c, 'source_expression', '')
                    )
                )
            }

        # =================================================================
        # TABLES
        # =================================================================
        tables_lines: list[str] = []
        dataset_aliases: dict[str, str] = {}
        used_table_aliases: set[str] = set()
        relationship_target_alias: dict[tuple[str, str], str] = {}
        declared_pk_by_alias: dict[str, list[str]] = {}

        for dataset in osi.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table = self._safe_table_name(source_table)
            full_table = (
                f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'
            )

            alias = self._resolve_unique_table_alias(
                self._sanitize_alias(dataset.unique_name),
                used_table_aliases,
            )
            dataset_aliases[dataset.unique_name] = alias

            # Determine PK columns — validate against physical column set
            known_phys = dataset_col_lookup.get(dataset.unique_name, set())
            relationship_pk_cols: list[str] = []
            is_measure_only_dataset = (
                metric_counts_by_dataset.get(dataset.unique_name, 0) > 0
                and dataset.unique_name not in related_datasets
                and not any(getattr(c, "is_key", False) for c in dataset.columns)
            )
            if is_measure_only_dataset:
                pk_cols = []
                relationship_pk_cols = []
            elif dataset.unique_name in relationship_pk_map:
                pk_candidates = [self._sanitize_col_name(c) for c in relationship_pk_map[dataset.unique_name]]
                pk_cols_valid = [c for c in pk_candidates if not known_phys or c in known_phys]
                for c in pk_cols_valid:
                    if c not in relationship_pk_cols:
                        relationship_pk_cols.append(c)
                if relationship_pk_cols:
                    pk_cols = [f'"{relationship_pk_cols[0]}"']
                else:
                    # All relationship PKs are non-physical; fall back
                    logger.warning(
                        f"All relationship PK columns for '{dataset.unique_name}' "
                        f"are non-physical ({pk_candidates}). Using first physical column."
                    )
                    fallback = next(iter(known_phys), "ID")
                    pk_cols = [f'"{fallback}"']
            else:
                key_cols = [c for c in dataset.columns if c.is_key]
                if key_cols:
                    pk_cols = [
                        f'"{self._sanitize_col_name(key_cols[0].unique_name)}"'
                    ]
                else:
                    # Mandate 4: PK Resolution Mode
                    pk_resolution_mode = getattr(
                        self.sf_behavior, "pk_resolution_mode", None
                    )
                    if pk_resolution_mode and pk_resolution_mode.value == "strict":
                        logger.error(
                            f"PK resolution STRICT: dataset '{dataset.unique_name}' "
                            f"has no is_key column and no inbound relationship PK. "
                            f"Aborting semantic view generation."
                        )
                        raise ValueError(
                            f"No primary key found for dataset '{dataset.unique_name}' "
                            f"(pk_resolution_mode=strict)."
                        )
                    col_name = (
                        dataset.columns[0].unique_name
                        if dataset.columns
                        else "ID"
                    )
                    pk_cols = [f'"{self._sanitize_col_name(col_name)}"']

            pk_clause = f"PRIMARY KEY ({', '.join(pk_cols)})" if pk_cols else ""
            tables_lines.append(f"  {alias} AS {full_table} {pk_clause}")
            if pk_cols:
                declared_pk_by_alias[alias] = [c.strip('"') for c in pk_cols]
                relationship_target_alias[(dataset.unique_name, declared_pk_by_alias[alias][0].upper())] = alias

            for rel_pk in relationship_pk_cols[1:]:
                alias_seed = self._sanitize_alias(f"{dataset.unique_name}__BY_{rel_pk}")
                rel_alias = self._resolve_unique_table_alias(alias_seed, used_table_aliases)
                tables_lines.append(
                    f'  {rel_alias} AS {full_table} PRIMARY KEY ("{rel_pk}")'
                )
                declared_pk_by_alias[rel_alias] = [rel_pk]
                relationship_target_alias[(dataset.unique_name, rel_pk.upper())] = rel_alias

        if tables_lines:
            definitions.append(
                "TABLES (\n" + ",\n".join(tables_lines) + "\n)"
            )

        # =================================================================
        # Build reverse alias lookup for OSI expression rewriting.
        # =================================================================
        from semabridge.utils.identifier_normalizer import IdentifierNormalizer
        _normalizer = IdentifierNormalizer(self._id)
        _alias_by_raw = _normalizer.build_alias_lookup(osi.datasets, dataset_aliases)

        # =================================================================
        # RELATIONSHIPS — validate FK columns exist as physical columns
        # =================================================================
        rel_lines: list[str] = []
        for rel in osi.relationships:
            if not rel.is_active:
                continue
            from_alias = dataset_aliases.get(rel.from_dataset)
            to_alias = dataset_aliases.get(rel.to_dataset)
            if from_alias and to_alias and rel.from_columns:
                from_col = self._sanitize_col_name(rel.from_columns[0])
                # Sanitize referenced (PK) column on the target side
                to_col = self._sanitize_col_name(rel.to_columns[0]) if rel.to_columns else ""
                # Validate FK column exists in the from-dataset's physical columns
                from_phys = dataset_col_lookup.get(rel.from_dataset, set())
                if from_phys and from_col not in from_phys:
                    fallback_fk = sorted(from_phys)[0]
                    logger.warning(
                        f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                        f"FK column '{from_col}' is not physical in '{rel.from_dataset}'. "
                        f"Using '{fallback_fk}' to preserve relationship emission."
                    )
                    from_col = fallback_fk
                # Validate to_col is both a physical column AND the declared PK
                # for that dataset. Snowflake requires REFERENCES to point to a
                # primary or unique key — any mismatch causes a SQL compilation error.
                if to_col:
                    to_phys = dataset_col_lookup.get(rel.to_dataset, set())
                    mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_col.upper()))
                    if mapped_to_alias:
                        to_alias = mapped_to_alias
                    declared_pk_cols = declared_pk_by_alias.get(to_alias, [])
                    # Case-insensitive check: sanitize both sides to uppercase
                    to_phys_upper = {c.upper() for c in to_phys}
                    if to_phys and to_col.upper() not in to_phys_upper:
                        if declared_pk_cols:
                            fallback_to = declared_pk_cols[0]
                        else:
                            fallback_to = sorted(to_phys)[0]
                        logger.warning(
                            f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                            f"referenced column '{to_col}' is not physical in '{rel.to_dataset}'. "
                            f"Using '{fallback_to}' to preserve relationship emission."
                        )
                        to_col = fallback_to
                        mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_col.upper()))
                        if mapped_to_alias:
                            to_alias = mapped_to_alias
                        declared_pk_cols = declared_pk_by_alias.get(to_alias, declared_pk_cols)
                    # Also check: to_col must be the declared PK for that dataset.
                    # Sanitize declared PKs the same way to_col is sanitized (handles Fabric
                    # mixed-casing like 'Id' vs sanitized 'ID').
                    if declared_pk_cols and to_col.upper() not in {c.upper() for c in declared_pk_cols}:
                        fallback_to = declared_pk_cols[0]
                        logger.warning(
                            f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                            f"referenced column '{to_col}' is not the declared PK {declared_pk_cols} "
                            f"for '{rel.to_dataset}'. Using '{fallback_to}' to preserve relationship emission."
                        )
                        to_col = fallback_to
                        mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_col.upper()))
                        if mapped_to_alias:
                            to_alias = mapped_to_alias
                # Build REFERENCES clause with explicit target column
                ref_clause = f'{to_alias} ("{to_col}")' if to_col else to_alias
                rel_name = self._to_snowflake_relationship_name(getattr(rel, "unique_name", "") or "")
                from_ref = f'"{from_col}"'
                if rel_name:
                    rel_lines.append(
                        f'  {rel_name} AS {from_alias} ({from_ref}) REFERENCES {ref_clause}'
                    )
                else:
                    rel_lines.append(
                        f'  {from_alias} ({from_ref}) REFERENCES {ref_clause}'
                    )

        if rel_lines:
            definitions.append(
                "RELATIONSHIPS (\n" + ",\n".join(rel_lines) + "\n)"
            )

        # =================================================================
        # DIMENSIONS
        # =================================================================
        dims_lines: list[str] = []
        added_dimensions: set[tuple[str, str, str]] = set()
        used_dimension_aliases: set[str] = set()

        # 1. Explicitly defined dimension attributes
        for dim in osi.dimensions:
            for attr in dim.attributes:
                alias = dataset_aliases.get(attr.dataset)
                if not alias:
                    logger.warning(
                        f"Alias not found for dataset '{attr.dataset}' "
                        f"- skipping dimension {attr.unique_name}"
                    )
                    continue

                # Cross-check: verify the backing column is a physical column.
                # Calculated columns (DAX expressions) don't exist in the
                # physical Snowflake table and cause "invalid identifier".
                # OSI uses source_column (vs SML dataset_column)
                phys_col = self._sanitize_col_name(attr.source_column)
                known_phys = dataset_col_lookup.get(attr.dataset, set())
                if known_phys and phys_col not in known_phys:
                    logger.debug(
                        f"Excluding dimension attribute '{attr.unique_name}' "
                        f"— column '{phys_col}' not in physical columns of "
                        f"'{attr.dataset}' (OSI path)"
                    )
                    continue

                semantic_name = self._sanitize_semantic_name(attr.unique_name)
                dim_key = (alias, semantic_name, phys_col)
                if dim_key not in added_dimensions:
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic_name,
                        alias,
                        used_dimension_aliases,
                        attr.unique_name,
                    )
                    dims_lines.append(
                        f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                    )
                    added_dimensions.add(dim_key)

        # 2. Raw dataset columns (excluding metric sources on fact tables)
        for dataset in osi.datasets:
            alias = dataset_aliases.get(dataset.unique_name)
            if not alias:
                continue
            known_phys = dataset_col_lookup.get(dataset.unique_name, set())
            for col in dataset.columns:
                if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                    continue

                # Skip calculated columns — these don't exist as physical
                # columns in Snowflake and cause "invalid identifier" errors
                source_expr = getattr(col, 'source_expression', None)
                if source_expr and not self._is_physical_source_column(source_expr):
                    logger.debug(
                        f"Excluding calculated column '{col.unique_name}' "
                        f"from DIMENSIONS (OSI path)"
                    )
                    continue

                semantic_name = self._sanitize_semantic_name(col.unique_name)
                phys_col = self._sanitize_col_name(col.unique_name)
                if known_phys and phys_col not in known_phys:
                    logger.debug(
                        f"Excluding non-physical column '{phys_col}' from '{dataset.unique_name}' "
                        f"(OSI path)"
                    )
                    continue
                dim_key = (alias, semantic_name, phys_col)
                if dim_key in added_dimensions:
                    continue

                # OSI: derive measure-candidate from metric source membership
                sync_all = self.behavior.semantic_model.sync_all_attributes
                col_is_metric_source = any(
                    m.dataset == dataset.unique_name
                    and m.source_column == col.unique_name
                    for m in osi.metrics
                )
                if col_is_metric_source and not sync_all:
                    logger.debug(
                        f"Excluding metric source '{col.unique_name}' "
                        f"from DIMENSIONS"
                    )
                    continue

                if (dataset.unique_name, col.unique_name) in measure_columns:
                    if dataset.is_fact:
                        continue

                emitted_name = self._resolve_unique_dimension_alias(
                    semantic_name,
                    alias,
                    used_dimension_aliases,
                    col.unique_name,
                )
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                )
                added_dimensions.add(dim_key)

        # Fallback: at least one dimension required
        if not dims_lines and osi.datasets:
            first_ds = osi.datasets[0]
            alias = dataset_aliases.get(first_ds.unique_name)
            # Use physical columns set
            known_phys = dataset_col_lookup.get(first_ds.unique_name, set())
            for col in first_ds.columns:
                phys = self._sanitize_col_name(col.unique_name)
                if not col.unique_name.startswith("_") and phys in known_phys:
                    semantic = self._sanitize_semantic_name(col.unique_name)
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic,
                        alias,
                        used_dimension_aliases,
                        col.unique_name,
                    )
                    dims_lines.append(
                        f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys, model_name=model_name)}'
                    )
                    break
            else:
                # Absolute fallback if no physical columns found (unlikely for a valid table)
                # But we still prefer the first physical column if one exists
                for col in first_ds.columns:
                    phys = self._sanitize_col_name(col.unique_name)
                    if phys in known_phys:
                        semantic = self._sanitize_semantic_name(col.unique_name)
                        emitted_name = self._resolve_unique_dimension_alias(
                            semantic,
                            alias,
                            used_dimension_aliases,
                            col.unique_name,
                        )
                        dims_lines.append(
                            f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys, model_name=model_name)}'
                        )
                        break
                else:
                    col = first_ds.columns[0]
                    semantic = self._sanitize_semantic_name(col.unique_name)
                    phys = self._sanitize_col_name(col.unique_name)
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic,
                        alias,
                        used_dimension_aliases,
                        col.unique_name,
                    )
                    dims_lines.append(
                        f'  {alias}."{emitted_name}" AS {self._format_physical_column_ref(alias, phys, model_name=model_name)}'
                    )

        if dims_lines:
            definitions.append(
                "DIMENSIONS (\n" + ",\n".join(dims_lines) + "\n)"
            )

        # =================================================================
        # METRICS
        # =================================================================
        metrics_lines: list[str] = []
        used_metric_names: set[str] = set()
        skipped_metric_names: set[str] = set()
        expected_metrics: list[tuple[str, str, str]] = []
        metric_name_set = {
            self._sanitize_alias(m.unique_name)
            for m in osi.metrics
        }
        all_physical_col_names: set[str] = set()
        for cols in dataset_col_lookup.values():
            all_physical_col_names.update(cols)
        emittable_metric_name_set = {
            self._sanitize_alias(m.unique_name)
            for m in osi.metrics
            if (m.source_column and m.aggregation) or m.sql_expression
        }
        metric_base_totals: dict[str, int] = {}
        for m in osi.metrics:
            base_alias = self._sanitize_alias(m.unique_name)
            metric_base_totals[base_alias] = metric_base_totals.get(base_alias, 0) + 1
        metric_base_seen: dict[str, int] = {}
        metric_signature_seen: dict[str, int] = {}
        metric_namespace = self._duplicate_namespace_key(osi.unique_name or osi.label)

        for metric in osi.metrics:
            alias = dataset_aliases.get(metric.dataset)
            if not alias:
                continue
            metric_dataset_obj = dataset_by_name.get(metric.dataset)
            metric_base_alias = self._sanitize_alias(metric.unique_name)
            metric_seen_idx = metric_base_seen.get(metric_base_alias, 0) + 1
            metric_base_seen[metric_base_alias] = metric_seen_idx
            metric_alias_seed = (
                metric_base_alias
                if metric_base_totals.get(metric_base_alias, 0) == 1
                else f"{metric_base_alias}_{metric_seen_idx}"
            )
            if metric_base_totals.get(metric_base_alias, 0) > 1:
                metric_signature_seed = self._build_duplicate_signature_seed(
                    source_name=metric.unique_name,
                    source_expression=metric.sql_expression or metric.expression,
                    data_type=None,
                    aggregation=metric.aggregation.value if metric.aggregation else None,
                )
                sig_idx = metric_signature_seen.get(metric_signature_seed, 0) + 1
                metric_signature_seen[metric_signature_seed] = sig_idx
                metric_signature = f"{metric_signature_seed}::occ{sig_idx}"
                metric_alias_seed = self._resolve_persistent_duplicate_name(
                    scope_type="metric",
                    namespace_key=metric_namespace,
                    dataset_key=self._sanitize_alias(metric.dataset),
                    normalized_base=metric_base_alias,
                    source_name=metric.unique_name,
                    source_signature=metric_signature,
                    preferred_name=metric_alias_seed,
                )
            metric_name = self._resolve_unique_metric_alias(
                metric_alias_seed,
                used_metric_names,
                metric.unique_name,
            )
            expected_metrics.append((alias, metric_name, metric.unique_name))

            if metric.source_column and metric.aggregation and (
                not metric.sql_expression or self._should_use_direct_metric_aggregation(metric)
            ):
                col_name = self._sanitize_col_name(metric.source_column)
                agg = metric.aggregation.value.upper()

                owners = [
                    ds for ds, cols in dataset_col_lookup.items()
                    if self._resolve_column_name_for_dataset(cols, col_name)
                ]
                if owners:
                    preferred_owner = None
                    if self._is_client_data_model(model_name):
                        if metric.dataset in owners:
                            preferred_owner = metric.dataset
                        else:
                            logger.warning(
                                "Client Data: skipping ambiguous metric '%s' from dataset '%s' because source column '%s' is shared by %s",
                                metric.unique_name,
                                metric.dataset,
                                col_name,
                                sorted(owners),
                            )
                            continue
                    elif metric.dataset in owners and len(owners) > 1:
                        non_self = [o for o in owners if o != metric.dataset]
                        fact_owners = [
                            o for o in non_self
                            if getattr(dataset_by_name.get(o), "is_fact", False)
                        ]
                        preferred_owner = fact_owners[0] if fact_owners else non_self[0]
                    elif len(owners) == 1:
                        preferred_owner = owners[0]

                    if preferred_owner and preferred_owner != metric.dataset:
                        owner_alias = dataset_aliases.get(preferred_owner)
                        owner_col = self._resolve_column_name_for_dataset(
                            dataset_col_lookup.get(preferred_owner, set()),
                            col_name,
                        ) or col_name
                        if owner_alias:
                            if agg == "COUNT_DISTINCT":
                                expr = f'COUNT(DISTINCT {self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                            elif agg == "NONE":
                                expr = f'{self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)}'
                            else:
                                expr = f'{agg}({self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                            metrics_lines.append(f'  {alias}."{metric_name}" AS {expr}')
                            continue
                known_cols = dataset_col_lookup.get(metric.dataset, set())
                if col_name not in known_cols:
                    owners = [
                        ds for ds, cols in dataset_col_lookup.items()
                        if self._resolve_column_name_for_dataset(cols, col_name)
                    ]
                    if len(owners) == 1:
                        owner_ds = owners[0]
                        owner_alias = dataset_aliases.get(owner_ds)
                        owner_col = self._resolve_column_name_for_dataset(
                            dataset_col_lookup.get(owner_ds, set()),
                            col_name,
                        ) or col_name
                        if owner_alias:
                            if agg == "COUNT_DISTINCT":
                                expr = f'COUNT(DISTINCT {self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                            elif agg == "NONE":
                                expr = f'{self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)}'
                            else:
                                expr = f'{agg}({self._format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                            metrics_lines.append(f'  {alias}."{metric_name}" AS {expr}')
                            logger.info(
                                "Remapped metric '%s' source column '%s' from dataset '%s' to '%s.%s'",
                                metric.unique_name,
                                col_name,
                                metric.dataset,
                                owner_ds,
                                owner_col,
                            )
                            continue

                    llm_expr = self._try_llm_metric_fallback_expression(
                        metric=metric,
                        metric_name=metric_name,
                        table_alias=alias,
                        alias_by_raw=_alias_by_raw,
                        dataset_col_lookup=dataset_col_lookup,
                        dataset_aliases=dataset_aliases,
                        metric_name_set=metric_name_set,
                        all_physical_col_names=all_physical_col_names,
                        emittable_metric_name_set=emittable_metric_name_set,
                        skipped_metric_names=skipped_metric_names,
                    )
                    if llm_expr:
                        metrics_lines.append(f'  {alias}."{metric_name}" AS {llm_expr}')
                        continue
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': column "
                        f"'{col_name}' not in dataset '{metric.dataset}'"
                    )
                    continue
                if agg == "COUNT_DISTINCT":
                    expr = f'COUNT(DISTINCT {self._format_physical_column_ref(alias, col_name, model_name=model_name)})'
                elif agg == "NONE":
                    expr = f'{self._format_physical_column_ref(alias, col_name, model_name=model_name)}'
                else:
                    expr = f'{agg}({self._format_physical_column_ref(alias, col_name, model_name=model_name)})'
                metric_entity_alias = self._resolve_metric_emission_alias(
                    alias,
                    expr,
                    dataset_aliases,
                )
                metrics_lines.append(
                    f'  {metric_entity_alias}."{metric_name}" AS {expr}'
                )

            elif metric.sql_expression:
                import re as _re
                expr = metric.sql_expression
                # CRITICAL FIX: Strip markdown code blocks from SQL expression
                expr = self._sanitize_sql_markdown(expr)
                is_override = getattr(metric, "complexity_tier", 0) >= 3
                if not is_override:
                    # Step A: Sanitize DAX-style [Column Name] → "COLUMN_NAME"
                    matches = _re.findall(r"\[(.+?)\]", expr)
                    for m in matches:
                        safe_m = self._sanitize_col_name(m)
                        expr = expr.replace(f"[{m}]", f'"{safe_m}"')

                    # Step B: Resolve cross-table references (TABLE.COLUMN
                    # or TABLE."COLUMN") via centralized resolver (Module 2).
                    expr = self._id.resolve_dot_notation(
                        expr, _alias_by_raw,
                        sanitize_col_fn=self._sanitize_col_name,
                    )

                    # Step C: Defence-in-depth — verify all TABLE.COL refs
                    # now use a valid alias from the TABLES clause.
                    valid_aliases = set(dataset_aliases.values())
                    invalid_table_refs = self._id.validate_table_refs(
                        expr, valid_aliases,
                    )
                    if invalid_table_refs:
                        logger.warning(
                            f"Metric '{metric.unique_name}': expression still "
                            f"references unknown table aliases {invalid_table_refs} "
                            f"after rewriting — skipping to avoid SQL compilation error"
                        )
                        continue

                    # Step D: Verify quoted column refs exist in *some* dataset
                    all_known_cols: set[str] = set()
                    for ds_cols in dataset_col_lookup.values():
                        all_known_cols.update(ds_cols)
                    # Mandate 3: Include calculated column names
                    for ds in osi.datasets:
                        for col in ds.columns:
                            if getattr(col, "is_calculated", False):
                                all_known_cols.add(col.unique_name.upper())
                    quoted_refs = _re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr)
                    invalid_refs = [
                        r for r in quoted_refs
                        if r not in all_known_cols
                        and r != metric_name
                        and r not in valid_aliases
                    ]
                    if invalid_refs and all_known_cols:
                        logger.warning(
                            f"Skipping metric '{metric.unique_name}': "
                            f"sql_expression references unknown columns "
                            f"{invalid_refs}"
                        )
                        continue
                else:
                    # Manual override — rewrite table aliases via
                    # centralized resolver (Module 2).
                    expr = self._id.resolve_dot_notation(
                        expr, _alias_by_raw,
                        sanitize_col_fn=self._sanitize_col_name,
                    )
                    logger.info(
                        f"Including manual SQL override metric: "
                        f"{metric.unique_name}"
                    )
                
                # Normalize column references to use unquoted uppercase (e.g., TABLE.COLUMN)
                expr = self._normalize_metric_column_references(
                    expr,
                    metric.unique_name,
                    dataset_col_lookup,
                    dataset_aliases,
                    metric_names=metric_name_set,
                    preferred_table_alias=alias,
                )
                
                # CRITICAL: Validate expression before appending to DDL
                # Prevent empty expressions and invalid patterns like SUM(*)
                if not expr or not expr.strip():
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': expression is empty"
                    )
                    continue

                self_ref_pattern = rf'(?<![\w\."])"{_re.escape(metric_name)}"(?![\w"])|(?<![\w\."])\b{_re.escape(metric_name)}\b(?![\w"])'
                if _re.search(self_ref_pattern, expr):
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': self-referential expression '{expr[:120]}'"
                    )
                    skipped_metric_names.add(metric_name)
                    continue
                
                # Check for invalid aggregation pattern SUM(*)
                expr_upper = expr.upper().strip()
                if expr_upper == 'SUM(*)' or expr_upper.endswith('SUM(*)'):
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': Invalid SUM(*) pattern detected"
                    )
                    continue

                unresolved_metric_refs = [
                    r for r in _re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr)
                    if r in metric_name_set
                    and r not in all_physical_col_names
                    and (
                        r not in emittable_metric_name_set
                        or r in skipped_metric_names
                    )
                    and r != metric_name
                ]
                if unresolved_metric_refs:
                    llm_expr = self._try_llm_metric_fallback_expression(
                        metric=metric,
                        metric_name=metric_name,
                        table_alias=alias,
                        alias_by_raw=_alias_by_raw,
                        dataset_col_lookup=dataset_col_lookup,
                        dataset_aliases=dataset_aliases,
                        metric_name_set=metric_name_set,
                        all_physical_col_names=all_physical_col_names,
                        emittable_metric_name_set=emittable_metric_name_set,
                        skipped_metric_names=skipped_metric_names,
                    )
                    if llm_expr:
                        expr = llm_expr
                    else:
                        logger.warning(
                            f"Skipping metric '{metric.unique_name}': unresolved metric "
                            f"dependencies {sorted(set(unresolved_metric_refs))}"
                        )
                        skipped_metric_names.add(metric_name)
                        continue
                
                metrics_lines.append(
                    f'  {alias}."{metric_name}" AS {expr}'
                )

            elif metric.expression:
                basic_expr = self._try_basic_dax_metric_fallback_expression(
                    metric=metric,
                    table_alias=alias,
                    dataset_col_lookup=dataset_col_lookup,
                       model=osi if 'osi' in locals() else None,
                       dataset_by_name={ds.unique_name: ds for ds in (osi.datasets if 'osi' in locals() else [])},
                )
                if basic_expr:
                    metrics_lines.append(f'  {alias}."{metric_name}" AS {basic_expr}')
                    continue

                if self._is_client_data_model(model_name) and self._is_simple_dax_aggregation_expression(metric.expression):
                    logger.warning(
                        "Client Data: skipping ambiguous simple metric '%s' from dataset '%s' to avoid cross-dataset remap",
                        metric.unique_name,
                        metric.dataset,
                    )
                    continue

                llm_expr = self._try_llm_metric_fallback_expression(
                    metric=metric,
                    metric_name=metric_name,
                    table_alias=alias,
                    alias_by_raw=_alias_by_raw,
                    dataset_col_lookup=dataset_col_lookup,
                    dataset_aliases=dataset_aliases,
                    metric_name_set=metric_name_set,
                    all_physical_col_names=all_physical_col_names,
                    emittable_metric_name_set=emittable_metric_name_set,
                    skipped_metric_names=skipped_metric_names,
                )
                if llm_expr:
                    metric_entity_alias = self._resolve_metric_emission_alias(
                        alias,
                        llm_expr,
                        dataset_aliases,
                    )
                    metrics_lines.append(f'  {metric_entity_alias}."{metric_name}" AS {llm_expr}')
                else:
                    logger.warning(
                        f"Skipping metric '{metric.unique_name}': no usable SQL expression and LLM fallback failed"
                    )

        metrics_lines = self._prune_unresolved_metric_lines(
            metrics_lines,
            metric_name_set,
        )

        def _extract_metric_name(metric_line: str) -> str | None:
            marker = '."'
            start = metric_line.find(marker)
            if start == -1:
                return None
            start += len(marker)
            end = metric_line.find('" AS ', start)
            if end == -1:
                return None
            return metric_line[start:end]

        emitted_metric_names: set[str] = set()
        for line in metrics_lines:
            metric_name = _extract_metric_name(line)
            if metric_name:
                emitted_metric_names.add(metric_name)

        scenario_alias = dataset_aliases.get("SCENARIO")
        if not scenario_alias:
            for ds_name, ds_alias in dataset_aliases.items():
                if self._sanitize_alias(ds_name) == "SCENARIO" or self._sanitize_alias(ds_alias) == "SCENARIO":
                    scenario_alias = ds_alias
                    break

        calendar_alias = dataset_aliases.get("CALENDAR")
        if not calendar_alias:
            for ds_name, ds_alias in dataset_aliases.items():
                if self._sanitize_alias(ds_name) == "CALENDAR" or self._sanitize_alias(ds_alias) == "CALENDAR":
                    calendar_alias = ds_alias
                    break

        for metric_alias, metric_name, metric_unique_name in expected_metrics:
            if metric_name in emitted_metric_names:
                continue
            known_expr = self._build_known_metric_fallback_expression(
                metric_name=metric_name,
                fact_alias=metric_alias,
                scenario_alias=scenario_alias,
                calendar_alias=calendar_alias,
                dataset_col_lookup=dataset_col_lookup,
            )
            if known_expr:
                metrics_lines.append(f'  {metric_alias}."{metric_name}" AS {known_expr}')
                emitted_metric_names.add(metric_name)
                logger.info(
                    "Metric '%s' used deterministic fallback SQL during emission",
                    metric_unique_name,
                )
                continue
            logger.warning(
                "Metric '%s' could not be translated to SQL; emitting NULL placeholder to preserve sync",
                metric_unique_name,
            )
            metrics_lines.append(f'  {metric_alias}."{metric_name}" AS NULL')
            emitted_metric_names.add(metric_name)

        if metrics_lines:
            definitions.append(
                "METRICS (\n" + ",\n".join(metrics_lines) + "\n)"
            )

        return lines[0] + "\n" + "\n".join(definitions) + ";"

    def generate_cortex_yaml_from_osi(self, osi: OSIModel) -> str:
        """Generate Cortex Analyst YAML from an OSI model."""
        output = {
            "semantic_model": {
                "name": osi.unique_name,
                "node_type": (
                    "semantic_model"
                    if self.behavior.features.enable_cortex_analyst
                    else "unknown"
                ),
                "tables": [],
            }
        }

        for ds in osi.datasets:
            safe_table = self._safe_table_name(
                ds.source_table or ds.unique_name
            )
            table_def = {
                "name": ds.unique_name,
                "base_table": {
                    "database": self.config.database,
                    "schema": self.config.schema_name,
                    "table": safe_table,
                },
                "dimensions": [],
                "measures": [],
            }

            for dim in osi.dimensions:
                for attr in dim.attributes:
                    if attr.dataset == ds.unique_name:
                        is_measure = any(
                            m.dataset == ds.unique_name
                            and m.source_column == attr.source_column
                            for m in osi.metrics
                        )
                        if is_measure and ds.is_fact:
                            continue
                        table_def["dimensions"].append(
                            {
                                "name": attr.unique_name,
                                "expr": attr.source_column,
                                "description": attr.label or "",
                            }
                        )

            for metric in osi.metrics:
                if metric.dataset == ds.unique_name:
                    measure_def = {
                        "name": metric.unique_name,
                        "description": metric.description or "",
                    }
                    if metric.sql_expression:
                        # CRITICAL: strip markdown from SQL expression
                        measure_def["expr"] = self._sanitize_sql_markdown(metric.sql_expression)
                    elif metric.expression:
                        measure_def["expr"] = "NULL"
                        dax_note = (
                            f" [DAX: {metric.expression[:100]}"
                            f"{'...' if len(metric.expression) > 100 else ''}]"
                        )
                        measure_def["description"] = (
                            measure_def["description"] + dax_note
                        ).strip()
                    else:
                        continue
                    if metric.format_string:
                        measure_def["sample_values"] = (
                            f"Format: {metric.format_string}"
                        )
                    table_def["measures"].append(measure_def)

            output["semantic_model"]["tables"].append(table_def)

        return yaml.dump(output, sort_keys=False, Dumper=IndentDumper)