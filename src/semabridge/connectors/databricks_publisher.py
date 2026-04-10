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
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.connectors.schema_reconciler import MissingPrerequisiteException, SchemaMapper
from semabridge.sml.models import AggregationType, SMLDataset, SMLMetric, SMLModel
from semabridge.utils.naming import to_alias
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


DEFAULT_DATABRICKS_SCHEMA_OBJECT_LIMIT = 100


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

        dataset_candidates = {
            self._sanitize_identifier(dataset.unique_name).lower(),
            self._sanitize_identifier(dataset.source_table or dataset.unique_name).lower(),
        }
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
        mapper = SchemaMapper(expected_columns=expected_columns, available_columns=sorted(actual_columns))
        mapped, missing = mapper.build_mapping()

        if missing and str(self._dbx_behavior.on_missing_source or "plan").strip().lower() == "fail":
            try:
                mapper.generate_view_sql(
                    source_table=source_fq,
                    target_view="`semabridge`.`tmp`.`schema_reconcile_probe`",
                    missing_strategy="raise",
                )
            except MissingPrerequisiteException as exc:
                raise DatabricksPublishError(
                    "Databricks source prerequisites missing: "
                    f"{exc.to_dict()}"
                ) from exc

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
        stmt = (
            f"SELECT column_name FROM {catalog}.information_schema.columns "
            f"WHERE table_schema = '{schema_name}' AND table_name = '{table_name}'"
        )

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
        stmt = (
            f"SELECT COUNT(1) AS cnt FROM {catalog}.information_schema.tables "
            f"WHERE table_schema = '{schema_name}' AND table_name = '{table_name}'"
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
            return int(data_array[0][0]) > 0
        except (TypeError, ValueError):
            return None

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

        if not init_statements:
            return

        self.execute_statements(init_statements)
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

        for col in dataset.columns:
            projected_name = self._make_unique_projected_name(
                col.unique_name,
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
            column_name = match.group(1) or match.group(2) or ""
            projected = _resolve_projected_name(column_name)
            if not projected:
                return match.group(0)
            return f"`{projected}`"

        expr = quoted_pattern.sub(_replace_quoted, expr)
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
            column_name = match.group(1) or match.group(2) or ""
            resolved_column = _resolve_column_name(column_name)
            if not resolved_column:
                return f"`{self._sanitize_identifier(column_name)}`"
            return f"`{resolved_column}`"

        return quoted_pattern.sub(_replace_quoted, expr)

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
            if self._requires_source_alias_query(source_fq, bindings):
                # Use an aliasing source query when mapped physical columns do
                # not match semantic names.
                lines.append("source: " + self._build_metric_view_source_query(bindings, source_fq))
            else:
                lines.append(f"source: {source_fq.replace('`', '')}")
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
                lines.append(f"source: {source_for_yaml}")

        lines.append("")
        lines.append("dimensions:")

        for binding in bindings:
            if not binding.include_as_dimension:
                continue
            lines.append(f"  - name: {binding.projected_name}")
            lines.append(f'    expr: "`{binding.projected_name}`"')

        lines.append("")
        lines.append("measures:")

        for rm in resolved_measures:
            if not rm.sql_expression:
                continue

            if rm.confidence == CONFIDENCE_LOW and self._dbx_behavior.enable_low_confidence_drafts:
                warning = " ".join(rm.warnings).replace('"', "'") if rm.warnings else "LOW CONFIDENCE"
                original_dax = (rm.original_dax or "").replace('"', "'")
                lines.append(f"  # TRANSLATION WARNING: {warning}")
                if original_dax:
                    lines.append(f"  # Original DAX: {original_dax[:200]}")

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

            # Resolve source table
            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)
            if not existing_source:
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
                    "Skipped metric-view dataset '%s': source prerequisite missing (expected=%s)",
                    dataset.unique_name,
                    expected_source,
                )
                continue

            source_fq = existing_source
            self._reconcile_dataset_schema(dataset, source_fq)

            bindings = self._build_metric_view_column_bindings(dataset, source_fq, sml_model)
            # Resolve all measures for this dataset
            resolved: list[ResolvedMeasure] = []
            for metric in metrics:
                m_name = metric_name_index.get(
                    id(metric),
                    self._normalize_metric_identifier(metric.unique_name),
                )

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
        from_relation = f"{source_by_dataset[base_dataset_name]} AS `{alias_by_dataset[base_dataset_name]}`"
        included = {base_dataset_name}
        join_lines: list[str] = []

        for target in sorted(tables_involved):
            if target in included:
                continue
            path = self._find_relationship_path(graph, base_dataset_name, target)
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
        ref_matches = re.findall(
            r'(?:`([^`]+)`|"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:`[^`]+`|"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)',
            sql_expr,
        )
        unique_tables = {
            str(prefix_bt or prefix_dq or prefix or "").strip().lower()
            for prefix_bt, prefix_dq, prefix in ref_matches
            if str(prefix_bt or prefix_dq or prefix or "").strip()
        }
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

    # ── Parallel Metric Resolution (CP-Level Optimization) ──────────────────

    def _resolve_metric_sql_cached(self, metric: SMLMetric, sml_model: SMLModel) -> dict:
        """Resolve SQL for one metric in parallel context.
        
        Returns dict with metric id, sql_expr, translation_type for thread-safe result passing.
        """
        dataset = sml_model.get_dataset(metric.dataset)
        sql_expr, translation_type = self._resolve_measure_sql(metric, dataset)
        return {
            "metric_id": id(metric),
            "metric": metric,
            "sql_expr": sql_expr,
            "translation_type": translation_type,
            "dataset": dataset,
        }

    def _resolve_all_metrics_parallel(self, sml_model: SMLModel, max_workers: int = 8) -> dict:
        """Resolve SQL for all metrics in parallel (CP-Level: 8 workers for DAX translation).
        
        Parallelizes the heavy DAX→SQL translation work across multiple threads.
        Each worker independently translates a metric's DAX expression.
        """
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._resolve_metric_sql_cached, metric, sml_model)
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
        """One view per measure (default mode) - with parallel metric resolution."""
        stmts: list[str] = []
        created = 0
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []
        metric_name_index = self._build_metric_name_index(sml_model)

        # CP-LEVEL: Resolve all metrics in parallel (8 workers for DAX translation)
        logger.info("Resolving SQL for %d metrics in parallel (8 workers)...", len(sml_model.metrics))
        metric_sql_cache = self._resolve_all_metrics_parallel(sml_model, max_workers=8)

        for metric in sml_model.metrics:
            measure_name = metric_name_index.get(
                id(metric),
                self._normalize_metric_identifier(metric.unique_name),
            )
            dataset = sml_model.get_dataset(metric.dataset)

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

            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)
            if not existing_source:
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

            measure_expressions: list[str] = []
            combined_group_by: set[str] = set()
            used_projection_names: set[str] = set()

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

                sql_expr, translation_type = self._resolve_measure_sql(metric, dataset)
                if not sql_expr:
                    sql_view_expr = None
                else:
                    sql_view_expr = self._rewrite_sql_view_measure_expression(
                        sql_expr,
                        dataset,
                        source_table,
                    )
                    if not sql_view_expr and self._dbx_behavior.enable_cross_table_joins:
                        resolved_cross_table = self._resolve_cross_table_query(sql_expr, dataset, sml_model)
                        if resolved_cross_table:
                            sql_view_expr, source_relation = resolved_cross_table

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
        metric_name_index = self._build_metric_name_index(sml_model)

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

    def _execute_single_statement(self, payload: dict[str, Any], endpoint: str, is_retry: bool = False) -> dict[str, Any]:
        """Execute single statement with token refresh on 401."""
        headers = self._headers()
        resp = self._session_pool.post(endpoint, headers=headers, json=payload, timeout=60)
        
        # If 401, handle token refresh explicitly
        if resp.status_code == 401 and not is_retry:
            auth_type = getattr(self.config, "auth_type", "pat")
            if auth_type == "interactive":
                logger.warning("Databricks API returned 401 Unauthorized. Forcing MSAL token refresh...")
                from semabridge.repository.credential_manager import CredentialManager
                cm = CredentialManager()
                if cm.refresh_databricks_token():
                    logger.info("Token refresh successful. Retrying Databricks API call...")
                    return self._execute_single_statement(payload, endpoint, is_retry=True)
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

    def execute_statements(self, statements: list[str], batch_size: int = 10, max_workers: int = 5) -> list[dict[str, Any]]:
        """Execute SQL statements via Databricks SQL Statements API with batching and parallelization.
        
        Strategy:
        1. Batch statements (10 per batch) to reduce overhead
        2. Execute batches in parallel (5 workers) for concurrency
        3. Reuse HTTP connections via session pool
        
        Args:
            statements: List of SQL statements to execute
            batch_size: Statements to batch together (default 10)
            max_workers: Parallel executor threads (default 5)
        
        Returns:
            List of Databricks API responses
        """
        if not statements:
            return []
        
        endpoint = f"{self.config.api_base_url}/api/2.0/sql/statements"
        results: list[dict[str, Any]] = []
        
        # Batch statements together
        batches = [statements[i:i + batch_size] for i in range(0, len(statements), batch_size)]
        
        def _execute_batch(batch: list[str]) -> list[dict[str, Any]]:
            """Execute one batch of statements."""
            batch_results = []
            for sql in batch:
                payload = {
                    "statement": sql,
                    "warehouse_id": self.config.warehouse_id,
                    "wait_timeout": "30s",
                }
                batch_results.append(self._execute_single_statement(payload, endpoint))
            return batch_results
        
        # Execute batches in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_execute_batch, batch) for batch in batches]
            for future in as_completed(futures):
                results.extend(future.result())
        
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
            resolved_view_type = self._determine_view_type()
            logger.info(
                "View technology resolved: %s (config=%s)",
                resolved_view_type,
                self._dbx_behavior.measure_view_type,
            )

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

            self._auto_initialize_missing_tables(sml_model)
            self._validate_source_table_schema(sml_model)

            statements = self.generate_sql_statements(
                sml_model, view_type_override=resolved_view_type,
            )

            metadata_stmts = [
                s for s in statements
                if not s.strip().startswith("CREATE OR REPLACE VIEW")
            ]
            view_stmts = [
                s for s in statements
                if s.strip().startswith("CREATE OR REPLACE VIEW")
            ]

            logger.info(
                "Publishing semantic model to Databricks: model=%s metadata_statements=%s "
                "measure_views=%s view_type=%s view_mode=%s",
                sml_model.unique_name,
                len(metadata_stmts),
                len(view_stmts),
                resolved_view_type,
                self._dbx_behavior.measure_view_mode,
            )

            self.execute_statements(metadata_stmts)
            logger.info(
                "Metadata table deployed successfully for model '%s'",
                sml_model.unique_name,
            )

            views_success = 0
            views_failed = 0
            
            # Deploy all views in parallel batches instead of one-by-one
            if view_stmts:
                logger.info("Deploying %d measure views in parallel batches (batch_size=10, workers=5)...", len(view_stmts))
                try:
                    # Execute all views in batches of 10 with 5 parallel workers
                    self.execute_statements(view_stmts, batch_size=10, max_workers=5)
                    views_success = len(view_stmts)
                    logger.info("✓ All %d measure views deployed successfully", len(view_stmts))
                except DatabricksPublishError as exc:
                    # If batch fails, fall back to individual deployment for error tracking
                    logger.warning("Batch view deployment failed: %s. Attempting individual deployment...", str(exc)[:200])
                    for view_sql in view_stmts:
                        view_name = "unknown"
                        match = re.search(r'VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)', view_sql)
                        if match:
                            view_name = match.group(1)
                        try:
                            self.execute_statements([view_sql])
                            views_success += 1
                            logger.info("Deployed measure view: %s", view_name)
                        except DatabricksPublishError as e:
                            views_failed += 1
                            logger.warning(
                                "Measure view %s skipped because Databricks rejected the deployment. Error: %s",
                                view_name,
                                str(e)[:200],
                            )

            if views_failed:
                logger.warning(
                    "%d/%d measure views failed in Databricks. Metadata table deployed OK.",
                    views_failed,
                    len(view_stmts),
                )

            if (
                resolved_view_type == VIEW_TYPE_METRIC
                and view_stmts
                and views_success == 0
            ):
                logger.warning(
                    "Native Databricks metric-view deployment produced 0 successful "
                    "views. Falling back to SQL views for model '%s'.",
                    sml_model.unique_name,
                )
                fallback_view_stmts, _, _, _ = self.generate_measure_view_statements(
                    sml_model,
                    view_type_override=VIEW_TYPE_SQL,
                )

                fallback_failed = 0
                if fallback_view_stmts:
                    logger.info("Deploying %d SQL fallback views in parallel batches...", len(fallback_view_stmts))
                    try:
                        # Deploy fallback views in parallel batches
                        self.execute_statements(fallback_view_stmts, batch_size=10, max_workers=5)
                        logger.info("✓ All %d SQL fallback views deployed successfully", len(fallback_view_stmts))
                    except DatabricksPublishError as exc:
                        # Fall back to individual deployment
                        logger.warning("Batch fallback deployment failed: %s. Attempting individual deployment...", str(exc)[:200])
                        for view_sql in fallback_view_stmts:
                            view_name = "unknown"
                            match = re.search(r'VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)', view_sql)
                            if match:
                                view_name = match.group(1)
                            try:
                                self.execute_statements([view_sql])
                                logger.info("Deployed SQL fallback view: %s", view_name)
                            except DatabricksPublishError as e:
                                fallback_failed += 1
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

