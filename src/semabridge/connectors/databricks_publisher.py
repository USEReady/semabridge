"""
Databricks Publisher.

Publishes SML models to Databricks SQL Warehouse using the Statements API.

Deployment produces three layers:
1. **Metadata table** (Delta) — introspection catalog of all model objects.
2. **Metric views** (native) — Unity Catalog WITH METRICS LANGUAGE YAML (preferred).
3. **SQL views** (fallback) — plain CREATE OR REPLACE VIEW for older runtimes.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.connectors.databricks_measure_translation import DatabricksMeasureTranslator
from semabridge.connectors.schema_reconciler import SchemaMapper
from semabridge.sml.models import AggregationType, SMLDataset, SMLMetric, SMLModel
from semabridge.utils.naming import to_alias
from semabridge.utils.join_builder import JoinTreeBuilder
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class DatabricksPublishError(Exception):
    """Raised when Databricks publish fails."""


class DatabricksSessionPool:
    """Connection pool with retry strategy for Databricks API calls."""
    
    def __init__(self, max_retries: int = 3, backoff_factor: float = 0.5):
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
    
    def post(self, *args, **kwargs):
        """POST with connection pooling."""
        return self.session.post(*args, **kwargs)
    
    def close(self):
        """Close session."""
        self.session.close()


DEFAULT_DATABRICKS_SCHEMA_OBJECT_LIMIT = 500


# ── Deploy Status Constants ──────────────────────────────────────────────────
DEPLOY_STATUS_DEPLOYED = "DEPLOYED"
DEPLOY_STATUS_NOT_DEPLOYED = "NOT_DEPLOYED"
DEPLOY_STATUS_SKIPPED = "SKIPPED"
DEPLOY_STATUS_PLAN_ONLY = "PLAN_ONLY"

DEPLOY_REASON_DAX_NOT_SUPPORTED = "DAX_NOT_SUPPORTED"
DEPLOY_REASON_VALIDATION_FAILED = "VALIDATION_FAILED"
DEPLOY_REASON_CROSS_TABLE = "CROSS_TABLE_NOT_SUPPORTED"
DEPLOY_REASON_PREREQUISITE_MISSING = "PREREQUISITE_MISSING"
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

DRAFT_MEASURE_SQL = "CAST(NULL AS DOUBLE)"

# ── View Technology ──────────────────────────────────────────────────────────
VIEW_TYPE_METRIC = "metric_view"
VIEW_TYPE_MATERIALIZED = "materialized_view"
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


@dataclass(frozen=True)
class MetricViewColumnBinding:
    """Projection metadata for a Databricks metric-view source column."""
    semantic_name: str
    projected_name: str
    source_column: str
    include_as_dimension: bool = True


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
        self._session_pool = DatabricksSessionPool(max_retries=3)
        self._measure_translator = DatabricksMeasureTranslator(
            behavior=self._dbx_behavior,
            sanitize_identifier=self._sanitize_identifier,
            build_aggregation_sql=self._build_aggregation_sql,
            distinct_count_expression=lambda column: f"COUNT(DISTINCT {column})",
        )

        # Initialize network pooling and thread-safe OAuth recovery
        self.session = requests.Session()
        self._auth_lock = threading.Lock()

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

        Resolution priority for **interactive** mode:
            1. ``config.token`` (already injected by ``scoped_account_env``
               when running in multi-account mode).
            2. Fall back to global ``CredentialManager`` for single-account
               legacy flows.

        For ``service_principal``: exchanges ``client_credentials``.
        For ``pat``: uses ``config.token`` directly.

        Returns:
            A valid access token string.

        Raises:
            DatabricksPublishError: If no valid token can be obtained.
        """
        auth_type = getattr(self.config, "auth_type", "pat") or "pat"

        if auth_type == "interactive":
            # Multi-account path: scoped_account_env already resolved and
            # injected the correct token into DATABRICKS_TOKEN → config.token.
            if self.config.token is not None:
                scoped_token = self.config.token.get_secret_value()
                if scoped_token:
                    return scoped_token

            # Legacy single-account path: read from global CredentialManager
            from semabridge.repository.credential_manager import CredentialManager
            cm = CredentialManager()
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

    def _resolve_existing_source_for_dataset(self, dataset: SMLDataset, expected_source: str) -> str | None:
        """Return an existing source table for a dataset if one can be resolved."""
        existing = self._resolve_existing_source_table(dataset, expected_source)
        if existing:
            return existing

        fallback_source = self._fq_name(self._sanitize_identifier(dataset.unique_name))
        if fallback_source != expected_source:
            existing = self._resolve_existing_source_table(dataset, fallback_source)
            if existing:
                logger.info(
                    "Source table for dataset '%s' resolved via dataset-name fallback: %s",
                    dataset.unique_name,
                    existing,
                )
                return existing

        return None

    def _resolve_existing_source_table(self, dataset: SMLDataset, expected_source: str) -> str | None:
        """Return the source table reference only if it exists in Databricks."""
        if not expected_source:
            return None
        exists = self._check_source_table_exists(expected_source)
        if exists is False:
            discovered = self._discover_source_table_schema(expected_source)
            if discovered:
                logger.info(
                    "Source table for dataset '%s' discovered in alternate schema: %s",
                    dataset.unique_name,
                    discovered,
                )
            return discovered
        return expected_source

    def _expected_dataset_source_columns(self, dataset: SMLDataset) -> list[str]:
        """Return the semantic dataset columns expected to exist physically."""
        expected: list[str] = []
        seen: set[str] = set()

        for col in dataset.columns:
            semantic_name = str(col.unique_name or "").strip()
            if not semantic_name:
                continue
            normalized = self._sanitize_identifier(semantic_name).lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            expected.append(semantic_name)

        return expected

    def _find_missing_dataset_columns(
        self,
        dataset: SMLDataset,
        actual_columns: set[str],
    ) -> list[str]:
        """Return semantic columns with no matching physical Databricks column."""
        missing: list[str] = []

        for semantic_name in self._expected_dataset_source_columns(dataset):
            candidates = self._physical_source_column_candidates(dataset, semantic_name)
            configured = self._resolve_configured_source_column(dataset, semantic_name)
            if configured:
                candidates = [configured, *candidates]
            base = self._infer_physical_source_column_name(semantic_name)
            if base:
                candidates = [base, *candidates]

            if any(candidate in actual_columns for candidate in candidates):
                continue
            missing.append(semantic_name)

        return missing

    def _resolve_configured_source_column(self, dataset: SMLDataset, semantic_col: str) -> str:
        """Resolve explicit source-column mapping overrides from behavior config.

        Supported config formats:
            source_column_mapping:
              Fact:
                Customer Key: customer_key
              Customer.Customer: name
        """
        mapping = getattr(self._dbx_behavior, "source_column_mapping", {}) or {}
        if not isinstance(mapping, dict) or not mapping:
            return ""

        def _dataset_key_candidates(raw_name: str) -> set[str]:
            """Return normalized aliases for robust dataset-key matching.

            Handles model-qualified names (for example, "FabricModel - Customer")
            and fully-qualified source tables (for example, "semabridge.public.customer").
            """
            text = str(raw_name or "").strip()
            if not text:
                return set()

            aliases: set[str] = set()
            sanitized = self._sanitize_identifier(text).lower()
            if sanitized:
                aliases.add(sanitized)

            # Last segment of fully-qualified names: a.b.customer -> customer
            if "." in text:
                dot_tail = text.split(".")[-1].strip()
                dot_tail_sanitized = self._sanitize_identifier(dot_tail).lower()
                if dot_tail_sanitized:
                    aliases.add(dot_tail_sanitized)

            # Last token of model-qualified names: FabricModel_Customer -> customer
            if "_" in sanitized:
                tail = sanitized.split("_")[-1].strip()
                if tail:
                    aliases.add(tail)

            return aliases

        dataset_candidates: set[str] = set()
        dataset_candidates.update(_dataset_key_candidates(dataset.unique_name))
        dataset_candidates.update(_dataset_key_candidates(dataset.source_table or dataset.unique_name))
        target_col = self._sanitize_identifier(semantic_col).lower()

        def _normalize_physical(value: Any) -> str:
            return self._infer_physical_source_column_name(str(value or "")).lower()

        for key, value in mapping.items():
            key_str = str(key or "").strip()
            if not key_str:
                continue

            if isinstance(value, dict):
                dataset_key = self._sanitize_identifier(key_str).lower()
                if dataset_key not in dataset_candidates:
                    continue
                for semantic_name, physical_name in value.items():
                    semantic_key = self._sanitize_identifier(str(semantic_name or "")).lower()
                    if semantic_key == target_col:
                        return _normalize_physical(physical_name)
                continue

            if isinstance(value, str) and "." in key_str:
                ds_name, sem_col = key_str.split(".", 1)
                ds_key = self._sanitize_identifier(ds_name).lower()
                sem_key = self._sanitize_identifier(sem_col).lower()
                if ds_key in dataset_candidates and sem_key == target_col:
                    return _normalize_physical(value)

        return ""

    def _validate_source_table_schema(self, sml_model: SMLModel) -> None:
        """Fail fast when Databricks source tables are present but schema-mismatched.

        This validation is best-effort:
            - If Databricks returns column metadata, missing semantic columns abort publish.
            - If metadata cannot be introspected, validation is skipped for that dataset.
        """
        if not self._dbx_behavior.create_measure_views:
            return

        validation_errors: list[str] = []

        for dataset in sml_model.datasets:
            expected_source = self._resolve_source_table(dataset)
            source_table = self._resolve_existing_source_for_dataset(dataset, expected_source)
            if not source_table:
                continue

            actual_columns = self._get_source_table_columns(source_table)
            if not actual_columns:
                logger.info(
                    "Skipping Databricks schema validation for dataset '%s': source columns could not be introspected for %s",
                    dataset.unique_name,
                    source_table,
                )
                continue

            missing_columns = self._find_missing_dataset_columns(dataset, actual_columns)
            if missing_columns:
                validation_errors.append(
                    (
                        f"Dataset '{dataset.unique_name}' -> {source_table} is missing "
                        f"{len(missing_columns)} required columns: {', '.join(missing_columns[:12])}"
                        + (" ..." if len(missing_columns) > 12 else "")
                        + f". Available columns: {', '.join(sorted(actual_columns)[:20])}"
                        + (" ..." if len(actual_columns) > 20 else "")
                    )
                )

        if validation_errors:
            message = (
                "Schema Mismatch: Databricks source schema does not match the semantic model. "
                + " | ".join(validation_errors)
            )

            # Databricks publish already handles per-view skips and fallbacks.
            # Keep schema mismatch as a hard stop only when the deployment is
            # explicitly configured to fail on missing source prerequisites.
            if str(self._dbx_behavior.on_missing_source or "plan").strip().lower() == "fail":
                raise DatabricksPublishError(message)

            logger.warning(message)

    def _missing_source_columns_for_dataset(
        self,
        dataset: SMLDataset,
        source_fq: str,
    ) -> list[str]:
        """Return semantic columns that are absent from a physical Databricks source."""
        actual_columns = self._get_source_table_columns(source_fq)
        if not actual_columns:
            return []
        return self._find_missing_dataset_columns(dataset, actual_columns)

    def _reconcile_dataset_schema(
        self,
        dataset: SMLDataset,
        source_fq: str,
    ) -> tuple[dict[str, str], list[str]]:
        """Reconcile semantic columns to physical source columns using SchemaMapper.

        Returns:
            (mapped_columns, missing_columns) keyed by semantic column name.

        Raises:
            DatabricksPublishError when behavior.on_missing_source == "fail"
            and missing prerequisites are detected.
        """
        actual_columns = self._get_source_table_columns(source_fq)
        if not actual_columns:
            # Introspection unavailable; keep current behavior and avoid false fails.
            return {}, []

        expected_columns = self._expected_dataset_source_columns(dataset)

        # Apply explicit source_column_mapping overrides first so fail-fast
        # validation does not incorrectly flag mapped semantic columns as missing.
        mapped: dict[str, str] = {}
        unresolved_expected: list[str] = []
        for semantic_name in expected_columns:
            configured = self._resolve_configured_source_column(dataset, semantic_name)
            if configured and configured in actual_columns:
                mapped[semantic_name] = configured
            else:
                unresolved_expected.append(semantic_name)

        mapper = SchemaMapper(
            expected_columns=unresolved_expected,
            available_columns=sorted(actual_columns),
        )
        auto_mapped, missing = mapper.build_mapping()
        mapped.update(auto_mapped)

        if missing and str(self._dbx_behavior.on_missing_source or "plan").strip().lower() == "fail":
            details = {
                "error": "MISSING_PREREQUISITE",
                "source_table": source_fq,
                "expected_columns": expected_columns,
                "available_columns": sorted(actual_columns),
                "missing_columns": [
                    {
                        "expected_column": col,
                        "normalized_expected": self._infer_physical_source_column_name(col),
                        "recommendation": (
                            f"Add source column '{self._infer_physical_source_column_name(col)}' "
                            f"to {source_fq} or define source_column_mapping for '{dataset.unique_name}.{col}'."
                        ),
                    }
                    for col in missing
                ],
            }
            raise DatabricksPublishError(
                "Databricks source prerequisites missing: "
                f"{details}"
            )

        if missing:
            logger.warning(
                "Schema reconciliation fallback for dataset '%s': injecting NULL for missing columns: %s",
                dataset.unique_name,
                ", ".join(missing[:12]) + (" ..." if len(missing) > 12 else ""),
            )

        return mapped, missing

    def _build_reconciled_sql_source_relation(
        self,
        dataset: SMLDataset,
        source_fq: str,
    ) -> str:
        """Build a SQL source relation with column reconciliation.

        Projects physical columns to deterministic aliases (sanitized semantic
        names) and injects NULL for genuinely missing columns.
        """
        mapped, missing = self._reconcile_dataset_schema(dataset, source_fq)
        if not mapped and not missing:
            return source_fq

        expected_columns = self._expected_dataset_source_columns(dataset)
        used_aliases: set[str] = set()
        select_parts: list[str] = []

        for semantic_name in expected_columns:
            alias_name = self._infer_physical_source_column_name(semantic_name)
            if not alias_name:
                alias_name = self._sanitize_identifier(semantic_name)
            alias_key = alias_name.lower()
            if alias_key in used_aliases:
                continue
            used_aliases.add(alias_key)

            source_col = mapped.get(semantic_name)
            if source_col:
                select_parts.append(f"  `{source_col}` AS `{alias_name}`")
            else:
                select_parts.append(f"  NULL AS `{alias_name}`")

        if not select_parts:
            return source_fq

        select_sql = ",\n".join(select_parts)
        return (
            "(\n"
            "SELECT\n"
            f"{select_sql}\n"
            f"FROM {source_fq}\n"
            ") AS `semabridge_src`"
        )

    def _build_inline_null_source_relation(self, source_fq: str, column_names: list[str]) -> str:
        """Build a source relation that projects typed NULL placeholders."""
        deduped: list[str] = []
        seen: set[str] = set()
        for column_name in column_names:
            safe_name = self._sanitize_identifier(column_name)
            if not safe_name:
                continue
            key = safe_name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(safe_name)

        if not deduped:
            return source_fq

        select_list = ",\n".join(f"  CAST(NULL AS DOUBLE) AS `{name}`" for name in deduped)
        return (
            "(\n"
            "SELECT\n"
            f"{select_list}\n"
            f"FROM {source_fq}\n"
            ") AS `semabridge_src`"
        )

    def _discover_source_table_schema(self, source_table: str) -> str | None:
        """Find a table with the same name in an alternate schema in the catalog."""
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return None

        catalog, schema_name, table_name = parsed
        stmt = (
            f"SELECT LOWER(table_schema) AS table_schema FROM {catalog}.information_schema.tables "
            f"WHERE LOWER(table_name) = LOWER('{table_name}') ORDER BY table_schema"
        )

        try:
            rows = self.execute_statements([stmt])
        except Exception:
            return None

        if not rows:
            return None

        payload = rows[0] if isinstance(rows[0], dict) else {}
        result_block = payload.get("result") if isinstance(payload, dict) else {}
        data_array = result_block.get("data_array") if isinstance(result_block, dict) else None
        if not data_array:
            return None

        expected_schema = str(schema_name or "").strip().lower()
        discovered_schemas: list[str] = []
        for row in data_array:
            if isinstance(row, list) and row:
                candidate = str(row[0]).strip().lower()
                if re.fullmatch(r"[a-z_][a-z0-9_]*", candidate):
                    discovered_schemas.append(candidate)

        if not discovered_schemas:
            return None

        # Prefer a non-default alternate schema when available.
        chosen_schema = discovered_schemas[0]
        for candidate in discovered_schemas:
            if candidate and candidate != expected_schema:
                chosen_schema = candidate
                break

        return f"`{catalog}`.`{chosen_schema}`.`{table_name}`"

    def _parse_table_reference(self, source_table: str) -> tuple[str, str, str] | None:
        """Parse a Databricks table reference into catalog, schema, and table."""
        raw = str(source_table or "").strip().replace("`", "")
        if not raw:
            return None
        parts = [part for part in raw.split(".") if part]
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        if len(parts) == 2:
            return self.config.catalog, parts[0], parts[1]
        if len(parts) == 1:
            return self.config.catalog, self.config.schema_name, parts[0]
        return None

    def _get_source_table_columns(self, source_table: str) -> set[str]:
        """Best-effort introspection of physical columns for a source table."""
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return set()

        catalog, schema_name, table_name = parsed
        # Use native Databricks SHOW COLUMNS metadata command to bypass Serverless Information Schema locks
        stmt = f"SHOW COLUMNS IN `{catalog}`.`{schema_name}`.`{table_name}`"

        try:
            rows = self.execute_statements([stmt])
        except Exception:
            return set()

        if not rows:
            return set()

        payload = rows[0] if isinstance(rows[0], dict) else {}
        result_block = payload.get("result") if isinstance(payload, dict) else {}
        data_array = result_block.get("data_array") if isinstance(result_block, dict) else None
        if not data_array:
            return set()

        columns: set[str] = set()
        for row in data_array:
            if isinstance(row, list) and row:
                columns.add(str(row[0]).strip().lower())
        return columns

    def _physical_source_column_candidates(self, dataset: SMLDataset, semantic_col: str) -> list[str]:
        """Return likely physical column names for a semantic column."""
        base = self._infer_physical_source_column_name(semantic_col)
        dataset_base = self._infer_physical_source_column_name(dataset.unique_name)

        candidates: list[str] = []
        if base:
            candidates.append(base)
            if dataset_base and base != dataset_base:
                candidates.append(f"{dataset_base}_{base}")
            if base.endswith("_key"):
                stem = base[:-4].rstrip("_")
                candidates.append(stem)
                if dataset_base and stem:
                    candidates.append(f"{dataset_base}_{stem}")
            elif base.endswith("_id"):
                stem = base[:-3].rstrip("_")
                candidates.append(stem)
                if dataset_base and stem:
                    candidates.append(f"{dataset_base}_{stem}")
            else:
                candidates.append(f"{base}_key")
                candidates.append(f"{base}_id")
                if dataset_base and base != dataset_base:
                    candidates.append(f"{dataset_base}_{base}_key")
                    candidates.append(f"{dataset_base}_{base}_id")

        if dataset_base and base == dataset_base:
            candidates.append(f"{dataset_base}_key")
            candidates.append(f"{dataset_base}_id")

        deduped: list[str] = []
        seen: set[str] = set()
        for cand in candidates:
            key = str(cand or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        return deduped

    def _resolve_physical_source_column(self, dataset: SMLDataset, semantic_col: str, source_fq: str) -> str:
        """Resolve a semantic column name to the best matching physical column."""
        configured = self._resolve_configured_source_column(dataset, semantic_col)
        if configured:
            return configured

        base = self._infer_physical_source_column_name(semantic_col)
        candidates = self._physical_source_column_candidates(dataset, semantic_col)

        physical_cols = self._get_source_table_columns(source_fq)
        if physical_cols:
            for cand in candidates:
                if cand in physical_cols:
                    return cand
            for cand in candidates:
                for physical in physical_cols:
                    if physical.endswith(f"_{cand}") or physical.startswith(f"{cand}_"):
                        return physical

        # If Databricks metadata cannot confirm columns, keep the semantic base
        # name instead of forcing a synthetic *_key variant.
        return base or (candidates[0] if candidates else base)

    def _check_source_table_exists(self, source_table: str) -> bool | None:
        """Best-effort existence check for a source table reference.

        Returns:
            True when the table is definitively present,
            False when definitively missing,
            None when the check could not be completed.
        """
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return None

        catalog, schema_name, table_name = parsed
        
        # O(1) Preload Schema Cache (Eliminates 15 min N+1 sync blocks & case-sensitivity flaws!)
        cache_key = f"{catalog}.{schema_name}".lower()
        if getattr(self, "_schema_cache", None) is None:
            self._schema_cache = set()
            self._table_exists_cache = set()
            
        if cache_key not in self._schema_cache:
            stmt = f"SHOW TABLES IN `{catalog}`.`{schema_name}`"
            try:
                rows = self.execute_statements([stmt])
                if rows:
                    payload = rows[0] if isinstance(rows[0], dict) else {}
                    result_block = payload.get("result") if isinstance(payload, dict) else {}
                    data_array = result_block.get("data_array") if isinstance(result_block, dict) else []
                    if data_array:
                        for row in data_array:
                            # Databricks SHOW TABLES returns row like [database, tableName, isTemporary].
                            # We safely index all valid string fields into the cache to avoid schema shifts.
                            for field in row:
                                if isinstance(field, str) and field:
                                    self._table_exists_cache.add(f"{cache_key}.{field.lower()}")
                self._schema_cache.add(cache_key)
            except Exception as e:
                logger.warning("Databricks Schema Cache load failed: %s", e)
                return None
                
        return f"{cache_key}.{table_name.lower()}" in self._table_exists_cache

    def _auto_initialize_missing_tables(self, sml_model: SMLModel) -> None:
        """Create source aliases or shell tables for datasets missing in Databricks."""
        if not self._dbx_behavior.create_measure_views:
            return

        init_statements: list[str] = []
        initialized_tables: list[str] = []
        seen_sources: set[str] = set()

        for dataset in sml_model.datasets:
            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)
            if existing_source:
                continue

            normalized_source = str(expected_source or "").strip()
            if not normalized_source or normalized_source in seen_sources:
                continue

            alias_view_stmts = self._build_source_alias_view_statements(dataset, normalized_source)
            if alias_view_stmts:
                init_statements.extend(alias_view_stmts)
                initialized_tables.append(normalized_source)
                seen_sources.add(normalized_source)
                continue

            shell_stmts = self._build_shell_table_statements(dataset, normalized_source)
            if not shell_stmts:
                logger.warning(
                    "Databricks pre-flight skipped for dataset '%s': could not parse source table %s",
                    dataset.unique_name,
                    normalized_source,
                )
                continue

            init_statements.extend(shell_stmts)
            initialized_tables.append(normalized_source)
            seen_sources.add(normalized_source)

        # Deduplicate schemas and split from table creations to parallelize execution
        schema_stmts: set[str] = set()
        table_stmts: list[str] = []

        for stmt in init_statements:
            if "CREATE SCHEMA" in stmt:
                schema_stmts.add(stmt)
            else:
                table_stmts.append(stmt)

        if schema_stmts:
            self.execute_statements(list(schema_stmts), concurrent=False)
            
        if table_stmts:
            self.execute_statements(table_stmts, concurrent=True)

        logger.warning(
            "Databricks pre-flight initialized %d missing source table shell(s): %s",
            len(initialized_tables),
            ", ".join(initialized_tables),
        )

    def _infer_physical_source_column_name(self, semantic_name: str) -> str:
        """Infer a likely physical column name from a semantic name."""
        raw = str(semantic_name or "").strip().replace("`", "")
        if not raw:
            return ""
        candidate = raw.lower()
        candidate = re.sub(r"[^0-9a-z_]+", "_", candidate)
        candidate = re.sub(r"_+", "_", candidate).strip("_")
        return candidate

    def _metric_view_projection_prefix(self, dataset: SMLDataset) -> str:
        """Return the short alias prefix used for metric-view column names."""
        raw = str(dataset.unique_name or dataset.source_table or "").strip().replace("`", "")
        if not raw:
            return "COL"

        sanitized = self._sanitize_identifier(raw)
        compact = re.sub(r"[^A-Z0-9]+", "", sanitized.upper())
        if not compact:
            return "COL"

        return (compact[:4] or "COL").lower()

    def _prefix_metric_view_measure_name(
        self,
        dataset: SMLDataset,
        measure_name: str,
        used_dimension_names: set[str],
    ) -> str:
        """Prefix a measure name with table alias and handle collisions with dimensions.
        
        Example: dataset='customer', measure='revenue' -> 'cust_revenue'
        On collision with a dimension: 'cust_revenue' + dimension 'cust_revenue' -> 'cust_revenue_m'
        """
        prefix = self._metric_view_projection_prefix(dataset)
        sanitized_measure = self._sanitize_identifier(measure_name).lower()
        candidate = f"{prefix}_{sanitized_measure}"
        
        # Check for collision with any dimension name
        if candidate.upper() not in used_dimension_names:
            return candidate
        
        # Collision detected: append _m suffix to disambiguate as a measure
        collision_safe = f"{candidate}_m"
        counter = 2
        while collision_safe.upper() in used_dimension_names:
            collision_safe = f"{candidate}_m{counter}"
            counter += 1
        return collision_safe

    def _make_unique_projected_name(
        self,
        candidate: str,
        used_names: set[str],
        suffix: str = "dim",
    ) -> str:
        """Make a projected column name unique within a select list."""
        base_name = self._sanitize_identifier(candidate)
        unique_name = base_name
        if unique_name.upper() not in used_names:
            used_names.add(unique_name.upper())
            return unique_name

        unique_name = f"{base_name}_{suffix}"
        counter = 2
        while unique_name.upper() in used_names:
            unique_name = f"{base_name}_{suffix}{counter}"
            counter += 1
        used_names.add(unique_name.upper())
        return unique_name

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

    def _get_relationship_columns_for_dataset(self, dataset: SMLDataset, sml_model: SMLModel) -> set[str]:
        """Return columns used by relationships for this dataset.

        Hidden columns that participate in joins must still be emitted so the
        generated Databricks artifacts can resolve relationship predicates.
        """
        dataset_name = str(dataset.unique_name or "").upper()
        relationship_cols: set[str] = set()
        for rel in sml_model.relationships:
            if rel.to_dataset and rel.to_dataset.upper() == dataset_name:
                relationship_cols.update(c for c in rel.to_columns if c)
            if rel.from_dataset and rel.from_dataset.upper() == dataset_name:
                relationship_cols.update(c for c in rel.from_columns if c)
        return relationship_cols

    def _build_shell_table_statements(self, dataset: SMLDataset, source_table: str) -> list[str]:
        """Build Databricks DDL for an empty managed table shell.

        Relationship columns are always included, even if hidden in the semantic
        model, because they are required for joins and sync parity.
        """
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return []

        catalog, schema_name, table_name = parsed
        relationship_cols = {col.unique_name for col in dataset.columns if col.is_key}
        column_defs: list[str] = []
        used_column_names: set[str] = set()

        for col in dataset.columns:
            if col.is_hidden and col.unique_name not in relationship_cols and not col.is_key:
                continue
            safe_name = self._make_unique_projected_name(
                col.unique_name,
                used_column_names,
                suffix="col",
            )
            dbx_type = self._sql_type(
                col.data_type.value,
                getattr(col, "source_type", "") or "",
                col.unique_name,
            )
            column_defs.append(f"`{safe_name}` {dbx_type}")

        if not column_defs:
            column_defs.append("`_semabridge_placeholder` STRING")

        return [
            f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema_name}`",
            (
                f"CREATE TABLE IF NOT EXISTS `{catalog}`.`{schema_name}`.`{table_name}` (\n    "
                + ",\n    ".join(column_defs)
                + "\n) USING DELTA"
            ),
        ]

    def _build_source_alias_view_statements(self, dataset: SMLDataset, source_table: str) -> list[str]:
        """Build a shim view over a normalized physical table.

        This is used when the physical table uses snake_case names and the
        semantic model keeps Fabric-style identifiers. Hidden relationship
        columns are preserved.
        """
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return []

        catalog, schema_name, table_name = parsed
        physical_table_name = self._infer_physical_source_column_name(dataset.unique_name or table_name)
        if not physical_table_name:
            return []

        physical_table = f"`{catalog}`.`{schema_name}`.`{physical_table_name}`"
        physical_exists = self._check_source_table_exists(physical_table)
        if physical_exists is False:
            return []

        relationship_cols = {col.unique_name for col in dataset.columns if col.is_key}
        column_selects: list[str] = []
        used_aliases: set[str] = set()

        for col in dataset.columns:
            if col.is_hidden and col.unique_name not in relationship_cols and not col.is_key:
                continue
            alias_name = str(col.unique_name or "").strip()
            if not alias_name:
                continue
            source_column = self._infer_physical_source_column_name(col.unique_name)
            if not source_column:
                continue
            unique_alias = alias_name
            counter = 2
            while unique_alias.upper() in used_aliases:
                unique_alias = f"{alias_name}_col{counter}"
                counter += 1
            used_aliases.add(unique_alias.upper())
            column_selects.append(f"  `{source_column}` AS `{unique_alias}`")

        if not column_selects:
            return []

        view_name = str(dataset.unique_name or table_name).strip().replace("`", "")
        view_fq = f"`{catalog}`.`{schema_name}`.`{view_name}`"
        select_list = ",\n".join(column_selects)
        return [
            f"CREATE OR REPLACE VIEW {view_fq} AS\n"
            f"SELECT\n"
            f"{select_list}\n"
            f"FROM {physical_table}"
        ]

    def _build_metric_view_column_bindings(
        self,
        dataset: SMLDataset,
        source_fq: str,
        sml_model: SMLModel,
    ) -> list[MetricViewColumnBinding]:
        """Build a stable source/projection mapping for metric-view generation."""
        relationship_cols = self._get_relationship_columns_for_dataset(dataset, sml_model)
        used_projection_names: set[str] = set()
        bindings: list[MetricViewColumnBinding] = []
        projection_prefix = self._metric_view_projection_prefix(dataset)

        for col in dataset.columns:
            projected_name = self._make_unique_projected_name(
                f"{projection_prefix}_{self._sanitize_identifier(col.unique_name).lower()}",
                used_projection_names,
                suffix="dim",
            )
            source_column = self._resolve_physical_source_column(
                dataset,
                col.unique_name,
                source_fq,
            )
            if not source_column:
                continue
            include_as_dimension = not (
                col.is_hidden and col.unique_name not in relationship_cols and not col.is_key
            )
            bindings.append(
                MetricViewColumnBinding(
                    semantic_name=col.unique_name,
                    projected_name=projected_name,
                    source_column=source_column,
                    include_as_dimension=include_as_dimension,
                )
            )

        return bindings

    def _build_metric_view_source_query(
        self,
        bindings: list[MetricViewColumnBinding],
        source_fq: str,
    ) -> str:
        """Build an inline source query that aliases physical columns to stable metric-view names."""
        physical_cols = self._get_source_table_columns(source_fq)
        select_parts = [
            (
                f"  `{binding.source_column}` AS `{binding.projected_name}`"
                if not physical_cols or binding.source_column.lower() in physical_cols
                else f"  NULL AS `{binding.projected_name}`"
            )
            for binding in bindings
        ]
        if not select_parts:
            return source_fq.replace("`", "")

        select_list = ",\n".join(select_parts)
        return (
            "|\n"
            "  SELECT\n"
            f"{select_list}\n"
            f"  FROM {source_fq.replace('`', '')}"
        )

    def _requires_source_alias_query(
        self,
        source_fq: str,
        bindings: list[MetricViewColumnBinding],
    ) -> bool:
        """Return True when mapped source columns need semantic aliasing."""
        physical_cols = self._get_source_table_columns(source_fq)
        if not physical_cols:
            # Unknown source shape: keep direct source mapping.
            return False

        for binding in bindings:
            source_name = binding.source_column.lower()
            projected_name = binding.projected_name.lower()
            if source_name not in physical_cols:
                return True
            if projected_name != source_name:
                return True
        return False

    def _metric_view_allowed_prefixes(self, dataset: SMLDataset, source_fq: str) -> set[str]:
        """Return prefixes that safely resolve to the current dataset in metric SQL."""
        prefixes: set[str] = set()
        candidates = {
            str(dataset.unique_name or "").strip(),
            str(dataset.source_table or dataset.unique_name or "").strip(),
            self._sanitize_identifier(dataset.unique_name).lower(),
            self._sanitize_identifier(dataset.source_table or dataset.unique_name).lower(),
            self._infer_physical_source_column_name(dataset.unique_name),
            self._infer_physical_source_column_name(dataset.source_table or dataset.unique_name),
            to_alias(dataset.unique_name or ""),
            to_alias(dataset.source_table or dataset.unique_name or ""),
        }

        parsed = self._parse_table_reference(source_fq)
        if parsed:
            _, _, table_name = parsed
            candidates.update({
                table_name,
                self._sanitize_identifier(table_name).lower(),
                self._infer_physical_source_column_name(table_name),
                to_alias(table_name),
            })

        for candidate in candidates:
            normalized = str(candidate or "").strip().lower()
            if normalized:
                prefixes.add(normalized)
        return prefixes

    def _rewrite_metric_view_measure_expression(
        self,
        sql_expression: str,
        dataset: SMLDataset,
        source_fq: str,
        bindings: list[MetricViewColumnBinding],
    ) -> str | None:
        """Rewrite dataset-local measure SQL to the metric-view source projection."""
        expr = str(sql_expression or "").strip()
        if not expr:
            return None

        allowed_prefixes = self._metric_view_allowed_prefixes(dataset, source_fq)
        column_lookup: dict[str, str] = {}
        physical_cols = {str(c).lower() for c in self._get_source_table_columns(source_fq)}
        for binding in bindings:
            keys = {
                binding.projected_name,
                self._sanitize_identifier(binding.semantic_name),
                binding.source_column,
            }
            for candidate in self._physical_source_column_candidates(dataset, binding.semantic_name):
                keys.add(candidate)
            for key in keys:
                normalized = self._sanitize_identifier(key).lower()
                if normalized and normalized not in column_lookup:
                    column_lookup[normalized] = binding.projected_name

        unresolved = False

        def _resolve_projected_name(column_name: str) -> str | None:
            normalized = self._sanitize_identifier(column_name).lower()
            if not normalized:
                return None
            projected = column_lookup.get(normalized)
            if projected:
                return projected

            # Fallback for sparse semantic models: if the physical source table
            # has this column, allow it as-is so translatable measures are not
            # dropped solely due to missing semantic bindings.
            if normalized in physical_cols:
                return self._sanitize_identifier(column_name)
            return None

        qualified_pattern = re.compile(
            r"(?:`(?P<prefix_bt>[^`]+)`|\"(?P<prefix_dq>[^\"]+)\"|(?P<prefix>[A-Za-z_][A-Za-z0-9_]*))\s*\.\s*"
            r"(?:`(?P<bt>[^`]+)`|\"(?P<dq>[^\"]+)\"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
        )

        def _replace_qualified(match: re.Match[str]) -> str:
            nonlocal unresolved
            prefix = str(
                match.group("prefix_bt")
                or match.group("prefix_dq")
                or match.group("prefix")
                or ""
            ).strip().lower()
            column_name = match.group("bt") or match.group("dq") or match.group("bare") or ""
            
            # Try to resolve with allowed prefix first
            if prefix in allowed_prefixes:
                projected = _resolve_projected_name(column_name)
                if projected:
                    return f"`{projected}`"

            # Could not resolve: mark as unresolved and return original.
            unresolved = True
            return match.group(0)

        expr = qualified_pattern.sub(_replace_qualified, expr)
        if unresolved:
            return None

        quoted_pattern = re.compile(r"`([^`]+)`|\"([^\"]+)\"")

        def _replace_quoted(match: re.Match[str]) -> str:
            nonlocal unresolved
            column_name = match.group(1) or match.group(2) or ""
            projected = _resolve_projected_name(column_name)
            if not projected:
                if not dataset.columns:
                    return f"`{self._sanitize_identifier(column_name)}`"
                unresolved = True
                return match.group(0)
            return f"`{projected}`"

        expr = quoted_pattern.sub(_replace_quoted, expr)
        if unresolved:
            return None
        return expr

    def _rewrite_sql_view_measure_expression(
        self,
        sql_expression: str,
        dataset: SMLDataset,
        source_fq: str,
    ) -> str | None:
        """Rewrite dataset-local SQL for plain Databricks SQL views.

        Databricks SQL view fallback cannot safely accept Fabric/Snowflake-style
        identifiers like ``fact."REVENUE"``. This normalizes same-dataset
        references to Databricks backtick identifiers and rejects expressions
        that still reference another dataset alias/table.
        """
        expr = str(sql_expression or "").strip()
        if not expr:
            return None

        allowed_prefixes = self._metric_view_allowed_prefixes(dataset, source_fq)
        physical_cols = {str(c).lower() for c in self._get_source_table_columns(source_fq)}
        column_lookup: dict[str, str] = {}
        for col in dataset.columns:
            resolved_column = self._resolve_physical_source_column(
                dataset,
                col.unique_name,
                source_fq,
            )
            keys = {
                col.unique_name,
                self._sanitize_identifier(col.unique_name),
                resolved_column,
            }
            for candidate in self._physical_source_column_candidates(dataset, col.unique_name):
                keys.add(candidate)
            for key in keys:
                normalized = self._sanitize_identifier(key).lower()
                if normalized and normalized not in column_lookup:
                    column_lookup[normalized] = resolved_column

        unresolved = False

        def _resolve_column_name(column_name: str) -> str | None:
            normalized = self._sanitize_identifier(column_name).lower()
            if not normalized:
                return None
            return column_lookup.get(normalized)

        qualified_pattern = re.compile(
            r"(?:`(?P<prefix_bt>[^`]+)`|\"(?P<prefix_dq>[^\"]+)\"|(?P<prefix>[A-Za-z_][A-Za-z0-9_]*))\s*\.\s*"
            r"(?:`(?P<bt>[^`]+)`|\"(?P<dq>[^\"]+)\"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
        )

        def _replace_qualified(match: re.Match[str]) -> str:
            nonlocal unresolved
            prefix = str(
                match.group("prefix_bt")
                or match.group("prefix_dq")
                or match.group("prefix")
                or ""
            ).strip().lower()
            column_name = match.group("bt") or match.group("dq") or match.group("bare") or ""
            if prefix not in allowed_prefixes:
                unresolved = True
                return match.group(0)
            resolved_column = _resolve_column_name(column_name)
            if not resolved_column:
                unresolved = True
                return match.group(0)
            return f"`{resolved_column}`"

        expr = qualified_pattern.sub(_replace_qualified, expr)
        if unresolved:
            return None

        quoted_pattern = re.compile(r"`([^`]+)`|\"([^\"]+)\"")

        def _replace_quoted(match: re.Match[str]) -> str:
            nonlocal unresolved
            column_name = match.group(1) or match.group(2) or ""
            resolved_column = _resolve_column_name(column_name)
            if not resolved_column:
                # Fail closed for SQL fallback when source schema is known and
                # the quoted identifier is absent. This avoids emitting invalid
                # SQL that later fails with UNRESOLVED_COLUMN in Databricks.
                normalized = self._sanitize_identifier(column_name).lower()
                if physical_cols and normalized not in physical_cols:
                    unresolved = True
                    return match.group(0)
                return f"`{self._sanitize_identifier(column_name)}`"
            return f"`{resolved_column}`"

        rewritten = quoted_pattern.sub(_replace_quoted, expr)
        if unresolved:
            return None
        return rewritten

    def _generate_metric_view_yaml(
        self,
        sml_model: SMLModel,
        dataset: SMLDataset,
        source_fq: str,
        resolved_measures: list[ResolvedMeasure],
        bindings: list[MetricViewColumnBinding],
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
        yaml_quote = self._yaml_quote

        # Build the source — use inline SQL query when no physical table is mapped
        ds_name = self._sanitize_identifier(
            dataset.source_table or dataset.unique_name
        )
        has_explicit_mapping = self._has_source_table_mapping(ds_name)

        lines: list[str] = [
            "version: 1.1",
            f"comment: {yaml_quote(f'Semabridge: {model_label} - {ds_label}')}",
        ]

        inline_measure_source_required = not bindings and not dataset.columns and resolved_measures
        inline_source_columns: set[str] = set()

        if inline_measure_source_required:
            inline_cols: list[str] = []
            used_cols: set[str] = set()
            for rm in resolved_measures:
                for col_name in self._extract_inline_source_columns_from_sql_expression(rm.sql_expression):
                    if col_name in used_cols:
                        continue
                    used_cols.add(col_name)
                    inline_source_columns.add(col_name)  # Track these for dimension generation
                    inline_cols.append(f"CAST(NULL AS DOUBLE) AS `{col_name}`")
            if inline_cols:
                lines.append("source: |")
                lines.append("  SELECT")
                for index, inline_col in enumerate(inline_cols):
                    suffix = "," if index < len(inline_cols) - 1 else ""
                    lines.append(f"  {inline_col}{suffix}")
                lines.append(f"  FROM {source_fq.replace('`', '')}")
            else:
                lines.append(f"source: {yaml_quote(source_fq.replace('`', ''))}")
        elif has_explicit_mapping:
            if self._requires_source_alias_query(source_fq, bindings):
                # Use an aliasing source query when mapped physical columns do
                # not match semantic names.
                lines.append("source: " + self._build_metric_view_source_query(bindings, source_fq))
            else:
                lines.append(f"source: {yaml_quote(source_fq.replace('`', ''))}")
        else:
            # Inline SQL query as source — no physical table needed
            # Generates a typed schema SELECT using CAST(NULL AS type)
            inline_cols: list[str] = []
            for binding in bindings:
                col = dataset.get_column(binding.semantic_name)
                if not col:
                    continue
                dbx_type = self._sql_type(
                    col.data_type.value, col.source_type, col.unique_name,
                )
                inline_cols.append(
                    f"CAST(NULL AS {dbx_type}) AS `{binding.projected_name}`"
                )
            if inline_cols:
                inline_select = ", ".join(inline_cols)
                lines.append("source: |")
                lines.append(f"  SELECT {inline_select}")
            else:
                # Fallback: reference the table directly
                source_for_yaml = source_fq.replace("`", "")
                lines.append(f"source: {yaml_quote(source_for_yaml)}")

        # Emit joins if enabled and relationships exist
        joins_lines = self._generate_metric_view_joins_yaml(sml_model, dataset)
        lines.extend(joins_lines)

        lines.append("")
        lines.append("dimensions:")
        dimensions_added = 0
        used_dimension_names: set[str] = set()
        
        # First, emit explicit dimensions from dataset.columns
        for binding in bindings:
            if not binding.include_as_dimension:
                continue
            lines.append(f"  - name: {yaml_quote(binding.projected_name)}")
            lines.append(f"    expr: {yaml_quote(f'`{binding.projected_name}`')}")
            used_dimension_names.add(binding.projected_name.upper())
            dimensions_added += 1
        
        # Then, emit auto-discovered dimensions from inline source columns when dataset.columns is empty
        if inline_measure_source_required and inline_source_columns:
            projection_prefix = self._metric_view_projection_prefix(dataset)
            for col_name in sorted(inline_source_columns):
                projected_name = self._make_unique_projected_name(
                    f"{projection_prefix}_{self._sanitize_identifier(col_name).lower()}",
                    used_dimension_names,
                    suffix="dim",
                )
                lines.append(f"  - name: {yaml_quote(projected_name)}")
                lines.append(f"    expr: {yaml_quote(f'`{projected_name}`')}")
                used_dimension_names.add(projected_name.upper())
                dimensions_added += 1
        
        if dimensions_added == 0:
            lines[-1] = "dimensions: []"

        lines.append("")
        lines.append("measures:")
        measures_added = 0
        for rm in resolved_measures:
            if not rm.sql_expression:
                continue

            dbx_expr = self._normalize_metric_view_sql_expression(rm.sql_expression)
            if not dbx_expr or not dbx_expr.strip('`').strip():
                logger.warning(
                    "Skipping malformed metric-view measure '%s' for dataset '%s': empty SQL expression",
                    rm.name,
                    dataset.unique_name,
                )
                continue

            # Prefix measure name with table alias for consistency
            prefixed_measure_name = self._prefix_metric_view_measure_name(
                dataset, rm.name, used_dimension_names
            )

            if rm.confidence == CONFIDENCE_LOW and self._dbx_behavior.enable_low_confidence_drafts:
                warning = " ".join(rm.warnings).replace('"', "'") if rm.warnings else "LOW CONFIDENCE"
                original_dax = (rm.original_dax or "").replace('"', "'")
                lines.append(f"  # TRANSLATION WARNING: {warning}")
                if original_dax:
                    dax_preview = re.sub(r"\s+", " ", original_dax).strip()[:200]
                    lines.append(f"  # Original DAX: {dax_preview}")

            lines.append(f"  - name: {yaml_quote(prefixed_measure_name)}")
            lines.append(f"    expr: {yaml_quote(dbx_expr)}")
            measures_added += 1
        if measures_added == 0:
            lines[-1] = "measures: []"

        return "\n".join(lines)

    def _generate_metric_view_joins_yaml(
        self,
        sml_model: SMLModel,
        dataset: SMLDataset,
    ) -> list[str]:
        """
        Generate YAML for metric view 'joins' property.
        
        Builds nested join structures when enable_metric_view_joins=true
        and relationships exist for the primary dataset.
        
        Args:
            sml_model: The parent semantic model with relationships.
            dataset: The primary dataset for this metric view.
        
        Returns:
            List of YAML lines for the joins section.
            Empty list if no joins should be emitted.
        """
        # Check feature flag
        if not self._dbx_behavior.enable_metric_view_joins:
            return []
        
        # Check that cross-table joins are also enabled
        if not self._dbx_behavior.enable_cross_table_joins:
            logger.debug(
                "Skipping metric view joins for '%s': "
                "enable_metric_view_joins=true but enable_cross_table_joins=false",
                dataset.unique_name,
            )
            return []
        
        try:
            # Build join tree from relationships
            builder = JoinTreeBuilder(sml_model)
            joins = builder.build_join_tree(dataset.unique_name)
        except ValueError as e:
            logger.debug(
                "Could not build join tree for '%s': %s",
                dataset.unique_name,
                str(e),
            )
            return []
        
        if not joins:
            return []
        
        # Emit joins YAML
        lines: list[str] = [""]
        lines.append("joins:")
        self._emit_join_yaml(joins, lines, indent=1)
        
        return lines
    
    def _emit_join_yaml(
        self,
        joins: list,
        lines: list[str],
        indent: int = 0,
    ) -> None:
        """
        Recursively emit nested join structures as YAML.
        
        Args:
            joins: List of SMLJoin objects.
            lines: Output YAML line list (mutated).
            indent: Current indentation level.
        """
        indent_str = "  " * indent
        
        for join in joins:
            lines.append(f"{indent_str}  - name: {self._yaml_quote(join.name)}")
            lines.append(f"{indent_str}    source: {self._yaml_quote(join.source)}")
            lines.append(f"{indent_str}    'on': {self._yaml_quote(join.on)}")
            
            # Emit nested joins if present
            if join.joins:
                lines.append(f"{indent_str}    joins:")
                self._emit_join_yaml(join.joins, lines, indent=indent + 2)

    def _yaml_quote(self, value: str) -> str:
        """Encode a value as a YAML-safe scalar via JSON string quoting."""
        return json.dumps(str(value or ""))

    def _normalize_metric_view_sql_expression(self, sql_expression: str) -> str:
        """Normalize SQL expressions for metric-view YAML readability."""
        expr = str(sql_expression or "").strip().replace("`", "")
        if not expr:
            return expr

        parts = re.split(r"('(?:''|[^'])*')", expr)
        normalized_parts: list[str] = []
        for index, part in enumerate(parts):
            if not part:
                continue
            if index % 2 == 1:
                normalized_parts.append(part)
            else:
                normalized_parts.append(part.lower())
        return "".join(normalized_parts)

    def _extract_inline_source_columns_from_sql_expression(self, sql_expression: str) -> list[str]:
        """Extract likely source column names from translated SQL for inline YAML sources."""
        expr = str(sql_expression or "")
        raw_columns = set(re.findall(r"`([^`]+)`", expr))
        raw_columns.update(
            re.findall(
                r"(?i)\b(?:sum|average|min|max|count|countif|distinctcount)\s*\(\s*(?:distinct\s+)?([a-z_][a-z0-9_]*)\s*\)",
                expr,
            )
        )
        deduped: list[str] = []
        seen: set[str] = set()
        for raw_col in raw_columns:
            column_name = self._sanitize_identifier(raw_col).lower()
            if not column_name or column_name in seen:
                continue
            seen.add(column_name)
            deduped.append(column_name)
        return deduped

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
        metric_name_index = self._build_metric_name_index(sml_model)

        # Group metrics by dataset
        metrics_by_dataset: dict[str, list[SMLMetric]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if ds_name:
                metrics_by_dataset.setdefault(ds_name, []).append(metric)
        has_any_metrics = bool(metrics_by_dataset)

        for dataset in sml_model.datasets:
            ds_name = self._sanitize_identifier(dataset.unique_name)
            if not ds_name:
                continue
            metrics = metrics_by_dataset.get(ds_name, [])

            # Resolve source table — schema-first: never skip on missing data
            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)

            if existing_source:
                source_fq = existing_source
                self._reconcile_dataset_schema(dataset, source_fq)
            else:
                # Schema-first deployment: use expected source reference.
                # _generate_metric_view_yaml will emit inline SQL (CAST NULL)
                # since _has_source_table_mapping returns False for unmapped tables.
                source_fq = expected_source
                logger.info(
                    "📋 Schema-first deploy for dataset '%s': no physical table at %s — "
                    "deploying metric view with inline SQL schema stub",
                    dataset.unique_name,
                    expected_source,
                )

            bindings = self._build_metric_view_column_bindings(dataset, source_fq, sml_model)
            # Resolve all measures for this dataset
            resolved: list[ResolvedMeasure] = []
            for metric in metrics:
                m_name = metric_name_index.get(
                    id(metric),
                    self._normalize_metric_identifier(metric.unique_name),
                )

                measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                    metrics,
                    metric,
                )

                is_valid, error_reason = self._validate_measure_dependencies(metric, sml_model)
                if not is_valid:
                    skipped_count += 1
                    skipped_details.append({
                        "name": m_name,
                        "reason": DEPLOY_REASON_VALIDATION_FAILED,
                    })
                    continue

                sql_expr, translation_type = self._resolve_measure_sql(
                    metric,
                    dataset,
                    measure_sql_map=measure_sql_map,
                )
                if not sql_expr:
                    if self._dbx_behavior.enable_low_confidence_drafts:
                        resolved.append(ResolvedMeasure(
                            name=m_name,
                            sql_expression=DRAFT_MEASURE_SQL,
                            translation_type=translation_type,
                            confidence=CONFIDENCE_LOW,
                            original_dax=(metric.expression or ""),
                            warnings=[metric.sync_failure_reason or DEPLOY_REASON_DAX_NOT_SUPPORTED],
                        ))
                    else:
                        skipped_count += 1
                        skipped_details.append({
                            "name": m_name,
                            "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED,
                            "translation_type": translation_type,
                        })
                    continue

                metric_view_expr = self._rewrite_metric_view_measure_expression(
                    sql_expr,
                    dataset,
                    source_fq,
                    bindings,
                )
                if not metric_view_expr:
                    metric_view_expr = self._build_scalar_subquery_aggregate_expression(
                        sql_expr,
                        dataset,
                        sml_model,
                    )
                if not metric_view_expr:
                    if self._dbx_behavior.enable_cross_table_joins:
                        resolved.append(ResolvedMeasure(
                            name=m_name,
                            sql_expression=DRAFT_MEASURE_SQL,
                            translation_type=translation_type,
                            confidence=CONFIDENCE_LOW,
                            original_dax=(metric.expression or ""),
                            warnings=[DEPLOY_REASON_CROSS_TABLE],
                        ))
                    else:
                        skipped_count += 1
                        skipped_details.append({
                            "name": m_name,
                            "reason": DEPLOY_REASON_CROSS_TABLE,
                            "translation_type": translation_type,
                        })
                    continue

                # Determine confidence based on translation type
                confidence = self._assess_confidence(translation_type)
                resolved.append(ResolvedMeasure(
                    name=m_name,
                    sql_expression=metric_view_expr,
                    translation_type=translation_type,
                    confidence=confidence,
                    original_dax=(metric.expression or ""),
                ))

            deployable = [
                rm for rm in resolved
                if rm.sql_expression and (
                    rm.confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM)
                    or self._dbx_behavior.enable_low_confidence_drafts
                )
            ]
            if not deployable:
                if self._dbx_behavior.emit_metric_views_for_all_datasets or not has_any_metrics:
                    resolved = [
                        ResolvedMeasure(
                            name="total_rows",
                            sql_expression="COUNT(*)",
                            translation_type=TRANSLATION_TYPE_AGGREGATION_BUILT,
                            confidence=CONFIDENCE_HIGH,
                        )
                    ]
                    deployable = resolved
                    logger.info(
                        "📊 Falling back to synthetic metric-view measure for dataset '%s'",
                        dataset.unique_name,
                    )
                else:
                    continue

            # Generate YAML and wrap in CREATE VIEW
            yaml_body = self._generate_metric_view_yaml(
                sml_model, dataset, source_fq, resolved, bindings,
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
            TRANSLATION_TYPE_DAX_SKIPPED: CONFIDENCE_LOW,
        }.get(translation_type, CONFIDENCE_NONE)

    def _build_draft_measure_expression(
        self,
        metric: SMLMetric,
        measure_name: str,
        reason: str,
    ) -> str:
        """Build a SQL select expression for low-confidence draft measures."""
        original_dax = (metric.expression or "").replace("*/", "* /").strip() or "<missing>"
        reason_text = (reason or "LOW CONFIDENCE TRANSLATION").replace("*/", "* /")
        return (
            f"    /* TRANSLATION WARNING: {reason_text}\n"
            f"       Original DAX: {original_dax}\n"
            "    */\n"
            f"    {DRAFT_MEASURE_SQL} AS `{measure_name}`"
        )

    def _build_dataset_prefix_map(self, sml_model: SMLModel) -> dict[str, str]:
        """Build lookup map of SQL prefixes to semantic dataset names."""
        prefix_map: dict[str, str] = {}

        for dataset in sml_model.datasets:
            ds_name = str(dataset.unique_name or "").strip()
            if not ds_name:
                continue

            expected_source = self._resolve_source_table(dataset)
            parsed = self._parse_table_reference(expected_source)
            source_table_name = parsed[2] if parsed else ""

            candidates = {
                ds_name,
                self._sanitize_identifier(ds_name),
                to_alias(ds_name),
                str(dataset.source_table or "").strip(),
                self._sanitize_identifier(str(dataset.source_table or "")),
                to_alias(str(dataset.source_table or "")),
                source_table_name,
                self._sanitize_identifier(source_table_name),
                to_alias(source_table_name),
            }

            for candidate in candidates:
                normalized = str(candidate or "").strip().lower()
                if normalized and normalized not in prefix_map:
                    prefix_map[normalized] = ds_name

        return prefix_map

    def _extract_tables_from_expression(
        self,
        sql_expression: str,
        prefix_map: dict[str, str],
    ) -> set[str]:
        """Extract semantic dataset names referenced by qualified SQL identifiers."""
        ref_matches = re.findall(
            r'(?:`([^`]+)`|"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:`[^`]+`|"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)',
            sql_expression,
        )
        datasets: set[str] = set()
        for prefix_bt, prefix_dq, prefix in ref_matches:
            normalized = str(prefix_bt or prefix_dq or prefix or "").strip().lower()
            if not normalized:
                continue
            mapped = prefix_map.get(normalized)
            if mapped:
                datasets.add(mapped)
        return datasets

    def _build_relationship_graph(self, sml_model: SMLModel) -> dict[str, list[tuple[str, Any, bool]]]:
        """Build an undirected relationship graph from SML relationships.

        Each edge stores (neighbor_dataset, relationship, current_is_from_side).
        """
        graph: dict[str, list[tuple[str, Any, bool]]] = {}
        for rel in sml_model.relationships:
            if not rel.is_active:
                continue
            from_ds = str(rel.from_dataset or "").strip()
            to_ds = str(rel.to_dataset or "").strip()
            if not from_ds or not to_ds:
                continue
            graph.setdefault(from_ds, []).append((to_ds, rel, True))
            graph.setdefault(to_ds, []).append((from_ds, rel, False))
        return graph

    def _find_relationship_path(
        self,
        graph: dict[str, list[tuple[str, Any, bool]]],
        start_dataset: str,
        target_dataset: str,
    ) -> list[tuple[str, str, Any, bool]]:
        """Find a relationship path between two datasets using BFS."""
        if start_dataset == target_dataset:
            return []
        queue: deque[tuple[str, list[tuple[str, str, Any, bool]]]] = deque([(start_dataset, [])])
        visited = {start_dataset}

        while queue:
            current, path = queue.popleft()
            for neighbor, rel, current_is_from in graph.get(current, []):
                if neighbor in visited:
                    continue
                next_path = path + [(current, neighbor, rel, current_is_from)]
                if neighbor == target_dataset:
                    return next_path
                visited.add(neighbor)
                queue.append((neighbor, next_path))
        return []

    def _resolve_cross_table_query(
        self,
        sql_expression: str,
        dataset: SMLDataset,
        sml_model: SMLModel,
    ) -> tuple[str, str] | None:
        """Resolve cross-table SQL by building explicit LEFT JOIN clauses."""
        expr = str(sql_expression or "").strip()
        if not expr:
            return None

        prefix_map = self._build_dataset_prefix_map(sml_model)
        tables_involved = self._extract_tables_from_expression(expr, prefix_map)
        if len(tables_involved) <= 1:
            return None

        base_dataset_name = str(dataset.unique_name or "").strip()
        if not base_dataset_name:
            return None
        tables_involved.add(base_dataset_name)

        dataset_by_name = {
            str(ds.unique_name or "").strip(): ds
            for ds in sml_model.datasets
            if str(ds.unique_name or "").strip()
        }
        if base_dataset_name not in dataset_by_name:
            return None

        source_by_dataset: dict[str, str] = {}
        for ds_name, ds in dataset_by_name.items():
            expected_source = self._resolve_source_table(ds)
            source_by_dataset[ds_name] = self._resolve_existing_source_for_dataset(ds, expected_source) or expected_source

        alias_by_dataset: dict[str, str] = {}
        used_aliases: set[str] = set()
        for ds_name in dataset_by_name:
            preferred = self._sanitize_identifier(to_alias(ds_name) or ds_name).lower()
            alias_by_dataset[ds_name] = self._make_unique_projected_name(preferred, used_aliases, suffix="join").lower()

        graph = self._build_relationship_graph(sml_model)
        candidate_bases = [base_dataset_name] + [name for name in sorted(tables_involved) if name != base_dataset_name]
        chosen_base = ""
        chosen_paths: dict[str, list[tuple[str, str, Any, bool]]] = {}

        for candidate in candidate_bases:
            if candidate not in dataset_by_name:
                continue
            candidate_paths: dict[str, list[tuple[str, str, Any, bool]]] = {}
            candidate_ok = True
            for target in sorted(tables_involved):
                if target == candidate:
                    continue
                path = self._find_relationship_path(graph, candidate, target)
                if not path:
                    candidate_ok = False
                    break
                candidate_paths[target] = path
            if candidate_ok:
                chosen_base = candidate
                chosen_paths = candidate_paths
                break

        if not chosen_base:
            # Last-resort fallback for simple single-foreign-table aggregates:
            # embed the aggregate as a scalar subquery over the referenced table.
            if len(tables_involved) == 2:
                foreign_dataset_name = next((name for name in sorted(tables_involved) if name != base_dataset_name), "")
                foreign_ds = dataset_by_name.get(foreign_dataset_name)
                foreign_source = source_by_dataset.get(foreign_dataset_name, "")
                if foreign_ds and foreign_source:
                    subquery_match = re.match(
                        r"(?is)^\s*(SUM|AVERAGE|COUNT|DISTINCTCOUNT|MIN|MAX)\s*\(\s*(?:`(?P<prefix_bt>[^`]+)`|\"(?P<prefix_dq>[^\"]+)\"|(?P<prefix>[A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:`(?P<col_bt>[^`]+)`|\"(?P<col_dq>[^\"]+)\"|(?P<col>[A-Za-z_][A-Za-z0-9_]*))\s*\)\s*$",
                        expr,
                    )
                    if subquery_match:
                        agg_func = subquery_match.group(1).upper()
                        column_name = subquery_match.group("col_bt") or subquery_match.group("col_dq") or subquery_match.group("col") or ""
                        column_sql = self._sanitize_identifier(column_name)
                        if agg_func == "DISTINCTCOUNT":
                            agg_sql = f"COUNT(DISTINCT `{column_sql}`)"
                        else:
                            agg_sql = f"{agg_func}(CASE WHEN 1=1 THEN `{column_sql}` ELSE NULL END)" if agg_func in {"AVERAGE", "COUNT", "MIN", "MAX"} else f"SUM(`{column_sql}`)"
                        return (
                            f"(SELECT {agg_sql} FROM {foreign_source} AS `{alias_by_dataset[foreign_dataset_name]}`)"
                            ,
                            f"{source_by_dataset[base_dataset_name]} AS `{alias_by_dataset[base_dataset_name]}`",
                        )
            return None

        from_relation = f"{source_by_dataset[chosen_base]} AS `{alias_by_dataset[chosen_base]}`"
        included = {chosen_base}
        join_lines: list[str] = []

        for target in sorted(tables_involved):
            if target in included:
                continue
            path = chosen_paths.get(target)
            if not path:
                return None

            for current_ds_name, next_ds_name, rel, current_is_from in path:
                if next_ds_name in included:
                    continue
                current_ds = dataset_by_name.get(current_ds_name)
                next_ds = dataset_by_name.get(next_ds_name)
                if not current_ds or not next_ds:
                    return None

                current_source = source_by_dataset.get(current_ds_name, "")
                next_source = source_by_dataset.get(next_ds_name, "")
                if not current_source or not next_source:
                    return None

                if current_is_from:
                    left_cols = rel.from_columns
                    right_cols = rel.to_columns
                else:
                    left_cols = rel.to_columns
                    right_cols = rel.from_columns

                on_parts: list[str] = []
                for left_semantic, right_semantic in zip(left_cols, right_cols):
                    left_col = self._resolve_physical_source_column(current_ds, left_semantic, current_source)
                    right_col = self._resolve_physical_source_column(next_ds, right_semantic, next_source)
                    if not left_col or not right_col:
                        continue
                    on_parts.append(
                        f"`{alias_by_dataset[current_ds_name]}`.`{left_col}` = "
                        f"`{alias_by_dataset[next_ds_name]}`.`{right_col}`"
                    )

                if not on_parts:
                    return None

                join_lines.append(
                    f"LEFT JOIN {next_source} AS `{alias_by_dataset[next_ds_name]}` "
                    f"ON {' AND '.join(on_parts)}"
                )
                included.add(next_ds_name)

        unresolved = False
        qualified_pattern = re.compile(
            r"(?:`(?P<prefix_bt>[^`]+)`|\"(?P<prefix_dq>[^\"]+)\"|(?P<prefix>[A-Za-z_][A-Za-z0-9_]*))\s*\.\s*"
            r"(?:`(?P<bt>[^`]+)`|\"(?P<dq>[^\"]+)\"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
        )

        def _replace_qualified(match: re.Match[str]) -> str:
            nonlocal unresolved
            prefix = str(
                match.group("prefix_bt")
                or match.group("prefix_dq")
                or match.group("prefix")
                or ""
            ).strip().lower()
            column_name = match.group("bt") or match.group("dq") or match.group("bare") or ""

            ds_name = prefix_map.get(prefix)
            if not ds_name:
                unresolved = True
                return match.group(0)

            ds = dataset_by_name.get(ds_name)
            source = source_by_dataset.get(ds_name, "")
            alias = alias_by_dataset.get(ds_name, "")
            if not ds or not source or not alias:
                unresolved = True
                return match.group(0)

            resolved_col = self._resolve_physical_source_column(ds, column_name, source)
            if not resolved_col:
                unresolved = True
                return match.group(0)

            return f"`{alias}`.`{resolved_col}`"

        rewritten_expr = qualified_pattern.sub(_replace_qualified, expr)
        if unresolved:
            return None

        joined_from_clause = "\n".join([from_relation] + join_lines)
        return rewritten_expr, joined_from_clause

    def _build_scalar_subquery_aggregate_expression(
        self,
        sql_expression: str,
        dataset: SMLDataset,
        sml_model: SMLModel,
    ) -> str | None:
        """Build a scalar subquery for a simple table-qualified aggregate."""
        expr = str(sql_expression or "").strip()
        if not expr:
            return None

        agg_match = re.match(
            r"(?is)^\s*(SUM|AVERAGE|COUNT|DISTINCTCOUNT|MIN|MAX)\s*\(\s*(?:`(?P<prefix_bt>[^`]+)`|\"(?P<prefix_dq>[^\"]+)\"|(?P<prefix>[A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:`(?P<col_bt>[^`]+)`|\"(?P<col_dq>[^\"]+)\"|(?P<col>[A-Za-z_][A-Za-z0-9_]*))\s*\)\s*$",
            expr,
        )
        if not agg_match:
            return None

        agg_func = agg_match.group(1).upper()
        prefix = str(
            agg_match.group("prefix_bt")
            or agg_match.group("prefix_dq")
            or agg_match.group("prefix")
            or ""
        ).strip().lower()
        column_name = str(
            agg_match.group("col_bt")
            or agg_match.group("col_dq")
            or agg_match.group("col")
            or ""
        ).strip()
        if not prefix or not column_name:
            return None

        prefix_map = self._build_dataset_prefix_map(sml_model)
        target_dataset_name = prefix_map.get(prefix)
        if not target_dataset_name:
            return None

        current_dataset_name = str(dataset.unique_name or "").strip().lower()
        if target_dataset_name.strip().lower() == current_dataset_name:
            return None

        target_dataset = sml_model.get_dataset(target_dataset_name)
        if not target_dataset:
            return None

        target_source = self._resolve_source_table(target_dataset)
        target_source = self._resolve_existing_source_for_dataset(target_dataset, target_source) or target_source
        resolved_column = self._resolve_physical_source_column(target_dataset, column_name, target_source)
        if not resolved_column:
            return None

        agg_sql = {
            "SUM": f"SUM(`{resolved_column}`)",
            "AVERAGE": f"AVG(`{resolved_column}`)",
            "COUNT": f"COUNT(`{resolved_column}`)",
            "DISTINCTCOUNT": f"COUNT(DISTINCT `{resolved_column}`)",
            "MIN": f"MIN(`{resolved_column}`)",
            "MAX": f"MAX(`{resolved_column}`)",
        }.get(agg_func)
        if not agg_sql:
            return None

        return f"(SELECT {agg_sql} FROM {target_source})"

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

    def _debug_sql_artifact_dir(self, model_name: str) -> Path:
        """Return the output directory for Databricks SQL debug artifacts."""
        safe_model = self._sanitize_identifier(model_name)
        return Path("output") / "debug" / "databricks" / safe_model

    def _extract_view_name_from_sql(self, view_sql: str) -> str:
        """Extract fully-qualified view name from a CREATE VIEW statement."""
        match = re.search(
            r"(?is)CREATE\s+OR\s+REPLACE\s+VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)",
            str(view_sql or ""),
        )
        if not match:
            return "unknown_view"
        return str(match.group(1) or "unknown_view")

    def _persist_sql_debug_artifact(self, model_name: str, view_sql: str) -> str | None:
        """Persist generated SQL to disk for post-failure debugging."""
        try:
            artifact_dir = self._debug_sql_artifact_dir(model_name)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            view_name = self._extract_view_name_from_sql(view_sql)
            safe_view = self._sanitize_identifier(view_name.replace("`", "").replace(".", "_"))
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            file_name = f"{timestamp}_{safe_view}.sql"
            artifact_path = artifact_dir / file_name

            counter = 2
            while artifact_path.exists():
                artifact_path = artifact_dir / f"{timestamp}_{safe_view}_{counter}.sql"
                counter += 1

            artifact_path.write_text(str(view_sql or "") + "\n", encoding="utf-8")
            return str(artifact_path)
        except OSError as exc:
            logger.warning("Failed to persist Databricks SQL debug artifact: %s", exc)
            return None

    def _persist_sql_debug_error_artifact(self, sql_artifact_path: str, error_msg: str) -> str | None:
        """Persist deploy error details next to the emitted SQL artifact."""
        if not sql_artifact_path:
            return None
        try:
            error_path = Path(sql_artifact_path).with_suffix(".error.txt")
            error_path.write_text(str(error_msg or "") + "\n", encoding="utf-8")
            return str(error_path)
        except OSError as exc:
            logger.warning("Failed to persist Databricks SQL error artifact: %s", exc)
            return None

    def _count_schema_objects(self) -> int | None:
        """Return current number of tables/views in the target schema."""
        stmt = (
            f"SELECT COUNT(1) AS cnt FROM {self.config.catalog}.information_schema.tables "
            f"WHERE table_schema = '{self.config.schema_name}'"
        )
        try:
            rows = self.execute_statements([stmt])
        except Exception:
            return None

        if not rows:
            return None

        payload = rows[0] if isinstance(rows[0], dict) else {}
        result_block = payload.get("result") if isinstance(payload, dict) else {}
        data_array = result_block.get("data_array") if isinstance(result_block, dict) else None
        if not data_array or not data_array[0]:
            return None

        try:
            return int(data_array[0][0])
        except (TypeError, ValueError):
            return None

    def _estimate_publish_object_count(
        self,
        sml_model: SMLModel,
        *,
        resolved_view_type: str,
        measure_view_mode: str,
    ) -> int:
        """Estimate how many schema objects this publish will create or replace."""
        count = 0

        if self._dbx_behavior.create_metadata_table:
            count += 1

        if not self._dbx_behavior.create_measure_views or resolved_view_type == VIEW_TYPE_NONE:
            return count

        if measure_view_mode == "combined":
            metric_datasets = {
                self._sanitize_identifier(metric.dataset)
                for metric in sml_model.metrics
                if self._sanitize_identifier(metric.dataset)
            }
            if metric_datasets:
                count += len(metric_datasets)
            elif self._dbx_behavior.emit_metric_views_for_all_datasets:
                count += len(
                    [
                        ds for ds in sml_model.datasets
                        if self._sanitize_identifier(ds.unique_name)
                    ]
                )
            return count

        count += len(sml_model.metrics or [])
        return count

    def _select_measure_view_mode_for_quota(
        self,
        sml_model: SMLModel,
        *,
        resolved_view_type: str,
    ) -> str:
        """Pick the least risky Databricks measure-view mode for current schema quota."""
        configured_mode = str(self._dbx_behavior.measure_view_mode or "per_measure").strip().lower() or "per_measure"
        current_count = self._count_schema_objects()
        if current_count is None:
            return configured_mode

        configured_estimate = self._estimate_publish_object_count(
            sml_model,
            resolved_view_type=resolved_view_type,
            measure_view_mode=configured_mode,
        )
        configured_total = current_count + configured_estimate
        if configured_total <= DEFAULT_DATABRICKS_SCHEMA_OBJECT_LIMIT:
            return configured_mode

        if configured_mode != "combined" and self._dbx_behavior.create_measure_views:
            combined_estimate = self._estimate_publish_object_count(
                sml_model,
                resolved_view_type=resolved_view_type,
                measure_view_mode="combined",
            )
            combined_total = current_count + combined_estimate
            if combined_total <= DEFAULT_DATABRICKS_SCHEMA_OBJECT_LIMIT:
                logger.warning(
                    "Databricks schema quota preflight: auto-switching measure_view_mode from %s to combined "
                    "(current=%s, estimated=%s, combined_estimated=%s, limit=%s)",
                    configured_mode,
                    current_count,
                    configured_total,
                    combined_total,
                    DEFAULT_DATABRICKS_SCHEMA_OBJECT_LIMIT,
                )
                return "combined"

        raise DatabricksPublishError(
            "Databricks schema object quota would be exceeded before deployment starts. "
            f"schema={self.config.catalog}.{self.config.schema_name} current={current_count} "
            f"estimated_after_publish={configured_total} limit={DEFAULT_DATABRICKS_SCHEMA_OBJECT_LIMIT}. "
            "Use a cleaner/fresh schema or reduce Databricks artifact count."
        )

    def _normalize_metric_identifier(self, value: str) -> str:
        """Normalize semantic metric names while preserving meaningful symbols."""
        raw = str(value or "").strip()
        if not raw:
            return "measure"

        normalized = raw
        normalized = normalized.replace("%", "_Pct")
        normalized = normalized.replace("#", "Num_")
        normalized = re.sub(r"[\s\-]+", "_", normalized)
        normalized = self._sanitize_identifier(normalized)
        return normalized or "measure"

    def _dedupe_metric_identifier(self, base_name: str, used_names: set[str]) -> str:
        """Ensure metric identifiers are unique within a dataset scope."""
        candidate = self._sanitize_identifier(base_name) or "measure"
        if candidate.upper() not in used_names:
            used_names.add(candidate.upper())
            return candidate

        idx = 2
        while True:
            unique = f"{candidate}_{idx}"
            if unique.upper() not in used_names:
                used_names.add(unique.upper())
                return unique
            idx += 1

    def _build_metric_name_index(self, sml_model: SMLModel) -> dict[int, str]:
        """Build deterministic metric aliases keyed by object identity."""
        names_by_metric_id: dict[int, str] = {}
        used_by_dataset: dict[str, set[str]] = {}

        for metric in sml_model.metrics:
            ds_scope = self._sanitize_identifier(metric.dataset) or "_dataset"
            used_names = used_by_dataset.setdefault(ds_scope, set())
            base_name = self._normalize_metric_identifier(metric.unique_name)
            names_by_metric_id[id(metric)] = self._dedupe_metric_identifier(base_name, used_names)

        return names_by_metric_id

    # ── Measure SQL Resolution ───────────────────────────────────────────────

    def _is_simple_sum_dax_expression(self, dax_expression: str) -> bool:
        """Return True for canonical SUM(Table[Column]) DAX expressions."""
        expr = " ".join(str(dax_expression or "").split())
        if not expr:
            return False
        return bool(
            re.match(
                r"(?is)^\s*SUM\s*\(\s*(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*)?\[[^\]]+\]\s*\)\s*$",
                expr,
            )
        )

    def _resolve_measure_sql(
        self,
        metric: SMLMetric,
        dataset: Optional[SMLDataset],
        measure_sql_map: Optional[dict[str, str]] = None,
        allow_simple_sum_translation: bool = True,
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
                if (
                    not allow_simple_sum_translation
                    and self._is_simple_sum_dax_expression(dax_expr)
                ):
                    logger.info(
                        "Skipped simple SUM DAX translation for measure '%s' in SQL-view mode by policy",
                        metric.unique_name,
                    )
                    return None, TRANSLATION_TYPE_DAX_SKIPPED

                translated = self._measure_translator.try_simple_dax_to_sql(dax_expr)
                if translated:
                    logger.info(
                        "Translated DAX to SQL for measure '%s': %s → %s",
                        metric.unique_name,
                        dax_expr[:60],
                        translated,
                    )
                    return translated, TRANSLATION_TYPE_DAX_TRANSLATED

                if measure_sql_map:
                    contextual = self._measure_translator.try_contextual_dax_to_sql(
                        dax_expr,
                        measure_sql_map,
                    )
                    if contextual:
                        logger.info(
                            "Translated contextual DAX to SQL for measure '%s': %s → %s",
                            metric.unique_name,
                            dax_expr[:60],
                            contextual,
                        )
                        return contextual, TRANSLATION_TYPE_DAX_TRANSLATED

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
        exprs = [
            (metric.sql_expression or "").strip(),
            (metric.expression or "").strip(),
        ]
        for sql_expr in exprs:
            if not sql_expr:
                continue

            # Plain table-qualified aggregates like SUM('Fact'[Amount]) are the
            # important cross-table case for Databricks fallback routing.
            # Keep MAX-based refresh text unclassified so those measures stay
            # on the fast local translation path.
            m_simple_agg = re.match(
                r"(?is)^\s*(SUM|AVERAGE|COUNT|DISTINCTCOUNT)\s*\(\s*(?:'(?P<table_q>[^']+)'|(?P<table>[A-Za-z_][A-Za-z0-9_]*))\s*\[[^\]]+\]\s*\)\s*$",
                sql_expr,
            )
            if m_simple_agg:
                table_name = str(m_simple_agg.group("table_q") or m_simple_agg.group("table") or "").strip().lower()
                dataset_name = self._sanitize_identifier(metric.dataset).lower()
                if table_name and table_name != dataset_name:
                    return True

            ref_matches = re.findall(
                r'(?:`([^`]+)`|"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:`[^`]+`|"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)',
                sql_expr,
            )
            unique_tables = {
                str(prefix_bt or prefix_dq or prefix or "").strip().lower()
                for prefix_bt, prefix_dq, prefix in ref_matches
                if str(prefix_bt or prefix_dq or prefix or "").strip()
            }
            if len(unique_tables) > 1:
                return True

            dax_table_refs = re.findall(
                r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*\[([^\]]+)\]",
                sql_expr,
            )
            dax_tables = {
                str(table_q or table or "").strip().lower()
                for table_q, table, _ in dax_table_refs
                if str(table_q or table or "").strip()
            }
            if len(dax_tables) > 1:
                return True

        return False

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

    def _is_model_artifact_mode(self) -> bool:
        """Return True when per-model artifact generation is enabled."""
        return str(getattr(self._dbx_behavior, "model_artifact_mode", "per_dataset") or "").strip().lower() == "per_model"

    def _metadata_table_fq_name(self, model_name: str) -> str:
        """Resolve metadata table name for current artifact mode."""
        if not self._is_model_artifact_mode():
            return self._fq_name(model_name)

        suffix = self._sanitize_identifier(
            str(getattr(self._dbx_behavior, "model_metadata_suffix", "metadata") or "metadata")
        )
        return self._fq_name(f"{model_name}_{suffix}")

    def _generate_model_metric_view_name(self, model_name: str) -> str:
        """Generate model-level metric view name for per_model mode."""
        suffix = self._sanitize_identifier(
            str(getattr(self._dbx_behavior, "model_metric_view_suffix", "metric_view") or "metric_view")
        )
        safe_model = self._sanitize_identifier(model_name)
        view_name = f"{safe_model}_{suffix}"
        return f'`{self.config.catalog}`.`{self.config.schema_name}`.`{view_name}`'

    def _select_model_fact_dataset(
        self,
        sml_model: SMLModel,
        metrics_by_dataset: dict[str, list[SMLMetric]],
    ) -> SMLDataset | None:
        """Choose deterministic root dataset for per-model metric view generation."""
        configured_root = self._sanitize_identifier(
            str(getattr(self._dbx_behavior, "model_fact_root", "") or "")
        )
        if configured_root:
            for dataset in sml_model.datasets:
                if self._sanitize_identifier(dataset.unique_name) == configured_root:
                    return dataset

        best: SMLDataset | None = None
        best_count = -1
        for dataset in sml_model.datasets:
            ds_name = self._sanitize_identifier(dataset.unique_name)
            if not ds_name:
                continue
            count = len(metrics_by_dataset.get(ds_name, []))
            if count > best_count:
                best = dataset
                best_count = count

        if best:
            return best
        return sml_model.datasets[0] if sml_model.datasets else None

    # ── Parallel Metric Resolution (CP-Level Optimization) ──────────────────

    def _resolve_metric_sql_cached(
        self,
        metric: SMLMetric,
        sml_model: SMLModel,
        allow_simple_sum_translation: bool = True,
    ) -> dict:
        """Resolve SQL for one metric in parallel context.
        
        Returns dict with metric id, sql_expr, translation_type for thread-safe result passing.
        """
        dataset = sml_model.get_dataset(metric.dataset)
        dataset_metrics = [
            candidate
            for candidate in sml_model.metrics
            if self._sanitize_identifier(candidate.dataset) == self._sanitize_identifier(metric.dataset)
        ]
        measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
            dataset_metrics,
            metric,
        )
        sql_expr, translation_type = self._resolve_measure_sql(
            metric,
            dataset,
            measure_sql_map=measure_sql_map,
            allow_simple_sum_translation=allow_simple_sum_translation,
        )
        return {
            "metric_id": id(metric),
            "metric": metric,
            "sql_expr": sql_expr,
            "translation_type": translation_type,
            "dataset": dataset,
        }

    def _resolve_all_metrics_parallel(
        self,
        sml_model: SMLModel,
        max_workers: int = 8,
        allow_simple_sum_translation: bool = True,
    ) -> dict:
        """Resolve SQL for all metrics in parallel (CP-Level: 8 workers for DAX translation).
        
        Parallelizes the heavy DAX→SQL translation work across multiple threads.
        Each worker independently translates a metric's DAX expression.
        """
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._resolve_metric_sql_cached, metric, sml_model)
                if allow_simple_sum_translation
                else executor.submit(
                    self._resolve_metric_sql_cached,
                    metric,
                    sml_model,
                    False,
                )
                for metric in sml_model.metrics
            ]
            for future in as_completed(futures):
                result = future.result()
                results[id(result["metric"])] = result
        return results

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

        if self._is_model_artifact_mode():
            vtype = view_type_override or self._dbx_behavior.measure_view_type.strip().lower()
            if vtype == "none":
                return [], 0, 0, []
            if vtype == VIEW_TYPE_METRIC:
                return self._generate_model_level_metric_view(sml_model, model_name)
            return self._generate_model_level_view(sml_model, model_name)

        # Determine view technology
        vtype = view_type_override or self._dbx_behavior.measure_view_type.strip().lower()
        if vtype == "none":
            return [], 0, 0, []

        # Native metric view path
        if vtype == "metric_view":
            if (
                self._dbx_behavior.enable_cross_table_joins
                and self._dbx_behavior.enable_cross_table_sql_fallback
                and any(
                self._is_cross_table_measure(metric) for metric in sml_model.metrics
                )
            ):
                logger.info(
                    "Cross-table measures detected in model '%s'; routing Databricks measure views to SQL generation "
                    "(mode=%s, fallback=sql_view)",
                    sml_model.unique_name,
                    self._dbx_behavior.measure_view_mode,
                )
                view_mode = self._dbx_behavior.measure_view_mode
                if view_mode == "combined":
                    return self._generate_combined_views(sml_model, model_name)
                return self._generate_per_measure_views(sml_model, model_name)
            return self._generate_metric_view_statements(sml_model, model_name)

        # SQL view path (per_measure or combined)
        view_mode = self._dbx_behavior.measure_view_mode
        if view_mode == "combined":
            return self._generate_combined_views(sml_model, model_name)
        return self._generate_per_measure_views(sml_model, model_name)

    def _generate_model_level_view(
        self,
        sml_model: SMLModel,
        model_name: str,
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """Generate one model-level SQL view containing all deployable measures."""
        stmts: list[str] = []
        created = 0
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []

        metric_name_index = self._build_metric_name_index(sml_model)
        metrics_by_dataset: dict[str, list[SMLMetric]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if not ds_name:
                continue
            metrics_by_dataset.setdefault(ds_name, []).append(metric)

        root_dataset = self._select_model_fact_dataset(sml_model, metrics_by_dataset)
        if not root_dataset:
            return stmts, created, skipped_count, skipped_details

        root_expected = self._resolve_source_table(root_dataset)
        root_existing = self._resolve_existing_source_for_dataset(root_dataset, root_expected) or root_expected

        select_parts: list[str] = []
        for metric in sml_model.metrics:
            measure_name = metric_name_index.get(
                id(metric),
                self._normalize_metric_identifier(metric.unique_name),
            )

            dataset = sml_model.get_dataset(metric.dataset)
            if not dataset:
                skipped_count += 1
                skipped_details.append({
                    "name": measure_name,
                    "reason": DEPLOY_REASON_VALIDATION_FAILED,
                })
                continue

            is_valid, _ = self._validate_measure_dependencies(metric, sml_model)
            if not is_valid:
                skipped_count += 1
                skipped_details.append({
                    "name": measure_name,
                    "reason": DEPLOY_REASON_VALIDATION_FAILED,
                })
                continue

            dataset_key = self._sanitize_identifier(metric.dataset)
            measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                metrics_by_dataset.get(dataset_key, []),
                metric,
            )
            sql_expr, translation_type = self._resolve_measure_sql(
                metric,
                dataset,
                measure_sql_map=measure_sql_map,
                allow_simple_sum_translation=not self._dbx_behavior.metric_view_only_sum_translation,
            )

            if not sql_expr:
                if self._dbx_behavior.enable_low_confidence_drafts:
                    select_parts.append(f"    {DRAFT_MEASURE_SQL} AS `{measure_name}`")
                else:
                    skipped_count += 1
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED,
                        "translation_type": translation_type,
                    })
                continue

            source_expected = self._resolve_source_table(dataset)
            source_table = self._resolve_existing_source_for_dataset(dataset, source_expected) or source_expected
            source_relation = self._build_reconciled_sql_source_relation(dataset, source_table)
            if not dataset.columns:
                inline_cols = self._extract_inline_source_columns_from_sql_expression(sql_expr)
                if inline_cols:
                    source_relation = self._build_inline_null_source_relation(
                        source_relation,
                        inline_cols,
                    )

            sql_view_expr = self._rewrite_sql_view_measure_expression(
                sql_expr,
                dataset,
                source_table,
            )
            if not sql_view_expr:
                sql_view_expr = self._build_scalar_subquery_aggregate_expression(
                    sql_expr,
                    dataset,
                    sml_model,
                )

            if not sql_view_expr and self._dbx_behavior.enable_cross_table_joins:
                resolved_cross_table = self._resolve_cross_table_query(sql_expr, dataset, sml_model)
                if resolved_cross_table:
                    cross_expr, cross_from = resolved_cross_table
                    sql_view_expr = f"(SELECT {cross_expr} FROM {cross_from})"

            if not sql_view_expr:
                if self._dbx_behavior.enable_low_confidence_drafts:
                    select_parts.append(f"    {DRAFT_MEASURE_SQL} AS `{measure_name}`")
                else:
                    skipped_count += 1
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_CROSS_TABLE,
                        "translation_type": translation_type,
                    })
                continue

            normalized_expr = str(sql_view_expr).strip()
            if not normalized_expr.lower().startswith("(select"):
                needs_source = bool(re.search(r"`|\b(sum|avg|average|min|max|count|distinctcount)\s*\(", normalized_expr, re.IGNORECASE))
                if needs_source:
                    normalized_expr = f"(SELECT {normalized_expr} FROM {source_relation})"

            select_parts.append(f"    {normalized_expr} AS `{measure_name}`")

        if not select_parts:
            select_parts.append(f"    (SELECT COUNT(*) FROM {root_existing}) AS `total_rows`")

        select_clause = ",\n".join(select_parts)
        view_fq = self._generate_model_metric_view_name(model_name)
        view_sql = (
            f"CREATE OR REPLACE VIEW {view_fq} AS\n"
            "SELECT\n"
            f"{select_clause}\n"
            "FROM (SELECT 1 AS `_semabridge_anchor`) AS `semabridge_anchor`"
        )
        stmts.append(view_sql)
        created = 1
        logger.info("Created model-level metric view: %s", view_fq)

        return stmts, created, skipped_count, skipped_details

    def _generate_model_level_metric_view(
        self,
        sml_model: SMLModel,
        model_name: str,
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """Generate one model-level native Databricks metric view (YAML)."""
        stmts: list[str] = []
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []

        metric_name_index = self._build_metric_name_index(sml_model)
        metrics_by_dataset: dict[str, list[SMLMetric]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if not ds_name:
                continue
            metrics_by_dataset.setdefault(ds_name, []).append(metric)

        root_dataset = self._select_model_fact_dataset(sml_model, metrics_by_dataset)
        if not root_dataset:
            return stmts, 0, skipped_count, skipped_details

        root_expected = self._resolve_source_table(root_dataset)
        root_source = self._resolve_existing_source_for_dataset(root_dataset, root_expected) or root_expected
        root_bindings = self._build_metric_view_column_bindings(root_dataset, root_source, sml_model)

        resolved_measures: list[ResolvedMeasure] = []
        root_dataset_name = self._sanitize_identifier(root_dataset.unique_name)
        for metric in sml_model.metrics:
            measure_name = metric_name_index.get(
                id(metric),
                self._normalize_metric_identifier(metric.unique_name),
            )

            metric_dataset = sml_model.get_dataset(metric.dataset)
            if not metric_dataset:
                skipped_count += 1
                skipped_details.append({
                    "name": measure_name,
                    "reason": DEPLOY_REASON_VALIDATION_FAILED,
                })
                continue

            dataset_key = self._sanitize_identifier(metric.dataset)
            measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                metrics_by_dataset.get(dataset_key, []),
                metric,
            )
            sql_expr, translation_type = self._resolve_measure_sql(
                metric,
                metric_dataset,
                measure_sql_map=measure_sql_map,
                allow_simple_sum_translation=not self._dbx_behavior.metric_view_only_sum_translation,
            )

            if not sql_expr:
                skipped_count += 1
                skipped_details.append({
                    "name": measure_name,
                    "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED,
                    "translation_type": translation_type,
                })
                continue

            metric_expr: str | None = None
            if self._sanitize_identifier(metric_dataset.unique_name) == root_dataset_name:
                metric_expr = self._rewrite_metric_view_measure_expression(
                    sql_expr,
                    metric_dataset,
                    root_source,
                    root_bindings,
                )

            if not metric_expr:
                metric_expr = self._build_scalar_subquery_aggregate_expression(
                    sql_expr,
                    metric_dataset,
                    sml_model,
                )

            if not metric_expr:
                skipped_count += 1
                skipped_details.append({
                    "name": measure_name,
                    "reason": DEPLOY_REASON_CROSS_TABLE,
                    "translation_type": translation_type,
                })
                continue

            resolved_measures.append(
                ResolvedMeasure(
                    name=measure_name,
                    sql_expression=metric_expr,
                    translation_type=translation_type,
                    confidence=self._assess_confidence(translation_type),
                    original_dax=(metric.expression or ""),
                )
            )

        if not resolved_measures:
            resolved_measures = [
                ResolvedMeasure(
                    name="total_rows",
                    sql_expression="COUNT(*)",
                    translation_type=TRANSLATION_TYPE_AGGREGATION_BUILT,
                    confidence=CONFIDENCE_HIGH,
                )
            ]

        yaml_body = self._generate_model_level_metric_yaml(
            sml_model,
            root_dataset,
            root_source,
            resolved_measures,
            root_bindings,
        )
        view_fq = self._generate_model_metric_view_name(model_name)
        view_sql = (
            f"CREATE OR REPLACE VIEW {view_fq} "
            "WITH METRICS LANGUAGE YAML AS $$\n"
            f"{yaml_body}\n$$"
        )
        stmts.append(view_sql)
        logger.info("Created model-level native metric view: %s", view_fq)
        return stmts, 1, skipped_count, skipped_details

    def _generate_model_level_metric_yaml(
        self,
        sml_model: SMLModel,
        root_dataset: SMLDataset,
        root_source: str,
        resolved_measures: list[ResolvedMeasure],
        root_bindings: list[MetricViewColumnBinding],
    ) -> str:
        """Build YAML for model-level metric view with TPC-H-style dimensions when available."""
        yaml_quote = self._yaml_quote
        model_label = self._escape_literal(sml_model.label or sml_model.unique_name)

        lines: list[str] = [
            "version: 1.1",
            f"comment: {yaml_quote(f'Semabridge: {model_label} - Model Metric View')}",
        ]

        # For schemaless roots (e.g., Project Measures), synthesize required
        # source columns from measure expressions so Databricks can resolve
        # bare identifiers such as gl_refresh_datetime.
        if not root_bindings and resolved_measures:
            inline_cols: list[str] = []
            seen_inline_cols: set[str] = set()
            for rm in resolved_measures:
                for col_name in self._extract_inline_source_columns_from_sql_expression(rm.sql_expression):
                    if col_name in seen_inline_cols:
                        continue
                    seen_inline_cols.add(col_name)
                    inline_cols.append(f"CAST(NULL AS DOUBLE) AS `{col_name}`")

            if inline_cols:
                lines.append("source: |")
                lines.append("  SELECT")
                for index, inline_col in enumerate(inline_cols):
                    suffix = "," if index < len(inline_cols) - 1 else ""
                    lines.append(f"  {inline_col}{suffix}")
                lines.append(f"  FROM {root_source.replace('`', '')}")
            else:
                lines.append(f"source: {yaml_quote(root_source.replace('`', ''))}")
        else:
            lines.append(f"source: {yaml_quote(root_source.replace('`', ''))}")

        # Emit joins using existing relationship-based join-tree machinery.
        joins_lines = self._generate_metric_view_joins_yaml(sml_model, root_dataset)
        lines.extend(joins_lines)

        lines.append("")
        lines.append("dimensions:")

        root_name = self._sanitize_identifier(root_dataset.unique_name).lower()
        has_tpch_shape = root_name == "orders"
        if has_tpch_shape:
            lines.extend([
                "  - name: order_date",
                "    expr: o_orderdate",
                "  - name: order_month",
                "    expr: \"DATE_TRUNC('MONTH', o_orderdate)\"",
                "  - name: order_year",
                "    expr: YEAR(o_orderdate)",
                "  - name: order_status",
                "    expr: |-",
                "      CASE o_orderstatus",
                "        WHEN 'O' THEN 'Open'",
                "        WHEN 'P' THEN 'Processing'",
                "        WHEN 'F' THEN 'Fulfilled'",
                "      END",
                "  - name: order_priority",
                "    expr: \"SPLIT(o_orderpriority, '-')[0]\"",
                "  - name: customer_name",
                "    expr: customer.c_name",
                "  - name: market_segment",
                "    expr: customer.c_mktsegment",
                "  - name: customer_nation",
                "    expr: customer.nation.n_name",
            ])
        else:
            dimensions_added = 0
            for binding in root_bindings:
                if not binding.include_as_dimension:
                    continue
                lines.append(f"  - name: {yaml_quote(binding.projected_name)}")
                lines.append(f"    expr: {yaml_quote(f'`{binding.projected_name}`')}")
                dimensions_added += 1
            if dimensions_added == 0:
                lines[-1] = "dimensions: []"

        lines.append("")
        lines.append("measures:")
        for rm in resolved_measures:
            expr = self._normalize_metric_view_sql_expression(rm.sql_expression)
            lines.append(f"  - name: {yaml_quote(rm.name)}")
            lines.append(f"    expr: {yaml_quote(expr)}")

        return "\n".join(lines)

    def _generate_per_measure_views(
        self,
        sml_model: SMLModel,
        model_name: str,
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """One view per measure (default mode) - with parallel metric resolution."""
        stmts: list[str] = []
        created = 0
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []
        metric_name_index = self._build_metric_name_index(sml_model)

        # CP-LEVEL: Resolve all metrics in parallel (8 workers for DAX translation)
        logger.info("Resolving SQL for %d metrics in parallel (8 workers)...", len(sml_model.metrics))
        allow_simple_sum_translation = not self._dbx_behavior.metric_view_only_sum_translation
        metric_sql_cache = self._resolve_all_metrics_parallel(
            sml_model,
            max_workers=8,
            allow_simple_sum_translation=allow_simple_sum_translation,
        )

        for metric in sml_model.metrics:
            measure_name = metric_name_index.get(
                id(metric),
                self._normalize_metric_identifier(metric.unique_name),
            )
            dataset = sml_model.get_dataset(metric.dataset)
            dataset_metrics = [
                candidate
                for candidate in sml_model.metrics
                if self._sanitize_identifier(candidate.dataset) == self._sanitize_identifier(metric.dataset)
            ]

            measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                dataset_metrics,
                metric,
            )

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

            # Resolve SQL expression (from parallel cache)
            cached_result = metric_sql_cache.get(id(metric), {})
            sql_expr = cached_result.get("sql_expr")
            translation_type = cached_result.get("translation_type", TRANSLATION_TYPE_DAX_SKIPPED)
            
            source_table = self._fq_name(
                self._sanitize_identifier(dataset.source_table or dataset.unique_name)
            )
            source_relation = self._build_reconciled_sql_source_relation(dataset, source_table)
            source_relation_is_joined = False
            inline_source_columns: set[str] = set()

            if not sql_expr:
                sql_view_expr = None
            else:
                sql_view_expr = self._rewrite_sql_view_measure_expression(
                    sql_expr,
                    dataset,
                    source_table,
                )

                # Cross-table fallback: build JOINs from relationship graph.
                if not sql_view_expr and self._dbx_behavior.enable_cross_table_joins:
                    resolved_cross_table = self._resolve_cross_table_query(sql_expr, dataset, sml_model)
                    if resolved_cross_table:
                        sql_view_expr, source_relation = resolved_cross_table
                        source_relation_is_joined = True

                if not dataset.columns:
                    inline_source_columns.update(
                        self._extract_inline_source_columns_from_sql_expression(sql_expr)
                    )

            if not sql_view_expr:
                if self._dbx_behavior.enable_low_confidence_drafts:
                    sql_view_expr = self._build_draft_measure_expression(
                        metric,
                        measure_name,
                        metric.sync_failure_reason
                        or DEPLOY_REASON_DAX_NOT_SUPPORTED,
                    )
                else:
                    skipped_count += 1
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_CROSS_TABLE if sql_expr else DEPLOY_REASON_DAX_NOT_SUPPORTED,
                        "translation_type": translation_type,
                    })
                    continue

            if not dataset.columns and inline_source_columns and not source_relation_is_joined:
                source_relation = self._build_inline_null_source_relation(
                    source_relation,
                    sorted(inline_source_columns),
                )

            # Build the view SQL
            view_fq = self._generate_measure_view_name(model_name, measure_name)

            # GROUP BY: only explicit dimensions, or pure aggregate
            group_by_cols = self._resolve_group_by_columns(metric, dataset)

            select_parts: list[str] = []
            if group_by_cols:
                for gc in group_by_cols:
                    select_parts.append(f"    `{gc}`")
            if " AS `" in sql_view_expr and "TRANSLATION WARNING" in sql_view_expr:
                select_parts.append(sql_view_expr)
            else:
                select_parts.append(f"    {sql_view_expr} AS `{measure_name}`")

            select_clause = ",\n".join(select_parts)
            view_sql = f"CREATE OR REPLACE VIEW {view_fq} AS\nSELECT\n{select_clause}\nFROM {source_relation}"

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
        metric_name_index = self._build_metric_name_index(sml_model)
        dataset_attempted = 0
        dataset_skipped_no_metrics = 0
        dataset_skipped_missing_source = 0

        # Group metrics by dataset
        metrics_by_dataset: dict[str, list[SMLMetric]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if not ds_name:
                continue
            metrics_by_dataset.setdefault(ds_name, []).append(metric)

        ordered_datasets: list[tuple[str, SMLDataset]] = []
        for dataset in sml_model.datasets:
            ds_name = self._sanitize_identifier(dataset.unique_name)
            if not ds_name:
                continue
            ordered_datasets.append((ds_name, dataset))

        for ds_name, dataset in ordered_datasets:
            metrics = metrics_by_dataset.get(ds_name, [])
            if not metrics and not self._dbx_behavior.emit_metric_views_for_all_datasets:
                dataset_skipped_no_metrics += 1
                logger.info(
                    "Skipping dataset '%s' in combined mode: no metrics found",
                    dataset.unique_name,
                )
                continue

            dataset_attempted += 1

            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)
            if not existing_source:
                dataset_skipped_missing_source += 1
                if metrics:
                    for m in metrics:
                        skipped_count += 1
                        skipped_details.append({
                            "name": metric_name_index.get(
                                id(m),
                                self._normalize_metric_identifier(m.unique_name),
                            ),
                            "reason": DEPLOY_REASON_PREREQUISITE_MISSING,
                        })
                logger.warning(
                    "Skipped combined SQL dataset '%s': source prerequisite missing (expected=%s)",
                    dataset.unique_name,
                    expected_source,
                )
                continue

            source_table = existing_source
            source_relation = self._build_reconciled_sql_source_relation(dataset, source_table)
            source_relation_is_joined = False
            inline_source_columns: set[str] = set()

            measure_expressions: list[str] = []
            combined_group_by: set[str] = set()
            used_projection_names: set[str] = set()

            if not metrics:
                safe_measure_alias = self._make_unique_projected_name(
                    "total_rows",
                    used_projection_names,
                    suffix="metric",
                )
                measure_expressions.append(f"    COUNT(*) AS `{safe_measure_alias}`")
                logger.info(
                    "Combined mode emitted synthetic measure for dataset '%s' (no metrics)",
                    dataset.unique_name,
                )

            for metric in metrics:
                measure_name = metric_name_index.get(
                    id(metric),
                    self._normalize_metric_identifier(metric.unique_name),
                )

                is_valid, error_reason = self._validate_measure_dependencies(metric, sml_model)
                if not is_valid:
                    skipped_count += 1
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_VALIDATION_FAILED,
                    })
                    continue

                measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                    metrics,
                    metric,
                )
                sql_expr, translation_type = self._resolve_measure_sql(
                    metric,
                    dataset,
                    measure_sql_map=measure_sql_map,
                    allow_simple_sum_translation=not self._dbx_behavior.metric_view_only_sum_translation,
                )
                if not sql_expr:
                    sql_view_expr = None
                else:
                    sql_view_expr = self._rewrite_sql_view_measure_expression(
                        sql_expr,
                        dataset,
                        source_table,
                    )
                    if not sql_view_expr:
                        sql_view_expr = self._build_scalar_subquery_aggregate_expression(
                            sql_expr,
                            dataset,
                            sml_model,
                        )
                    if not dataset.columns:
                        inline_source_columns.update(
                            self._extract_inline_source_columns_from_sql_expression(sql_expr)
                        )
                    if not sql_view_expr and self._dbx_behavior.enable_cross_table_joins:
                        resolved_cross_table = self._resolve_cross_table_query(sql_expr, dataset, sml_model)
                        if resolved_cross_table:
                            sql_view_expr, source_relation = resolved_cross_table
                            source_relation_is_joined = True

                if not sql_view_expr:
                    if self._dbx_behavior.enable_low_confidence_drafts:
                        measure_expressions.append(
                            self._build_draft_measure_expression(
                                metric,
                                measure_name,
                                metric.sync_failure_reason
                                or DEPLOY_REASON_DAX_NOT_SUPPORTED,
                            )
                        )
                    else:
                        skipped_count += 1
                        skipped_details.append({
                            "name": measure_name,
                            "reason": DEPLOY_REASON_CROSS_TABLE if sql_expr else DEPLOY_REASON_DAX_NOT_SUPPORTED,
                            "translation_type": translation_type,
                        })
                    continue

                group_cols = self._resolve_group_by_columns(metric, dataset)
                combined_group_by.update(group_cols)
                for gc in group_cols:
                    if gc:
                        used_projection_names.add(str(gc).upper())

                safe_measure_alias = self._make_unique_projected_name(
                    measure_name,
                    used_projection_names,
                    suffix="metric",
                )
                measure_expressions.append(f"    {sql_view_expr} AS `{safe_measure_alias}`")

            if not measure_expressions:
                continue

            if not dataset.columns and inline_source_columns and not source_relation_is_joined:
                source_relation = self._build_inline_null_source_relation(
                    source_relation,
                    sorted(inline_source_columns),
                )

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
            view_sql = f"CREATE OR REPLACE VIEW {view_fq} AS\nSELECT\n{select_clause}\nFROM {source_relation}"

            if sorted_group_by:
                group_clause = ", ".join(f"`{gc}`" for gc in sorted_group_by)
                view_sql += f"\nGROUP BY {group_clause}"

            stmts.append(view_sql)
            created += 1
            logger.info("Created combined measure view: %s", view_fq)

        logger.info(
            "Combined dataset coverage for model '%s': expected=%d, attempted=%d, created=%d, "
            "skipped_no_metrics=%d, skipped_missing_source=%d",
            sml_model.unique_name,
            len(ordered_datasets),
            dataset_attempted,
            created,
            dataset_skipped_no_metrics,
            dataset_skipped_missing_source,
        )

        return stmts, created, skipped_count, skipped_details

    def _resolve_group_by_columns(
        self,
        metric: SMLMetric,
        dataset: Optional[SMLDataset],
    ) -> list[str]:
        """Resolve GROUP BY columns for a measure view.

        Logic:
                    if not sql_view_expr:
                        sql_view_expr = self._build_scalar_subquery_aggregate_expression(
                            sql_expr,
                            dataset,
                            sml_model,
                        )
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
        model_table = self._metadata_table_fq_name(model_name)

        if self._dbx_behavior.create_metadata_table:
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

        if not self._dbx_behavior.create_metadata_table:
            stmts.extend(view_stmts)
            return stmts

        # Build lookup for skipped measure details
        skipped_lookup: dict[str, str] = {
            d["name"]: d["reason"] for d in skipped_details
        }
        metric_name_index = self._build_metric_name_index(sml_model)

        metrics_by_dataset: dict[str, list[Any]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if not ds_name:
                continue
            metrics_by_dataset.setdefault(ds_name, []).append(metric)

        # Build set of measures that got views + their translation types
        deployed_measures: dict[str, str] = {}  # name -> translation_type
        for metric in sml_model.metrics:
            m_name = metric_name_index.get(
                id(metric),
                self._normalize_metric_identifier(metric.unique_name),
            )
            if m_name not in skipped_lookup:
                # Check if it could have been deployed (has valid SQL)
                dataset = sml_model.get_dataset(metric.dataset)
                dataset_key = self._sanitize_identifier(metric.dataset)
                measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                    metrics_by_dataset.get(dataset_key, []),
                    metric,
                )
                sql_expr, translation_type = self._resolve_measure_sql(
                    metric,
                    dataset,
                    measure_sql_map=measure_sql_map,
                    allow_simple_sum_translation=(
                        view_type_override != VIEW_TYPE_SQL
                        or not self._dbx_behavior.metric_view_only_sum_translation
                    ),
                )
                if sql_expr:
                    deployed_measures[m_name] = translation_type

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
                m_name = metric_name_index.get(
                    id(metric),
                    self._normalize_metric_identifier(metric.unique_name),
                )
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

    def execute_statements(self, statements: list[str], concurrent: bool = False) -> list[dict[str, Any]]:
        """Execute SQL statements via Databricks SQL Statements API.
        
        Optimized to use requests.Session() for native TCP Keep-Alive connection pooling 
        (bypassing TLS handshakes).
        
        If concurrent=True, utilizes a ThreadPoolExecutor to deeply fan-out independent 
        Execution statements, crushing wait_timeouts natively. 
        If concurrent=False, executes strictly sequentially to preserve DDL temporal dependencies.
        
        Includes thread-safe MSAL token resilience.
        """
        if not statements:
            return []

        endpoint = f"{self.config.api_base_url}/api/2.0/sql/statements"
        results: list[dict[str, Any]] = []

        def _fire_request(sql: str, is_retry: bool = False) -> dict[str, Any]:
            payload = {
                "statement": sql,
                "warehouse_id": self.config.warehouse_id,
                "wait_timeout": "30s",
            }
            
            headers = self._headers()
            
            # Use TCP Pooled Session to entirely bypass HTTPS TLS handshakes
            resp = self.session.post(endpoint, headers=headers, json=payload, timeout=60)
            
            # If 401, handle token refresh explicitly precisely once across threads via lock
            if resp.status_code == 401 and not is_retry:
                auth_type = getattr(self.config, "auth_type", "pat")
                with self._auth_lock:
                    # Double-check inside lock to see if another thread already refreshed
                    refreshed_headers = self._headers()
                    if headers.get("Authorization") != refreshed_headers.get("Authorization"):
                        return _fire_request(sql, is_retry=True)

                    if auth_type == "interactive":
                        # Multi-account mode: config.token was already injected
                        # by scoped_account_env — avoid hitting global CredentialManager.
                        # Only fall back to CM if config.token is empty (legacy flow).
                        if self.config.token is None:
                            logger.warning("Databricks API returned 401. Forcing MSAL token refresh...")
                            from semabridge.repository.credential_manager import CredentialManager
                            cm = CredentialManager()
                            if cm.refresh_databricks_token():
                                logger.info("Token refresh successful. Retrying...")
                                return _fire_request(sql, is_retry=True)
                            else:
                                raise DatabricksPublishError(
                                    "Databricks session expired and automatic refresh failed."
                                )

                    # For PAT/service_principal 401s, just reraise
                    raise DatabricksPublishError(
                        f"Databricks API returned 401 Unauthorized (auth_type={auth_type})"
                    )

            if resp.status_code >= 400:
                raise DatabricksPublishError(
                    f"Databricks statement failed ({resp.status_code}): {resp.text[:500]}"
                )

            data = resp.json()
            statement_id = data.get("statement_id")
            state = (data.get("status") or {}).get("state")
            
            # Async polling loop: If Databricks Serverless falls behind, elegantly poll it.
            while state in {"PENDING", "RUNNING"} and statement_id:
                import time
                time.sleep(3)
                headers = self._headers()
                poll_resp = self.session.get(f"{endpoint}/{statement_id}", headers=headers, timeout=60)
                if poll_resp.status_code >= 400:
                    raise DatabricksPublishError(
                        f"Databricks poll failed ({poll_resp.status_code}): {poll_resp.text[:500]}"
                    )
                data = poll_resp.json()
                state = (data.get("status") or {}).get("state")

            if state and state not in {"SUCCEEDED"}:
                err = data.get("status", {}).get("error") or "unknown error"
                raise DatabricksPublishError(f"Databricks statement state={state}: {err}")

            return data

        if concurrent:
            worker_count = min(20, len(statements))
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [pool.submit(_fire_request, sql) for sql in statements]
                for future in as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception as e:
                        raise e
        else:
            for sql in statements:
                results.append(_fire_request(sql))
        return results


    def publish(self, sml_model: SMLModel) -> str:
        """Generate and execute Databricks statements.

        Three-phase deployment:
            1. Probe runtime for metric view support (if auto)
            2. Run quota-aware preflight for Databricks artifacts
            3. Deploy metadata table and measure views

        Returns:
            A URI identifying the deployed artifact.
        """
        original_view_mode = self._dbx_behavior.measure_view_mode
        try:
            # Phase 0: Resolve view type (probe runtime if "auto")
            resolved_view_type = self._determine_view_type()
            logger.info(
                "📊 View technology resolved: %s (config=%s)",
                resolved_view_type,
                self._dbx_behavior.measure_view_type,
            )

            # Pre-flight quota check to auto-switch measure_view_mode if schemas are too big
            selected_view_mode = self._select_measure_view_mode_for_quota(
                sml_model,
                resolved_view_type=resolved_view_type,
            )
            configured_mode = str(original_view_mode or "").strip().lower() or "per_measure"
            if selected_view_mode != configured_mode:
                logger.warning(
                    "Databricks quota preflight selected measure_view_mode=%s for model '%s' (configured=%s)",
                    selected_view_mode,
                    sml_model.unique_name,
                    original_view_mode,
                )
                self._dbx_behavior.measure_view_mode = selected_view_mode

            # Databricks pre-flight: ensure missing sources have a usable table/view shape
            self._auto_initialize_missing_tables(sml_model)
            self._validate_source_table_schema(sml_model)

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
                "metadata_statements=%s measure_views=%s view_type=%s view_mode=%s",
                sml_model.unique_name,
                len(metadata_stmts),
                len(view_stmts),
                resolved_view_type,
                self._dbx_behavior.measure_view_mode,
            )

            # Phase 1: Metadata table — mandatory, fails the deploy if broken
            self.execute_statements(metadata_stmts)
            logger.info(
                "✅ Metadata table deployed successfully for model '%s'",
                sml_model.unique_name,
            )

            # Phase 2: Measure views — parallel deployment with retry/backoff.
            # Uses the team's concurrency framework (RetryManager + ErrorClassifier)
            # and ThreadPoolExecutor for I/O-bound DDL calls.
            from semabridge.core.concurrency.retry_manager import RetryManager
            from semabridge.core.concurrency.error_classifier import ErrorClassifier
            from semabridge.core.concurrency.models import RetryConfig

            MAX_VIEW_WORKERS = 10
            import os
            is_test = "PYTEST_CURRENT_TEST" in os.environ
            retry_mgr = RetryManager(
                config=RetryConfig(
                    max_retries=3, 
                    base_delay=0.001 if is_test else 0.5, 
                    max_delay=0.001 if is_test else 10.0
                ),
                classifier=ErrorClassifier(),
            )

            views_success = 0
            views_failed = 0
            failed_view_names: list[str] = []

            def _deploy_single_view(view_sql: str) -> tuple[str, bool, str]:
                """Deploy a single view with retry via RetryManager.

                Returns:
                    (view_name, success, error_message)
                """
                view_name = "unknown"
                match = re.search(r'VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)', view_sql)
                if match:
                    view_name = match.group(1)

                try:
                    retry_mgr.execute_with_retry(
                        operation=lambda: self.execute_statements([view_sql]),
                        operation_name=f"deploy_view:{view_name}",
                    )
                    return view_name, True, ""
                except Exception as exc:
                    return view_name, False, str(exc)[:300]

            if view_stmts:
                worker_count = min(MAX_VIEW_WORKERS, len(view_stmts))
                logger.info(
                    "🚀 Deploying %d measure views in parallel (workers=%d)",
                    len(view_stmts), worker_count,
                )
                deploy_start = time.monotonic()

                with ThreadPoolExecutor(max_workers=worker_count) as pool:
                    futures = {
                        pool.submit(_deploy_single_view, sql): sql
                        for sql in view_stmts
                    }
                    for future in as_completed(futures):
                        view_name, success, error_msg = future.result()
                        if success:
                            views_success += 1
                            logger.info("✅ Deployed measure view: %s", view_name)
                        else:
                            views_failed += 1
                            failed_view_names.append(view_name)
                            logger.warning(
                                "⚠️  Measure view %s failed: %s",
                                view_name, error_msg,
                            )

                deploy_elapsed = time.monotonic() - deploy_start
                logger.info(
                    "📊 View deployment complete: %d/%d succeeded in %.1fs",
                    views_success, len(view_stmts), deploy_elapsed,
                )

            if views_failed:
                failed_views_preview = ", ".join(failed_view_names[:5])
                logger.warning(
                    "⚠️  %d/%d measure views failed (source table/column mismatch in "
                    "Databricks). Failed views: %s%s. Metadata table deployed OK. "
                    "Verify source table names and physical column names used by "
                    "the semantic model.",
                    views_failed,
                    len(view_stmts),
                    failed_views_preview or "unknown",
                    "..." if len(failed_view_names) > 5 else "",
                )

            if (
                resolved_view_type == VIEW_TYPE_METRIC
                and view_stmts
                and views_failed > 0
            ):
                logger.warning(
                    "Native Databricks metric-view deployment had %d failed "
                    "view(s). Falling back to SQL views for model '%s' to keep "
                    "measure sync complete.",
                    views_failed,
                    sml_model.unique_name,
                )
                fallback_view_stmts, _, _, _ = self.generate_measure_view_statements(
                    sml_model,
                    view_type_override=VIEW_TYPE_SQL,
                )

                fallback_failed = 0
                if fallback_view_stmts:
                    sql_debug_artifacts: list[str] = []
                    for view_sql in fallback_view_stmts:
                        artifact_path = self._persist_sql_debug_artifact(sml_model.unique_name, view_sql)
                        sql_debug_artifacts.append(artifact_path or "")
                        if artifact_path:
                            logger.info("Saved SQL fallback debug statement: %s", artifact_path)

                    logger.info("Deploying %d SQL fallback views concurrently...", len(fallback_view_stmts))
                    try:
                        self.execute_statements(fallback_view_stmts, concurrent=True)
                        logger.info("✓ All %d SQL fallback views deployed successfully", len(fallback_view_stmts))
                    except DatabricksPublishError as exc:
                        # Fall back to individual deployment for better diagnostics.
                        logger.warning("Concurrent fallback deployment failed: %s. Attempting individual deployment...", str(exc)[:200])
                        for idx, view_sql in enumerate(fallback_view_stmts):
                            view_name = "unknown"
                            match = re.search(r'VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)', view_sql)
                            if match:
                                view_name = match.group(1)
                            try:
                                self.execute_statements([view_sql])
                                logger.info("Deployed SQL fallback view: %s", view_name)
                            except DatabricksPublishError as e:
                                fallback_failed += 1
                                self._persist_sql_debug_error_artifact(
                                    sql_debug_artifacts[idx] if idx < len(sql_debug_artifacts) else "",
                                    str(e),
                                )
                                logger.warning(
                                    "SQL fallback view %s failed. Error: %s",
                                    view_name,
                                    str(e)[:200],
                                )

                if fallback_failed:
                    logger.warning(
                        "%d/%d SQL fallback views also failed for model '%s'.",
                        fallback_failed,
                        len(fallback_view_stmts),
                        sml_model.unique_name,
                    )

            return f"databricks://{self.config.catalog}/{self.config.schema_name}/{sml_model.unique_name}"
        finally:
            self._dbx_behavior.measure_view_mode = original_view_mode

