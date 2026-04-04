"""
Databricks Publisher.

Publishes SML models to Databricks SQL Warehouse using the Statements API.

Deployment produces three layers:
1. **Metadata table** (Delta) — introspection catalog of all model objects.
2. **Metric views** (native) — Unity Catalog WITH METRICS LANGUAGE YAML (preferred).
3. **SQL views** (fallback) — plain CREATE OR REPLACE VIEW for older runtimes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import AggregationType, SMLDataset, SMLMetric, SMLModel
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class DatabricksPublishError(Exception):
    """Raised when Databricks publish fails."""


# ── Deploy Status Constants ──────────────────────────────────────────────────
DEPLOY_STATUS_DEPLOYED = "DEPLOYED"
DEPLOY_STATUS_NOT_DEPLOYED = "NOT_DEPLOYED"
DEPLOY_STATUS_SKIPPED = "SKIPPED"
DEPLOY_STATUS_PLAN_ONLY = "PLAN_ONLY"

DEPLOY_REASON_DAX_NOT_SUPPORTED = "DAX_NOT_SUPPORTED"
DEPLOY_REASON_VALIDATION_FAILED = "VALIDATION_FAILED"
DEPLOY_REASON_CROSS_TABLE = "CROSS_TABLE_NOT_SUPPORTED"
DEPLOY_REASON_SOURCE_NOT_FOUND = "SOURCE_TABLE_NOT_FOUND"

# ── Translation Type Constants ───────────────────────────────────────────────
TRANSLATION_TYPE_SQL_NATIVE = "SQL_NATIVE"
TRANSLATION_TYPE_AGGREGATION_BUILT = "AGGREGATION_BUILT"
TRANSLATION_TYPE_DAX_TRANSLATED = "DAX_TRANSLATED"
TRANSLATION_TYPE_DAX_SKIPPED = "DAX_SKIPPED"

# ── Confidence Levels ────────────────────────────────────────────────────────
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_NONE = "NONE"

# ── View Technology ──────────────────────────────────────────────────────────
VIEW_TYPE_METRIC = "metric_view"
VIEW_TYPE_SQL = "sql_view"
VIEW_TYPE_NONE = "none"


@dataclass(frozen=True)
class ResolvedMeasure:
    """Immutable result of measure SQL resolution with confidence scoring."""
    name: str
    sql_expression: Optional[str]
    translation_type: str
    confidence: str
    original_dax: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class MeasureDeployResult:
    """Per-measure deployment outcome for the manifest."""
    name: str
    dataset: str
    status: str
    translation_type: str
    confidence: str
    reason: str = ""
    view_name: str = ""
    sql_preview: str = ""


@dataclass
class DeploymentManifest:
    """Structured deployment summary for observability."""
    model_name: str
    deploy_timestamp: str = ""
    runtime_supports_metric_views: bool = False
    view_type_used: str = VIEW_TYPE_NONE
    total_measures: int = 0
    deployed: int = 0
    skipped: int = 0
    plan_only: int = 0
    failed: int = 0
    measure_results: list[MeasureDeployResult] = field(default_factory=list)


class DatabricksPublisher:
    """Publish semantic artifacts to Databricks SQL Warehouse."""

    # In-memory OAuth token cache: avoids redundant token exchanges
    # while keeping the implementation lightweight.
    _oauth_cache: dict[str, object] = {}

    def __init__(
        self,
        config: DatabricksConfig,
        behavior: Optional[ConnectorBehavior] = None,
    ):
        self.config = config
        self._behavior = behavior or ConnectorBehavior()
        self._dbx_behavior: DatabricksBehavior = self._behavior.databricks

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        """Build authorization headers for Databricks API calls.

        Selects the token source based on ``config.auth_type``:
            - ``interactive``:  Uses the MSAL device code token stored in
              CredentialManager.  Auto-refreshes expired tokens.
            - ``service_principal``:  Exchanges client credentials for a
              short-lived OAuth token (cached in memory until expiry).
            - ``pat``:  Uses the stored Personal Access Token directly.

        Returns:
            Dict with ``Authorization`` and ``Content-Type`` headers.

        Raises:
            DatabricksPublishError: If the required credentials are missing
                or the token exchange / refresh fails.
        """
        token = self._resolve_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _resolve_token(self) -> str:
        """Resolve the access token based on config.auth_type.

        For interactive mode, reads from CredentialManager and auto-refreshes
        if the token is expired.  Never exposes tokens to logs.

        Returns:
            A valid access token string.

        Raises:
            DatabricksPublishError: If no valid token can be obtained.
        """
        from semabridge.repository.credential_manager import CredentialManager

        auth_type = getattr(self.config, "auth_type", "pat") or "pat"

        if auth_type == "interactive":
            cm = CredentialManager()
            # Check if token needs refresh
            if cm.is_databricks_token_expired():
                logger.info("Databricks MSAL token expired, attempting refresh...")
                refreshed = cm.refresh_databricks_token()
                if not refreshed:
                    raise DatabricksPublishError(
                        "Databricks session expired. Please sign in again "
                        "via the Connections panel."
                    )

            token_data = cm.get_databricks_token()
            if not token_data or not token_data.get("access_token"):
                raise DatabricksPublishError(
                    "No Databricks interactive token found. "
                    "Please sign in via the Connections panel."
                )
            return token_data["access_token"]

        if auth_type == "service_principal":
            return self._get_oauth_token()

        # Default: PAT
        if self.config.token is None:
            raise DatabricksPublishError(
                "No Databricks PAT configured. Set DATABRICKS_TOKEN or "
                "switch to interactive/service_principal auth."
            )
        return self.config.token.get_secret_value()

    def _get_oauth_token(self) -> str:
        """Exchange client credentials for an OAuth access token.

        Uses the Databricks OIDC token endpoint with the
        ``client_credentials`` grant type.  Results are cached in memory
        with a 60-second safety buffer before actual expiry.

        Returns:
            A valid OAuth access token string.

        Raises:
            DatabricksPublishError: If credentials are missing or the
                token exchange request fails.
        """
        import time

        cache_key = f"{self.config.host}:{self.config.client_id}"
        cached = DatabricksPublisher._oauth_cache.get(cache_key)
        if cached and time.time() < cached["expires_at"]:
            return cached["token"]

        if not self.config.client_id or not self.config.client_secret:
            raise DatabricksPublishError(
                "Service principal auth requires DATABRICKS_CLIENT_ID and "
                "DATABRICKS_CLIENT_SECRET."
            )

        token_url = f"https://{self.config.host}/oidc/v1/token"
        try:
            resp = requests.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret.get_secret_value(),
                    "scope": "all-apis",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise DatabricksPublishError(
                f"OAuth token exchange network error: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise DatabricksPublishError(
                f"OAuth token exchange failed (HTTP {resp.status_code}): "
                f"{resp.text[:200]}"
            )

        data = resp.json()
        access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)

        # Cache with 60-second safety buffer
        DatabricksPublisher._oauth_cache[cache_key] = {
            "token": access_token,
            "expires_at": time.time() + expires_in - 60,
        }
        logger.info(
            "Databricks OAuth token obtained (expires in %ds)", expires_in,
        )
        return access_token

    def _fq_name(self, name: str) -> str:
        safe = str(name).replace('"', '""')
        return f'`{self.config.catalog}`.`{self.config.schema_name}`.`{safe}`'

    # ── Source Table Resolution ──────────────────────────────────────────────

    def _resolve_source_table(
        self,
        dataset: SMLDataset,
    ) -> str:
        """Resolve the fully-qualified Databricks source table for a dataset.

        Resolution priority:
            1. Explicit mapping in behavior.source_table_mapping
            2. source_catalog.source_schema.dataset_name (if configured)
            3. Same catalog/schema as metadata table

        Args:
            dataset: The SML dataset to resolve a source table for.

        Returns:
            Fully-qualified Databricks table name with backtick quoting.
        """
        ds_name = self._sanitize_identifier(
            dataset.source_table or dataset.unique_name
        )

        # Priority 1: Explicit mapping
        mapping = self._dbx_behavior.source_table_mapping
        if ds_name in mapping:
            return mapping[ds_name]
        # Case-insensitive lookup
        for key, val in mapping.items():
            if key.upper() == ds_name.upper():
                return val

        # Priority 2: Configured source catalog/schema
        src_catalog = self._dbx_behavior.source_catalog.strip()
        src_schema = self._dbx_behavior.source_schema.strip()
        if src_catalog and src_schema:
            return f"`{src_catalog}`.`{src_schema}`.`{ds_name}`"

        # Priority 3: Same catalog/schema as metadata table
        return self._fq_name(ds_name)

    # ── Metric View Probe ────────────────────────────────────────────────────

    def _probe_metric_view_support(self) -> bool:
        """Probe Databricks warehouse for metric view support.

        Industry pattern: try the operation, handle the error.
        Safer than version-gating because features may differ
        between serverless/pro/classic warehouses.

        Uses information_schema.tables as the source because it
        always exists in any Databricks catalog — Databricks validates
        source table existence at metric view creation time.

        Returns:
            True if WITH METRICS LANGUAGE YAML is supported.
        """
        probe_view = self._fq_name("_semabridge_probe_mv")
        # Use information_schema.tables — guaranteed to exist in every catalog
        probe_source = f"{self.config.catalog}.information_schema.tables"
        probe_sql = (
            f"CREATE OR REPLACE VIEW {probe_view} "
            f"WITH METRICS LANGUAGE YAML AS $$\n"
            f"version: 1.1\n"
            f"source: {probe_source}\n"
            f"measures:\n  - name: probe\n    expr: COUNT(1)\n$$"
        )
        try:
            self.execute_statements([probe_sql])
            # Clean up probe view
            self.execute_statements([f"DROP VIEW IF EXISTS {probe_view}"])
            logger.info("✅ Databricks metric view support: CONFIRMED")
            return True
        except DatabricksPublishError as exc:
            logger.info(
                "ℹ️  Databricks metric views NOT supported on this warehouse. "
                "Falling back to plain SQL views. Probe error: %s",
                str(exc)[:150],
            )
            return False

    def _determine_view_type(self) -> str:
        """Determine which view technology to use based on config + probe.

        Returns:
            One of VIEW_TYPE_METRIC, VIEW_TYPE_SQL, or VIEW_TYPE_NONE.
        """
        configured = self._dbx_behavior.measure_view_type.strip().lower()

        if configured == "none" or not self._dbx_behavior.create_measure_views:
            return VIEW_TYPE_NONE
        if configured == "metric_view":
            return VIEW_TYPE_METRIC
        if configured == "sql_view":
            return VIEW_TYPE_SQL

        # "auto" — probe the runtime
        if self._probe_metric_view_support():
            return VIEW_TYPE_METRIC
        return VIEW_TYPE_SQL

    # ── Metric View YAML Generation ──────────────────────────────────────────

    def _generate_metric_view_yaml(
        self,
        sml_model: SMLModel,
        dataset: SMLDataset,
        source_fq: str,
        resolved_measures: list[ResolvedMeasure],
    ) -> str:
        """Generate YAML for a Databricks Unity Catalog metric view.

        Follows official Databricks metric view YAML spec v1.1.
        The source can be:
            - A table/view name (if mapped or exists)
            - An inline SQL query (if no physical table)

        Args:
            sml_model: The parent semantic model.
            dataset: The dataset these measures belong to.
            source_fq: Fully-qualified source table reference.
            resolved_measures: List of resolved measures with confidence.

        Returns:
            YAML string for the metric view body.
        """
        model_label = self._escape_literal(sml_model.label or sml_model.unique_name)
        ds_label = self._escape_literal(dataset.label or dataset.unique_name)

        # Build the source — use inline SQL query when no physical table is mapped
        ds_name = self._sanitize_identifier(
            dataset.source_table or dataset.unique_name
        )
        has_explicit_mapping = self._has_source_table_mapping(ds_name)

        lines: list[str] = [
            "version: 1.1",
            f'comment: "Semabridge: {model_label} - {ds_label}"',
        ]

        if has_explicit_mapping:
            # Direct table reference — source table is known to exist
            source_for_yaml = source_fq.replace("`", "")
            lines.append(f"source: {source_for_yaml}")
        else:
            # Inline SQL query as source — no physical table needed
            # Generates a typed schema SELECT using CAST(NULL AS type)
            inline_cols: list[str] = []
            for col in dataset.columns:
                if col.is_hidden:
                    continue
                safe_name = self._sanitize_identifier(col.unique_name)
                dbx_type = self._sql_type(
                    col.data_type.value, col.source_type, col.unique_name,
                )
                inline_cols.append(
                    f"CAST(NULL AS {dbx_type}) AS `{safe_name}`"
                )
            if inline_cols:
                inline_select = ", ".join(inline_cols)
                lines.append("source: |")
                lines.append(f"  SELECT {inline_select}")
            else:
                # Fallback: reference the table directly
                source_for_yaml = source_fq.replace("`", "")
                lines.append(f"source: {source_for_yaml}")

        lines.append("")
        lines.append("dimensions:")

        for col in dataset.columns:
            if col.is_hidden:
                continue
            safe_name = self._sanitize_identifier(col.unique_name)
            lines.append(f"  - name: {safe_name}")
            lines.append(f'    expr: "`{safe_name}`"')

        lines.append("")
        lines.append("measures:")

        for rm in resolved_measures:
            if rm.sql_expression and rm.confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM):
                dbx_expr = rm.sql_expression.replace('"', '`')
                lines.append(f"  - name: {rm.name}")
                lines.append(f"    expr: {dbx_expr}")

        return "\n".join(lines)

    def _has_source_table_mapping(self, ds_name: str) -> bool:
        """Check if a dataset has an explicit source table mapping.

        Args:
            ds_name: Sanitized dataset name.

        Returns:
            True if the dataset has an explicit mapping in behavior config.
        """
        mapping = self._dbx_behavior.source_table_mapping
        if ds_name in mapping:
            return True
        for key in mapping:
            if key.upper() == ds_name.upper():
                return True
        return False

    def _generate_metric_view_statements(
        self,
        sml_model: SMLModel,
        model_name: str,
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """Generate native Databricks Metric View statements.

        Groups measures by dataset and creates one metric view per dataset
        containing all its dimensions and measures.

        Returns:
            Tuple of (statements, created_count, skipped_count, skipped_details).
        """
        stmts: list[str] = []
        created = 0
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []

        # Group metrics by dataset
        metrics_by_dataset: dict[str, list[SMLMetric]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if ds_name:
                metrics_by_dataset.setdefault(ds_name, []).append(metric)

        for ds_name, metrics in metrics_by_dataset.items():
            dataset = sml_model.get_dataset(metrics[0].dataset)
            if not dataset:
                for m in metrics:
                    skipped_count += 1
                    skipped_details.append({
                        "name": self._sanitize_identifier(m.unique_name),
                        "reason": DEPLOY_REASON_VALIDATION_FAILED,
                    })
                continue

            # Resolve source table
            source_fq = self._resolve_source_table(dataset)
            # Strip backticks for YAML source field
            source_for_yaml = source_fq.replace("`", "")

            # Resolve all measures for this dataset
            resolved: list[ResolvedMeasure] = []
            for metric in metrics:
                m_name = self._sanitize_identifier(metric.unique_name)

                if self._is_cross_table_measure(metric):
                    skipped_count += 1
                    skipped_details.append({
                        "name": m_name,
                        "reason": DEPLOY_REASON_CROSS_TABLE,
                    })
                    continue

                is_valid, error_reason = self._validate_measure_dependencies(metric, sml_model)
                if not is_valid:
                    skipped_count += 1
                    skipped_details.append({
                        "name": m_name,
                        "reason": DEPLOY_REASON_VALIDATION_FAILED,
                    })
                    continue

                sql_expr, translation_type = self._resolve_measure_sql(metric, dataset)
                if not sql_expr:
                    skipped_count += 1
                    skipped_details.append({
                        "name": m_name,
                        "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED,
                        "translation_type": translation_type,
                    })
                    continue

                # Determine confidence based on translation type
                confidence = self._assess_confidence(translation_type)
                resolved.append(ResolvedMeasure(
                    name=m_name,
                    sql_expression=sql_expr,
                    translation_type=translation_type,
                    confidence=confidence,
                    original_dax=(metric.expression or ""),
                ))

            deployable = [
                rm for rm in resolved
                if rm.confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM)
            ]
            if not deployable:
                continue

            # Generate YAML and wrap in CREATE VIEW
            yaml_body = self._generate_metric_view_yaml(
                sml_model, dataset, source_for_yaml, resolved,
            )
            prefix = self._sanitize_identifier(self._dbx_behavior.view_prefix or "mv")
            safe_model = self._sanitize_identifier(model_name)
            safe_ds = self._sanitize_identifier(ds_name)
            view_name = f"{prefix}_{safe_model}_{safe_ds}"
            view_fq = f"`{self.config.catalog}`.`{self.config.schema_name}`.`{view_name}`"

            view_sql = (
                f"CREATE OR REPLACE VIEW {view_fq} "
                f"WITH METRICS LANGUAGE YAML AS $$\n"
                f"{yaml_body}\n$$"
            )
            stmts.append(view_sql)
            created += 1
            logger.info(
                "📊 Generated metric view: %s (%d measures, %d dimensions)",
                view_fq,
                len(deployable),
                len([c for c in dataset.columns if not c.is_hidden]),
            )

        return stmts, created, skipped_count, skipped_details

    def _assess_confidence(self, translation_type: str) -> str:
        """Map translation type to confidence score."""
        return {
            TRANSLATION_TYPE_SQL_NATIVE: CONFIDENCE_HIGH,
            TRANSLATION_TYPE_AGGREGATION_BUILT: CONFIDENCE_HIGH,
            TRANSLATION_TYPE_DAX_TRANSLATED: CONFIDENCE_MEDIUM,
            TRANSLATION_TYPE_DAX_SKIPPED: CONFIDENCE_NONE,
        }.get(translation_type, CONFIDENCE_NONE)

    def _sql_type(self, normalized_type: str, source_type: str = "", column_name: str = "") -> str:
        # Calendar-like dimension columns do not need BIGINT width.
        if str(normalized_type or "").lower() == "integer" and self._is_small_calendar_int(column_name):
            return "INT"

        # Preserve source type when it maps cleanly to a Databricks SQL type.
        source = str(source_type or "").strip()
        preserved = self._from_source_type(source) if source else None
        if preserved:
            return preserved

        mapping = {
            "string": "STRING",
            "integer": "BIGINT",
            "decimal": "DECIMAL(38, 10)",
            "float": "DOUBLE",
            "boolean": "BOOLEAN",
            "date": "DATE",
            "datetime": "TIMESTAMP",
            "time": "TIMESTAMP",
            "binary": "BINARY",
            "variant": "STRING",
            "unknown": "STRING",
        }
        return mapping.get(str(normalized_type or "").lower(), "STRING")

    def _from_source_type(self, source_type: str) -> str | None:
        """Best-effort mapping from source native type to Databricks SQL type."""
        st = str(source_type or "").strip().upper()
        if not st:
            return None

        st = re.sub(r"\s+", " ", st)

        if st in {"INT", "INTEGER", "BIGINT", "INT64", "LONG"}:
            return "BIGINT"
        if st in {"SMALLINT", "TINYINT", "INT32", "SHORT", "BYTE"}:
            return "INT"
        if st in {"FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL"}:
            return "DOUBLE"
        if st in {"BOOLEAN", "BOOL"}:
            return "BOOLEAN"
        if st in {"DATE"}:
            return "DATE"
        if st in {"DATETIME", "DATETIME2", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIME"}:
            return "TIMESTAMP"
        if st in {"BINARY", "VARBINARY"}:
            return "BINARY"
        if st in {"STRING", "TEXT"}:
            return "STRING"

        # Keep precision/scale if provided by the source.
        if re.fullmatch(r"(DECIMAL|NUMERIC)\(\d+\s*,\s*\d+\)", st):
            return st.replace("NUMERIC", "DECIMAL")

        # VARCHAR/CHAR with optional length still maps safely to STRING.
        if re.fullmatch(r"(VAR)?CHAR(\(\d+\))?", st) or re.fullmatch(r"NVARCHAR(\(\d+\))?", st):
            return "STRING"

        return None

    def _escape_literal(self, value: str) -> str:
        return str(value or "").replace("'", "''")

    def _is_small_calendar_int(self, column_name: str) -> bool:
        normalized = str(column_name or "").strip().lower()
        normalized = re.sub(r"[^a-z0-9]", "", normalized)
        return normalized in {
            "year",
            "quarter",
            "month",
            "monthnumber",
            "monthnum",
            "day",
            "dayofmonth",
            "dayofweek",
            "week",
            "weekofyear",
        }

    def _chunk(self, items: list[str], size: int) -> list[list[str]]:
        return [items[i:i + size] for i in range(0, len(items), size)]

    def _sanitize_identifier(self, value: str) -> str:
        # Databricks object names must be alphanumeric/underscore only.
        raw = str(value or "").strip().replace('`', '')
        safe = re.sub(r"[^0-9A-Za-z_]", "_", raw)
        safe = re.sub(r"_+", "_", safe).strip("_")
        return safe or "unnamed"

    # ── Measure SQL Resolution ───────────────────────────────────────────────

    def _resolve_measure_sql(
        self,
        metric: SMLMetric,
        dataset: Optional[SMLDataset],
    ) -> tuple[Optional[str], str]:
        """Resolve the executable SQL expression for a metric.

        Priority chain:
            1. metric.sql_expression  (pre-translated SQL)
            2. Build from aggregation + source_column
            3. Simple DAX-to-SQL translation (common patterns)
            4. None (complex DAX — no view created)

        Args:
            metric: The SML metric to resolve.
            dataset: The dataset this metric belongs to (for column validation).

        Returns:
            Tuple of (sql_expression, translation_type).
            sql_expression is None if unresolvable.
        """
        # Priority 1: Pre-translated SQL expression
        sql_expr = (metric.sql_expression or "").strip()
        if sql_expr:
            return sql_expr, TRANSLATION_TYPE_SQL_NATIVE

        # Priority 2: Build from aggregation + source_column
        if metric.source_column:
            built = self._build_aggregation_sql(metric)
            if built:
                return built, TRANSLATION_TYPE_AGGREGATION_BUILT

        # Priority 3: Simple DAX-to-SQL translation (gated by feature flag)
        if self._dbx_behavior.enable_simple_dax_translation:
            dax_expr = (metric.expression or "").strip()
            if dax_expr:
                translated = self._try_simple_dax_to_sql(dax_expr)
                if translated:
                    logger.info(
                        "Translated DAX to SQL for measure '%s': %s → %s",
                        metric.unique_name,
                        dax_expr[:60],
                        translated,
                    )
                    return translated, TRANSLATION_TYPE_DAX_TRANSLATED

        # Priority 4: Skip — complex DAX, no viable translation
        logger.warning(
            "⚠️  Skipped measure '%s': no viable SQL translation (DAX too complex or translation disabled)",
            metric.unique_name,
        )
        return None, TRANSLATION_TYPE_DAX_SKIPPED

    def _try_simple_dax_to_sql(self, dax_expression: str) -> Optional[str]:
        """Translate common simple DAX patterns to Databricks SQL.

        Handles the most frequent Fabric measure patterns:
            - SUM('Table'[Column])         → SUM(`Column`)
            - AVERAGE('Table'[Column])     → AVG(`Column`)
            - COUNT('Table'[Column])       → COUNT(`Column`)
            - COUNTROWS('Table')           → COUNT(*)
            - DISTINCTCOUNT('Table'[Col])  → COUNT(DISTINCT `Col`)
            - MIN('Table'[Column])         → MIN(`Column`)
            - MAX('Table'[Column])         → MAX(`Column`)
            - COUNTBLANK('Table'[Column])  → COUNT_IF(`Column` IS NULL)

        Args:
            dax_expression: The raw DAX expression from Fabric.

        Returns:
            A Databricks SQL expression, or None if the pattern is too complex.
        """
        expr = " ".join(dax_expression.split())  # normalize whitespace

        # COUNTROWS('Table') → COUNT(*)
        if re.match(r"(?i)^COUNTROWS\(\s*'[^']+'\s*\)$", expr):
            return "COUNT(*)"

        # COUNTBLANK('Table'[Column]) → COUNT_IF(`Column` IS NULL)
        m_blank = re.match(
            r"(?i)^COUNTBLANK\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$",
            expr,
        )
        if m_blank:
            col = self._sanitize_identifier(m_blank.group(1))
            return f"COUNT_IF(`{col}` IS NULL)"

        # SUM / AVERAGE / COUNT / DISTINCTCOUNT / MIN / MAX('Table'[Column])
        m_agg = re.match(
            r"(?i)^(SUM|AVERAGE|COUNT|DISTINCTCOUNT|MIN|MAX)"
            r"\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$",
            expr,
        )
        if m_agg:
            func = m_agg.group(1).upper()
            col = self._sanitize_identifier(m_agg.group(2))

            func_map = {
                "SUM": f"SUM(`{col}`)",
                "AVERAGE": f"AVG(`{col}`)",
                "COUNT": f"COUNT(`{col}`)",
                "DISTINCTCOUNT": f"COUNT(DISTINCT `{col}`)",
                "MIN": f"MIN(`{col}`)",
                "MAX": f"MAX(`{col}`)",
            }
            return func_map.get(func)

        # Simple [Column] reference (bare column ref without aggregation)
        m_bare = re.match(r"^\[([^\]]+)\]$", expr)
        if m_bare:
            col = self._sanitize_identifier(m_bare.group(1))
            return f"`{col}`"

        # Pattern too complex for deterministic translation
        return None

    def _build_aggregation_sql(self, metric: SMLMetric) -> Optional[str]:
        """Build SQL aggregation from metric.aggregation + metric.source_column.

        Args:
            metric: The SML metric with aggregation and source_column set.

        Returns:
            A SQL aggregation expression like ``SUM(`REVENUE`)``, or None.
        """
        col = self._sanitize_identifier(metric.source_column or "")
        if not col:
            return None

        agg_map: dict[AggregationType, str] = {
            AggregationType.SUM: f"SUM(`{col}`)",
            AggregationType.COUNT: f"COUNT(`{col}`)",
            AggregationType.COUNT_DISTINCT: f"COUNT(DISTINCT `{col}`)",
            AggregationType.AVG: f"AVG(`{col}`)",
            AggregationType.MIN: f"MIN(`{col}`)",
            AggregationType.MAX: f"MAX(`{col}`)",
        }

        expr = agg_map.get(metric.aggregation)
        if expr:
            return expr

        # NONE aggregation → raw column reference (rare edge case)
        if metric.aggregation == AggregationType.NONE:
            return f"`{col}`"

        return None

    # ── Dependency Validation ────────────────────────────────────────────────

    def _validate_measure_dependencies(
        self,
        metric: SMLMetric,
        sml_model: SMLModel,
    ) -> tuple[bool, str]:
        """Check that the referenced dataset and columns exist in the model.

        Args:
            metric: The metric to validate.
            sml_model: The full SML model to search for datasets/columns.

        Returns:
            Tuple of (is_valid, error_reason). error_reason is empty on success.
        """
        dataset = sml_model.get_dataset(metric.dataset)
        if not dataset:
            return False, f"Dataset '{metric.dataset}' not found in model"

        # Validate source_column exists (for simple aggregation measures)
        if metric.source_column:
            col = dataset.get_column(metric.source_column)
            if not col:
                return False, f"Column '{metric.source_column}' not found in dataset '{metric.dataset}'"

        # Validate group_by_dimensions columns exist
        for dim_col in metric.group_by_dimensions:
            if not dataset.get_column(dim_col):
                return False, f"Group-by column '{dim_col}' not found in dataset '{metric.dataset}'"

        return True, ""

    def _is_cross_table_measure(self, metric: SMLMetric) -> bool:
        """Detect if a metric references multiple tables (not supported in v1).

        Simple heuristic: if sql_expression contains table-qualified
        references (TABLE.COLUMN patterns with distinct table prefixes),
        treat it as cross-table.
        """
        sql_expr = (metric.sql_expression or "").strip()
        if not sql_expr:
            return False

        # Find TABLE.COLUMN patterns
        refs = re.findall(r'(\w+)\.\w+', sql_expr)
        unique_tables = set(refs)
        return len(unique_tables) > 1

    # ── View Name Generation ─────────────────────────────────────────────────

    def _generate_measure_view_name(
        self,
        model_name: str,
        measure_name: str,
    ) -> str:
        """Generate a fully-qualified, sanitized view name.

        Format: ``catalog.schema.{prefix}_{model}_{measure}``

        Args:
            model_name: The sanitized model name.
            measure_name: The sanitized measure name.

        Returns:
            Fully-qualified view name with backtick quoting.
        """
        prefix = self._sanitize_identifier(self._dbx_behavior.view_prefix or "mv")
        safe_model = self._sanitize_identifier(model_name)
        safe_measure = self._sanitize_identifier(measure_name)
        view_name = f"{prefix}_{safe_model}_{safe_measure}"
        return f'`{self.config.catalog}`.`{self.config.schema_name}`.`{view_name}`'

    # ── Measure View SQL Generation ──────────────────────────────────────────

    def generate_measure_view_statements(
        self,
        sml_model: SMLModel,
        view_type_override: str = "",
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """Generate SQL statements for measure views.

        Supports two technologies:
            - Native metric views (WITH METRICS LANGUAGE YAML)
            - Plain SQL views (CREATE OR REPLACE VIEW ... AS SELECT)

        Args:
            sml_model: The SML model containing metrics.
            view_type_override: Force a specific view type (for testing).

        Returns:
            Tuple of:
                - List of SQL statements
                - Count of successfully generated views
                - Count of skipped measures
                - List of skipped measure dicts (name, reason) for metadata rows
        """
        if not self._dbx_behavior.create_measure_views:
            logger.info("Measure view creation disabled via behavior config")
            return [], 0, 0, []

        model_name = self._sanitize_identifier(sml_model.unique_name)

        # Determine view technology
        vtype = view_type_override or self._dbx_behavior.measure_view_type.strip().lower()
        if vtype == "none":
            return [], 0, 0, []

        # Native metric view path
        if vtype == "metric_view":
            return self._generate_metric_view_statements(sml_model, model_name)

        # SQL view path (per_measure or combined)
        view_mode = self._dbx_behavior.measure_view_mode
        if view_mode == "combined":
            return self._generate_combined_views(sml_model, model_name)
        return self._generate_per_measure_views(sml_model, model_name)

    def _generate_per_measure_views(
        self,
        sml_model: SMLModel,
        model_name: str,
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """One view per measure (default mode)."""
        stmts: list[str] = []
        created = 0
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []

        for metric in sml_model.metrics:
            measure_name = self._sanitize_identifier(metric.unique_name)
            dataset = sml_model.get_dataset(metric.dataset)

            # Cross-table check
            if self._is_cross_table_measure(metric):
                logger.warning(
                    "Skipped measure view '%s': cross-table measures not supported in v1",
                    metric.unique_name,
                )
                skipped_count += 1
                skipped_details.append({
                    "name": measure_name,
                    "reason": DEPLOY_REASON_CROSS_TABLE,
                })
                continue

            # Dependency validation
            is_valid, error_reason = self._validate_measure_dependencies(metric, sml_model)
            if not is_valid:
                logger.warning(
                    "Skipped measure view '%s': %s",
                    metric.unique_name,
                    error_reason,
                )
                skipped_count += 1
                skipped_details.append({
                    "name": measure_name,
                    "reason": DEPLOY_REASON_VALIDATION_FAILED,
                })
                continue

            # Resolve SQL expression
            sql_expr, translation_type = self._resolve_measure_sql(metric, dataset)
            if not sql_expr:
                skipped_count += 1
                skipped_details.append({
                    "name": measure_name,
                    "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED,
                    "translation_type": translation_type,
                })
                continue

            # Build the view SQL
            view_fq = self._generate_measure_view_name(model_name, measure_name)
            source_table = self._fq_name(
                self._sanitize_identifier(dataset.source_table or dataset.unique_name)
            )

            # GROUP BY: only explicit dimensions, or pure aggregate
            group_by_cols = self._resolve_group_by_columns(metric, dataset)

            select_parts: list[str] = []
            if group_by_cols:
                for gc in group_by_cols:
                    select_parts.append(f"    `{gc}`")
            select_parts.append(f"    {sql_expr} AS `{measure_name}`")

            select_clause = ",\n".join(select_parts)
            view_sql = f"CREATE OR REPLACE VIEW {view_fq} AS\nSELECT\n{select_clause}\nFROM {source_table}"

            if group_by_cols:
                group_clause = ", ".join(f"`{gc}`" for gc in group_by_cols)
                view_sql += f"\nGROUP BY {group_clause}"

            stmts.append(view_sql)
            created += 1
            logger.info("Created measure view: %s", view_fq)

        return stmts, created, skipped_count, skipped_details

    def _generate_combined_views(
        self,
        sml_model: SMLModel,
        model_name: str,
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """One view per dataset containing all its measures (combined mode)."""
        stmts: list[str] = []
        created = 0
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []

        # Group metrics by dataset
        metrics_by_dataset: dict[str, list[SMLMetric]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if not ds_name:
                continue
            metrics_by_dataset.setdefault(ds_name, []).append(metric)

        for ds_name, metrics in metrics_by_dataset.items():
            dataset = sml_model.get_dataset(metrics[0].dataset)
            if not dataset:
                for m in metrics:
                    skipped_count += 1
                    skipped_details.append({
                        "name": self._sanitize_identifier(m.unique_name),
                        "reason": DEPLOY_REASON_VALIDATION_FAILED,
                    })
                continue

            source_table = self._fq_name(
                self._sanitize_identifier(dataset.source_table or dataset.unique_name)
            )

            measure_expressions: list[str] = []
            combined_group_by: set[str] = set()

            for metric in metrics:
                measure_name = self._sanitize_identifier(metric.unique_name)

                if self._is_cross_table_measure(metric):
                    skipped_count += 1
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_CROSS_TABLE,
                    })
                    continue

                is_valid, error_reason = self._validate_measure_dependencies(metric, sml_model)
                if not is_valid:
                    skipped_count += 1
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_VALIDATION_FAILED,
                    })
                    continue

                sql_expr, translation_type = self._resolve_measure_sql(metric, dataset)
                if not sql_expr:
                    skipped_count += 1
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED,
                        "translation_type": translation_type,
                    })
                    continue

                measure_expressions.append(f"    {sql_expr} AS `{measure_name}`")
                group_cols = self._resolve_group_by_columns(metric, dataset)
                combined_group_by.update(group_cols)

            if not measure_expressions:
                continue

            # Build combined view
            prefix = self._sanitize_identifier(self._dbx_behavior.view_prefix or "mv")
            safe_model = self._sanitize_identifier(model_name)
            safe_ds = self._sanitize_identifier(ds_name)
            view_name = f"{prefix}_{safe_model}_{safe_ds}_measures"
            view_fq = f'`{self.config.catalog}`.`{self.config.schema_name}`.`{view_name}`'

            select_parts: list[str] = []
            sorted_group_by = sorted(combined_group_by)
            for gc in sorted_group_by:
                select_parts.append(f"    `{gc}`")
            select_parts.extend(measure_expressions)

            select_clause = ",\n".join(select_parts)
            view_sql = f"CREATE OR REPLACE VIEW {view_fq} AS\nSELECT\n{select_clause}\nFROM {source_table}"

            if sorted_group_by:
                group_clause = ", ".join(f"`{gc}`" for gc in sorted_group_by)
                view_sql += f"\nGROUP BY {group_clause}"

            stmts.append(view_sql)
            created += 1
            logger.info("Created combined measure view: %s", view_fq)

        return stmts, created, skipped_count, skipped_details

    def _resolve_group_by_columns(
        self,
        metric: SMLMetric,
        dataset: Optional[SMLDataset],
    ) -> list[str]:
        """Resolve GROUP BY columns for a measure view.

        Logic:
            - If metric.group_by_dimensions is set → use those explicitly
            - Otherwise → no GROUP BY (pure aggregate)

        Args:
            metric: The metric to check.
            dataset: The parent dataset.

        Returns:
            List of sanitized column names for GROUP BY, or empty list.
        """
        if metric.group_by_dimensions:
            return [
                self._sanitize_identifier(dim)
                for dim in metric.group_by_dimensions
                if dim
            ]
        # Pure aggregate — no GROUP BY
        return []

    # ── Metadata Table Generation (Enhanced) ─────────────────────────────────

    def generate_sql_statements(
        self,
        sml_model: SMLModel,
        view_type_override: str = "",
    ) -> list[str]:
        """Generate Databricks SQL statements from SML model metadata.

        Produces:
            1. Metadata table with deploy_status/deploy_reason columns
            2. Measure views (metric or SQL based on config/probe)

        Args:
            sml_model: The SML model to generate statements for.
            view_type_override: Resolved view type from publish() probe.
        """
        stmts: list[str] = [
            f"CREATE SCHEMA IF NOT EXISTS `{self.config.catalog}`.`{self.config.schema_name}`",
        ]

        model_name = self._sanitize_identifier(sml_model.unique_name)
        model_name_lit = self._escape_literal(model_name)
        model_table = self._fq_name(model_name)

        # Drop + recreate to handle schema evolution (old tables may lack
        # deploy_status/deploy_reason columns).  This is safe because each
        # model gets its own table and all rows are repopulated each deploy.
        stmts.append(f"DROP TABLE IF EXISTS {model_table}")
        stmts.append(
            (
                f"CREATE TABLE {model_table} ("
                "model_name STRING, dataset_name STRING, object_name STRING, "
                "object_kind STRING, data_type STRING, source_data_type STRING, "
                "expression STRING, deploy_status STRING, deploy_reason STRING, "
                "translation_type STRING, "
                "updated_at TIMESTAMP"
                ") USING DELTA"
            )
        )


        # Generate measure views first to collect deployment status
        view_stmts, views_created, views_skipped, skipped_details = \
            self.generate_measure_view_statements(sml_model, view_type_override)

        # Build lookup for skipped measure details
        skipped_lookup: dict[str, str] = {
            d["name"]: d["reason"] for d in skipped_details
        }

        # Build set of measures that got views + their translation types
        deployed_measures: dict[str, str] = {}  # name -> translation_type
        for metric in sml_model.metrics:
            m_name = self._sanitize_identifier(metric.unique_name)
            if m_name not in skipped_lookup:
                # Check if it could have been deployed (has valid SQL)
                dataset = sml_model.get_dataset(metric.dataset)
                sql_expr, translation_type = self._resolve_measure_sql(metric, dataset)
                if sql_expr:
                    deployed_measures[m_name] = translation_type

        metrics_by_dataset: dict[str, list[Any]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if not ds_name:
                continue
            metrics_by_dataset.setdefault(ds_name, []).append(metric)

        # Build lookup from skipped details for translation type
        skipped_translation_types: dict[str, str] = {
            d["name"]: d.get("translation_type", TRANSLATION_TYPE_DAX_SKIPPED)
            for d in skipped_details
        }

        row_values: list[str] = []

        # Store model structure in one model-named table.
        for ds in sml_model.datasets:
            dataset_name = self._sanitize_identifier(ds.unique_name)
            if not dataset_name:
                continue

            for col in ds.columns:
                col_name = self._sanitize_identifier(col.unique_name)
                if not col_name:
                    continue
                c_type = self._sql_type(col.data_type.value, col.source_type, col.unique_name)
                source_type = self._escape_literal(col.source_type or col.data_type.value or "")
                row_values.append(
                    (
                        f"('{model_name_lit}', '{self._escape_literal(dataset_name)}', '{self._escape_literal(col_name)}', "
                        f"'dimension', '{self._escape_literal(c_type)}', '{source_type}', NULL, "
                        f"NULL, NULL, NULL, current_timestamp())"
                    )
                )

            for metric in metrics_by_dataset.get(dataset_name, []):
                m_name = self._sanitize_identifier(metric.unique_name)
                if not m_name:
                    continue
                expr = self._escape_literal(metric.sql_expression or metric.expression or "")

                # Determine deploy status for this measure
                if m_name in deployed_measures:
                    deploy_status = DEPLOY_STATUS_DEPLOYED
                    deploy_reason = "NULL"
                    t_type = f"'{deployed_measures[m_name]}'"
                elif m_name in skipped_lookup:
                    deploy_status = DEPLOY_STATUS_NOT_DEPLOYED
                    deploy_reason = f"'{self._escape_literal(skipped_lookup[m_name])}'"
                    t_type = f"'{skipped_translation_types.get(m_name, TRANSLATION_TYPE_DAX_SKIPPED)}'"
                else:
                    deploy_status = DEPLOY_STATUS_NOT_DEPLOYED
                    deploy_reason = f"'{DEPLOY_REASON_DAX_NOT_SUPPORTED}'"
                    t_type = f"'{TRANSLATION_TYPE_DAX_SKIPPED}'"

                row_values.append(
                    (
                        f"('{model_name_lit}', '{self._escape_literal(dataset_name)}', '{self._escape_literal(m_name)}', "
                        f"'measure', 'DOUBLE', '', '{expr}', "
                        f"'{deploy_status}', {deploy_reason}, {t_type}, current_timestamp())"
                    )
                )

        # Keep statement size controlled while minimizing Databricks API round trips.
        for value_chunk in self._chunk(row_values, 500):
            stmts.append(
                (
                    f"INSERT INTO {model_table} (model_name, dataset_name, object_name, object_kind, data_type, source_data_type, expression, deploy_status, deploy_reason, translation_type, updated_at) "
                    f"VALUES {', '.join(value_chunk)}"
                )
            )

        # Append measure view statements after metadata table operations
        stmts.extend(view_stmts)

        return stmts

    # ── Network Execution ────────────────────────────────────────────────────

    def execute_statements(self, statements: list[str]) -> list[dict[str, Any]]:
        """Execute SQL statements via Databricks SQL Statements API.
        
        Includes built-in resilience for interactive MSAL tokens: if Databricks
        returns a 401 Unauthorized (e.g. token expired sooner than expected or
        revoked), it forces a silent refresh and retries exactly once.
        """
        endpoint = f"{self.config.api_base_url}/api/2.0/sql/statements"
        results: list[dict[str, Any]] = []

        for sql in statements:
            payload = {
                "statement": sql,
                "warehouse_id": self.config.warehouse_id,
                "wait_timeout": "30s",
            }
            
            # Request wrapper with 401 recovery
            def _fire_request(is_retry: bool = False) -> dict[str, Any]:
                headers = self._headers()
                resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
                
                # If 401, handle token refresh explicitly
                if resp.status_code == 401 and not is_retry:
                    auth_type = getattr(self.config, "auth_type", "pat")
                    if auth_type == "interactive":
                        logger.warning("Databricks API returned 401 Unauthorized. Forcing MSAL token refresh...")
                        from semabridge.repository.credential_manager import CredentialManager
                        cm = CredentialManager()
                        if cm.refresh_databricks_token():
                            logger.info("Token refresh successful. Retrying Databricks API call...")
                            return _fire_request(is_retry=True)
                        else:
                            raise DatabricksPublishError("Databricks session expired and automatic refresh failed. Please sign in again.")

                if resp.status_code >= 400:
                    raise DatabricksPublishError(
                        f"Databricks statement failed ({resp.status_code}): {resp.text[:500]}"
                    )

                data = resp.json()
                state = (data.get("status") or {}).get("state")
                if state and state not in {"SUCCEEDED"}:
                    err = data.get("status", {}).get("error") or "unknown error"
                    raise DatabricksPublishError(f"Databricks statement state={state}: {err}")

                return data

            results.append(_fire_request())

        return results

    def publish(self, sml_model: SMLModel) -> str:
        """Generate and execute Databricks statements.

        Three-phase deployment:
            1. Probe runtime for metric view support (if auto)
            2. Deploy metadata table (mandatory)
            3. Deploy measure views (best-effort, non-fatal)

        Returns:
            A URI identifying the deployed artifact.
        """
        # Phase 0: Resolve view type (probe runtime if "auto")
        resolved_view_type = self._determine_view_type()
        logger.info(
            "📊 View technology resolved: %s (config=%s)",
            resolved_view_type,
            self._dbx_behavior.measure_view_type,
        )

        # Generate all statements with the resolved view type
        statements = self.generate_sql_statements(
            sml_model, view_type_override=resolved_view_type,
        )

        # Split into metadata (mandatory) and view (best-effort) statements
        metadata_stmts = [
            s for s in statements
            if not s.strip().startswith("CREATE OR REPLACE VIEW")
        ]
        view_stmts = [
            s for s in statements
            if s.strip().startswith("CREATE OR REPLACE VIEW")
        ]

        logger.info(
            "Publishing semantic model to Databricks: model=%s "
            "metadata_statements=%s measure_views=%s view_type=%s",
            sml_model.unique_name,
            len(metadata_stmts),
            len(view_stmts),
            resolved_view_type,
        )

        # Phase 1: Metadata table — mandatory, fails the deploy if broken
        self.execute_statements(metadata_stmts)
        logger.info(
            "✅ Metadata table deployed successfully for model '%s'",
            sml_model.unique_name,
        )

        # Phase 2: Measure views — best-effort, non-fatal
        views_success = 0
        views_failed = 0
        for view_sql in view_stmts:
            # Extract view name for logging
            view_name = "unknown"
            match = re.search(r'VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)', view_sql)
            if match:
                view_name = match.group(1)

            try:
                self.execute_statements([view_sql])
                views_success += 1
                logger.info("✅ Deployed measure view: %s", view_name)
            except DatabricksPublishError as exc:
                views_failed += 1
                logger.warning(
                    "⚠️  Measure view %s skipped — source table may not exist "
                    "in Databricks. Error: %s",
                    view_name,
                    str(exc)[:200],
                )

        if views_failed:
            logger.warning(
                "⚠️  %d/%d measure views failed (source tables not found in "
                "Databricks). Metadata table deployed OK. Create the source "
                "tables in Databricks to enable measure views.",
                views_failed,
                len(view_stmts),
            )

        return f"databricks://{self.config.catalog}/{self.config.schema_name}/{sml_model.unique_name}"

