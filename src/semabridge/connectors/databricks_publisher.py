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
import os
from pathlib import Path
import re
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.connectors.databricks_measure_translation import (
    DatabricksMeasureTranslator,
    TIER_DEFERRED,
    TIER_RELATIONSHIP_AWARE_FILTERED_AGGREGATION,
)
from semabridge.connectors.schema_reconciler import SchemaMapper
from semabridge.connectors.fact_table_naming import is_fact_like_name, tokenize_dataset_name
from semabridge.repository.orm.session_factory import db_manager
from semabridge.repository.semantic_routing_repository import RouterDecision, SemanticRoutingRepository
from semabridge.sml.models import AggregationType, SMLDataset, SMLJoin, SMLMetric, SMLModel
from semabridge.utils.dimension_injector import DimensionInjector
from semabridge.utils.naming import to_alias
from semabridge.utils.join_builder import JoinTreeBuilder
from semabridge.utils.logger import get_logger
from semabridge.utils.measure_fact_mapping import MeasureFactMapping
from semabridge.utils.semantic_graph import SemanticGraph
from semabridge.utils.table_categorizer import TableCategorizer

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
DEPLOY_REASON_GRAPH_INTEGRITY = "GRAPH_INTEGRITY"

# ── Translation Type Constants ───────────────────────────────────────────────
TRANSLATION_TYPE_SQL_NATIVE = "SQL_NATIVE"
TRANSLATION_TYPE_AGGREGATION_BUILT = "AGGREGATION_BUILT"
TRANSLATION_TYPE_DAX_TRANSLATED = "DAX_TRANSLATED"
TRANSLATION_TYPE_DAX_LLM = "DAX_LLM_TRANSLATED"
TRANSLATION_TYPE_DAX_SKIPPED = "DAX_SKIPPED"
TRANSLATION_TYPE_PRECOMPUTED = "PRECOMPUTED_SQL"
TRANSLATION_TYPE_PRECOMPUTE_REQUIRED = "PRECOMPUTE_REQUIRED"

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

# ── Metric-View Deploy Error Classes ───────────────────────────────────────
ERROR_CLASS_SQL_SEMANTIC = "SQL_SEMANTIC"
ERROR_CLASS_MISSING_ENTITY = "MISSING_ENTITY"
ERROR_CLASS_SOURCE_MISMATCH = "SOURCE_MISMATCH"
ERROR_CLASS_GENERIC = "GENERIC"

# ── SQL Fallback States ────────────────────────────────────────────────────
SQL_FALLBACK_NOT_TRIGGERED = "NOT_TRIGGERED"
SQL_FALLBACK_IN_PROGRESS = "FALLBACK_IN_PROGRESS"
SQL_FALLBACK_SUCCESS = "FALLBACK_SUCCESS"
SQL_FALLBACK_FAILED = "FALLBACK_FAILED"
SQL_FALLBACK_SKIPPED = "FALLBACK_SKIPPED"


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
        self._semantic_graph: SemanticGraph | None = None
        self._table_categories: dict[str, dict[str, str]] = {}
        self._per_fact_metric_views: dict[str, str] = {}
        self._last_review_required_summary: dict[str, Any] = {}
        self._last_routing_summary: dict[str, int] = {}
        self._last_skipped_details: list[dict[str, str]] = []
        self._last_sql_fallback_state: str = SQL_FALLBACK_NOT_TRIGGERED
        self._last_sql_fallback_reason: str = ""
        # Lazily created and reused across every
        # _try_llm_metric_fallback_expression call made through this
        # instance (one per deploy), so a provider's per-run "unavailable
        # after auth failure" cache (Tier5Service._unavailable_providers)
        # spans the whole deploy instead of resetting on every measure.
        self._tier5_service = None

        # Initialize network pooling and thread-safe OAuth recovery
        self.session = requests.Session()
        self._auth_lock = threading.Lock()

    def _get_tier5_service(self):
        if self._tier5_service is None:
            from semabridge.dax_translation.tier5.service import Tier5Service
            self._tier5_service = Tier5Service()
        return self._tier5_service

    @staticmethod
    def _reason_action_hint(reason: str) -> str:
        """Return a short operator action hint for a skip reason."""
        mapping = {
            DEPLOY_REASON_DAX_NOT_SUPPORTED: "Rewrite metric expression using supported SQL aggregation patterns.",
            DEPLOY_REASON_VALIDATION_FAILED: "Review dataset/metric references and ensure all source objects exist.",
            DEPLOY_REASON_CROSS_TABLE: "Enable SQL fallback mode or refactor cross-table measure dependencies.",
            DEPLOY_REASON_PREREQUISITE_MISSING: "Verify relationship graph prerequisites and required source objects.",
            DEPLOY_REASON_SOURCE_NOT_FOUND: "Create or map the missing source table in the target schema.",
            DEPLOY_REASON_GRAPH_INTEGRITY: "Repair semantic relationship graph integrity before deployment.",
        }
        return mapping.get(str(reason or "").strip(), "Review metric definition and routing prerequisites.")

    def _emit_review_diagnostics(self, skipped_details: list[dict[str, str]]) -> dict[str, Any]:
        """Build and log review-required diagnostics for skipped measures/joins."""
        reason_counts: dict[str, int] = {}
        skipped_measure_names: list[str] = []
        skipped_join_names: list[str] = []

        for item in skipped_details:
            name = str(item.get("name") or "").strip()
            reason = str(item.get("reason") or DEPLOY_REASON_VALIDATION_FAILED).strip()
            item_type = str(item.get("item_type") or "measure").strip().lower()

            if not name:
                continue

            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if item_type == "join":
                skipped_join_names.append(name)
            else:
                skipped_measure_names.append(name)

            logger.warning(
                "Review required (%s): %s skipped due to %s. Suggested action: %s",
                item_type,
                name,
                reason,
                self._reason_action_hint(reason),
            )

        summary = {
            "reason_counts": reason_counts,
            "skipped_measure_names": sorted(set(skipped_measure_names)),
            "skipped_join_names": sorted(set(skipped_join_names)),
        }
        self._last_review_required_summary = summary
        return summary

    def _build_routing_summary(
        self,
        *,
        sml_model: SMLModel,
        view_statement_count: int,
        review_required_summary: dict[str, Any],
    ) -> dict[str, int]:
        """Build lightweight routing summary for API/UI response payloads."""
        source_table_count = len(sml_model.datasets)
        router_active = self._is_semantic_router_active_for_model(sml_model.unique_name) and bool(self._table_categories)

        if router_active:
            fact_table_count = sum(1 for row in self._table_categories.values() if row.get("category") == "FACT")
            dimension_table_count = sum(1 for row in self._table_categories.values() if row.get("category") == "DIMENSION")
            bridge_table_count = sum(1 for row in self._table_categories.values() if row.get("category") == "BRIDGE")
            generated_artifact_count = len(self._per_fact_metric_views) or max(1, view_statement_count)
        else:
            # Backward-compatible legacy mode: treat deploy as a single artifact.
            fact_table_count = 1 if source_table_count > 0 else 0
            dimension_table_count = 0
            bridge_table_count = 0
            generated_artifact_count = 1 if source_table_count > 0 else 0

        review_required_count = len(review_required_summary.get("skipped_measure_names", [])) + len(
            review_required_summary.get("skipped_join_names", [])
        )

        summary = {
            "source_table_count": source_table_count,
            "fact_table_count": fact_table_count,
            "dimension_table_count": dimension_table_count,
            "bridge_table_count": bridge_table_count,
            "review_required_count": review_required_count,
            "generated_artifact_count": generated_artifact_count,
        }
        self._last_routing_summary = summary
        return summary

    def get_last_publish_summary(self) -> dict[str, Any]:
        """Return the last computed publish diagnostics for API callers."""
        return {
            "review_required_summary": dict(self._last_review_required_summary or {}),
            "routing_summary": dict(self._last_routing_summary or {}),
            "sql_fallback_state": self._last_sql_fallback_state,
            "sql_fallback_reason": self._last_sql_fallback_reason,
        }

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

    def _statement_wait_timeout(self) -> str:
        """Return Databricks statement wait timeout in API format."""
        seconds = int(getattr(self._dbx_behavior, "statement_wait_timeout_seconds", 30) or 30)
        if seconds < 1:
            seconds = 1
        return f"{seconds}s"

    def _statement_poll_interval_seconds(self) -> float:
        """Return Databricks statement polling interval in seconds."""
        seconds = float(getattr(self._dbx_behavior, "statement_poll_interval_seconds", 3.0) or 3.0)
        if seconds < 0.25:
            seconds = 0.25
        return seconds

    def _is_semantic_router_active_for_model(self, model_name: str) -> bool:
        """Return True when semantic router should run for this model."""
        if not bool(getattr(self._dbx_behavior, "semantic_router_enabled", False)):
            return False

        disabled_models = {
            str(name or "").strip().upper()
            for name in getattr(self._dbx_behavior, "semantic_router_override_models", [])
            if str(name or "").strip()
        }
        return str(model_name or "").strip().upper() not in disabled_models

    def _build_semantic_router_outputs(self, sml_model: SMLModel) -> tuple[SemanticGraph, dict[str, dict[str, str]]]:
        """Build relationship graph and deterministic table categorization."""
        graph = SemanticGraph(sml_model)
        categorizer = TableCategorizer()
        categories = categorizer.categorize(graph)
        return graph, categories

    def _persist_semantic_router_decisions(
        self,
        *,
        model_name: str,
        categories: dict[str, dict[str, str]],
    ) -> None:
        """Persist graph categorization outputs to repository state."""
        with db_manager.get_session() as session:
            repo = SemanticRoutingRepository(session)
            for table_name in sorted(categories):
                entry = categories[table_name]
                repo.save_routing_decision(
                    RouterDecision(
                        model_name=model_name,
                        table_name=table_name,
                        category=entry.get("category", "UNKNOWN"),
                        confidence=entry.get("confidence", "LOW"),
                        reason_code=entry.get("reason_code", "UNSPECIFIED"),
                    )
                )

    def _initialize_semantic_router(self, sml_model: SMLModel) -> None:
        """Initialize and optionally persist semantic graph outputs."""
        self._semantic_graph = None
        self._table_categories = {}

        if not self._is_semantic_router_active_for_model(sml_model.unique_name):
            logger.debug("Semantic router disabled for model '%s'", sml_model.unique_name)
            return

        graph, categories = self._build_semantic_router_outputs(sml_model)
        self._semantic_graph = graph
        self._table_categories = categories

        logger.info(
            "Semantic router graph initialized for model '%s' (tables=%d, facts=%d)",
            sml_model.unique_name,
            len(graph.tables),
            sum(1 for value in categories.values() if value.get("category") == "FACT"),
        )

        try:
            self._persist_semantic_router_decisions(
                model_name=sml_model.unique_name,
                categories=categories,
            )
            logger.info(
                "Semantic router decisions persisted for model '%s' (rows=%d)",
                sml_model.unique_name,
                len(categories),
            )
        except Exception as exc:
            logger.warning(
                "Semantic router decision persistence failed for model '%s': %s",
                sml_model.unique_name,
                str(exc)[:300],
            )

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
            2. Dataset source_database.source_schema.source_table (if configured)
            3. source_catalog.source_schema.dataset_name (if configured)
            4. Same catalog/schema as metadata table

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

        # Priority 2: Dataset-level source location from extracted model metadata
        dataset_catalog = str(dataset.source_database or "").strip()
        dataset_schema = str(dataset.source_schema or "").strip()
        if dataset_catalog and dataset_schema:
            return f"`{self._sanitize_identifier(dataset_catalog)}`.`{self._sanitize_identifier(dataset_schema)}`.`{ds_name}`"

        # Priority 3: Configured source catalog/schema
        src_catalog = self._dbx_behavior.source_catalog.strip()
        src_schema = self._dbx_behavior.source_schema.strip()
        if src_catalog and src_schema:
            return f"`{src_catalog}`.`{src_schema}`.`{ds_name}`"

        # Priority 4: Same catalog/schema as metadata table
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

    def _infer_inline_null_sql_type(
        self,
        column_name: str,
        sql_expressions: Optional[list[str]] = None,
    ) -> str:
        """Infer a safe placeholder SQL type for inline schemaless source columns."""
        col = self._sanitize_identifier(column_name).lower()
        expr_blob = "\n".join(sql_expressions or []).lower()

        if any(token in col for token in ("date", "time", "timestamp", "refresh", "datetime", "dt")):
            return "TIMESTAMP"
        if col.endswith("_id") or col.startswith("is_"):
            return "BIGINT"
        if "concat(" in expr_blob and col in expr_blob:
            return "STRING"

        return "DOUBLE"

    def _build_inline_null_projection(
        self,
        column_names: list[str],
        sql_expressions: Optional[list[str]] = None,
    ) -> list[str]:
        """Build typed NULL projection entries for schemaless source relations."""
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

        projection: list[str] = []
        for name in deduped:
            inferred_type = self._infer_inline_null_sql_type(name, sql_expressions)
            projection.append(f"CAST(NULL AS {inferred_type}) AS `{name}`")

        return projection

    def _build_inline_null_source_relation(
        self,
        source_fq: str,
        column_names: list[str],
        sql_expressions: Optional[list[str]] = None,
    ) -> str:
        """Build a source relation that projects typed NULL placeholders."""
        projection = self._build_inline_null_projection(column_names, sql_expressions)
        if not projection:
            return source_fq

        select_list = ",\n".join(f"  {entry}" for entry in projection)
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

    # Whole-word terms suggesting a shared column is a genuine join key —
    # see _auto_infer_missing_relationships.
    _JOIN_KEY_TERMS = frozenset({"ID", "KEY", "UNIT", "PERIOD", "DATE"})

    def _auto_infer_missing_relationships(self, sml_model: SMLModel) -> None:
        """Automatically infer and inject missing relationships based on shared key columns.

        Conservative, opt-in only: disabled unless
        ``enable_auto_relationship_inference`` is set, matching the same
        opt-in pattern as the sibling ``_auto_bridge_relationship_join_keys``
        — this fabricates NEW relationships from a name-based heuristic
        with no independent structural signal (no constraint/uniqueness
        metadata available at this layer), so it should never run silently
        by default.
        """
        if not getattr(self._dbx_behavior, "enable_auto_relationship_inference", False):
            return

        from semabridge.sml.models import SMLRelationship, Cardinality

        existing_edges = set()
        for rel in sml_model.relationships:
            if rel.from_dataset and rel.to_dataset:
                existing_edges.add((rel.from_dataset.lower(), rel.to_dataset.lower()))
                existing_edges.add((rel.to_dataset.lower(), rel.from_dataset.lower()))

        datasets = sml_model.datasets
        fact_datasets = [d for d in datasets if d.is_fact or is_fact_like_name(d.unique_name)]
        if not fact_datasets:
            fact_datasets = datasets

        inferred_count = 0
        for ds1 in fact_datasets:
            for ds2 in datasets:
                if ds1.unique_name == ds2.unique_name:
                    continue
                edge = (ds1.unique_name.lower(), ds2.unique_name.lower())
                if edge in existing_edges:
                    continue

                ds1_cols = {str(c.unique_name).lower().replace(" ", "").replace("_", ""): c.unique_name for c in ds1.columns}
                ds2_cols = {str(c.unique_name).lower().replace(" ", "").replace("_", ""): c.unique_name for c in ds2.columns}

                shared_norm = set(ds1_cols.keys()).intersection(set(ds2_cols.keys()))

                join_keys_1 = []
                join_keys_2 = []
                for norm in shared_norm:
                    # Whole-word match against the ORIGINAL (unnormalized)
                    # column names — the aggressively-stripped `norm` key
                    # above (no spaces/underscores at all) is only for
                    # finding same-named columns across datasets; checking
                    # substring containment against it is unbounded (e.g.
                    # "Community" -> "community" contains "unit" mid-word).
                    name1, name2 = ds1_cols[norm], ds2_cols[norm]
                    tokens = set(tokenize_dataset_name(name1)) | set(tokenize_dataset_name(name2))
                    if tokens & self._JOIN_KEY_TERMS:
                        join_keys_1.append(name1)
                        join_keys_2.append(name2)

                if not join_keys_1:
                    continue
                    
                new_rel = SMLRelationship(
                    unique_name=f"auto_inferred_{self._sanitize_identifier(ds1.unique_name)}_to_{self._sanitize_identifier(ds2.unique_name)}",
                    from_dataset=ds1.unique_name,
                    from_columns=join_keys_1,
                    to_dataset=ds2.unique_name,
                    to_columns=join_keys_2,
                    cardinality=Cardinality.MANY_TO_ONE,
                    is_active=True
                )
                sml_model.relationships.append(new_rel)
                existing_edges.add(edge)
                existing_edges.add((edge[1], edge[0]))
                inferred_count += 1
                logger.info(
                    "Auto-inferred missing relationship between %s and %s on columns %s",
                    ds1.unique_name, ds2.unique_name, join_keys_1
                )
        
        if inferred_count > 0:
            logger.info("Automatically inferred and injected %d missing relationships.", inferred_count)

    def _auto_initialize_missing_tables(
        self,
        sml_model: SMLModel,
        resolved_view_type: str = "",
    ) -> None:
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

    def _quote_table_reference(self, source_table: str) -> str:
        """Return a safely quoted Databricks table reference."""
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return str(source_table or "").replace("`", "")
        catalog, schema_name, table_name = parsed
        return f"`{catalog}`.`{schema_name}`.`{table_name}`"

    def _auto_bridge_relationship_join_keys(self, sml_model: SMLModel) -> None:
        """Automatically provision missing physical join-key columns for relationships.

        This preflight is intentionally conservative and only runs when
        ``enable_auto_join_key_bridge`` is enabled.
        """
        if not self._dbx_behavior.create_measure_views:
            return
        if not getattr(self._dbx_behavior, "enable_auto_join_key_bridge", False):
            return

        remediated: list[str] = []

        for rel in sml_model.relationships:
            endpoint_pairs = list(zip(rel.from_columns, rel.to_columns))
            for from_col, to_col in endpoint_pairs:
                endpoints = [
                    (rel.from_dataset, str(from_col or "").strip()),
                    (rel.to_dataset, str(to_col or "").strip()),
                ]

                for dataset_name, semantic_col in endpoints:
                    if not semantic_col:
                        continue

                    dataset = sml_model.get_dataset(dataset_name)
                    if not dataset:
                        continue

                    expected_source = self._resolve_source_table(dataset)
                    source_fq = self._resolve_existing_source_for_dataset(dataset, expected_source)
                    if not source_fq:
                        continue

                    physical_cols = self._get_source_table_columns(source_fq)
                    if not physical_cols:
                        continue

                    resolved_col = self._resolve_physical_source_column(dataset, semantic_col, source_fq)
                    resolved_col = str(resolved_col or "").strip().lower()
                    if not resolved_col or resolved_col in physical_cols:
                        continue

                    table_ref = self._quote_table_reference(source_fq)
                    add_stmt = (
                        f"ALTER TABLE {table_ref} ADD COLUMNS "
                        f"(`{resolved_col}` STRING COMMENT 'Auto-bridged join key for semantic column {semantic_col}')"
                    )

                    add_failed = False
                    try:
                        self.execute_statements([add_stmt])
                    except Exception as exc:
                        error_text = str(exc)
                        lowered = error_text.lower()
                        duplicate_markers = (
                            "already exists",
                            "column already exists",
                            "duplicate column",
                            "field already exists",
                        )
                        if any(marker in lowered for marker in duplicate_markers):
                            logger.info(
                                "Databricks join-key bridge detected existing column for %s.%s -> %s",
                                dataset.unique_name,
                                semantic_col,
                                resolved_col,
                            )
                        else:
                            add_failed = True
                            logger.warning(
                                "Databricks join-key bridge skipped for %s.%s -> %s: %s",
                                dataset.unique_name,
                                semantic_col,
                                resolved_col,
                                error_text[:200],
                            )

                    if add_failed:
                        continue

                    refreshed_cols = self._get_source_table_columns(source_fq) or physical_cols
                    candidate_sources: list[str] = []

                    base = self._infer_physical_source_column_name(semantic_col)
                    if base:
                        candidate_sources.append(base)
                    candidate_sources.extend(self._physical_source_column_candidates(dataset, semantic_col))

                    backfill_col = ""
                    seen: set[str] = set()
                    for candidate in candidate_sources:
                        candidate_key = str(candidate or "").strip().lower()
                        if not candidate_key or candidate_key == resolved_col or candidate_key in seen:
                            continue
                        seen.add(candidate_key)
                        if candidate_key in refreshed_cols:
                            backfill_col = candidate_key
                            break

                    if backfill_col:
                        backfill_stmt = (
                            f"UPDATE {table_ref} "
                            f"SET `{resolved_col}` = COALESCE(`{resolved_col}`, CAST(`{backfill_col}` AS STRING)) "
                            f"WHERE `{resolved_col}` IS NULL"
                        )
                        try:
                            self.execute_statements([backfill_stmt])
                        except Exception as exc:
                            logger.warning(
                                "Databricks join-key bridge backfill skipped for %s.%s (%s <- %s): %s",
                                dataset.unique_name,
                                semantic_col,
                                resolved_col,
                                backfill_col,
                                str(exc)[:200],
                            )

                    remediated.append(f"{dataset.unique_name}.{semantic_col}->{resolved_col}")

        if remediated:
            logger.warning(
                "Databricks pre-flight auto-bridged %d relationship join-key column(s): %s",
                len(remediated),
                ", ".join(remediated[:12]) + (" ..." if len(remediated) > 12 else ""),
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

    def _allow_metric_deploy_sql_fallback(self) -> bool:
        """Return whether failed metric-view deploys may downgrade to SQL views.

        Only allow automatic downgrade when view type is configured as `auto`.
        If a user explicitly configures `metric_view`, keep that contract and
        avoid silently switching technologies.
        """
        configured = str(self._dbx_behavior.measure_view_type or "").strip().lower()
        if configured == "auto":
            return True
        return bool(getattr(self._dbx_behavior, "allow_sql_fallback_on_metric_view_failure", False))

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

    def _infer_dimension_primary_key(
        self,
        dataset: SMLDataset,
        sml_model: SMLModel,
    ) -> str | None:
        """Infer a stable key column for dimension-only distinct-count fallback."""
        key_columns = [col.unique_name for col in dataset.get_key_columns() if col.unique_name]
        if key_columns:
            return key_columns[0]

        dataset_name = str(dataset.unique_name or "").upper()
        relationship_key_candidates: list[str] = []
        for rel in sml_model.relationships:
            if rel.to_dataset and rel.to_dataset.upper() == dataset_name:
                relationship_key_candidates.extend([c for c in rel.to_columns if c])
        if relationship_key_candidates:
            return relationship_key_candidates[0]

        return None

    def _build_dimension_only_resolved_measures(
        self,
        dataset: SMLDataset,
        sml_model: SMLModel,
    ) -> list[ResolvedMeasure]:
        """Build synthetic measures for dimension-only datasets."""
        measures = [
            ResolvedMeasure(
                name="total_rows",
                sql_expression="COUNT(*)",
                translation_type=TRANSLATION_TYPE_AGGREGATION_BUILT,
                confidence=CONFIDENCE_HIGH,
            )
        ]

        if not self._dbx_behavior.emit_distinct_pk_metric_for_dimension_datasets:
            return measures

        pk_column = self._infer_dimension_primary_key(dataset, sml_model)
        if not pk_column:
            return measures

        pk_identifier = self._sanitize_identifier(pk_column)
        if not pk_identifier:
            return measures

        measures.append(
            ResolvedMeasure(
                name=f"distinct_{pk_identifier.lower()}_count",
                sql_expression=f"COUNT(DISTINCT `{pk_identifier}`)",
                translation_type=TRANSLATION_TYPE_AGGREGATION_BUILT,
                confidence=CONFIDENCE_HIGH,
            )
        )
        return measures

    def _validate_relationship_endpoints(self, sml_model: SMLModel, context: str) -> None:
        """Warn or fail when relationships reference missing datasets."""
        dataset_names = {
            self._sanitize_identifier(ds.unique_name).upper()
            for ds in sml_model.datasets
            if self._sanitize_identifier(ds.unique_name)
        }
        missing_refs: list[str] = []
        for rel in sml_model.relationships:
            from_name = self._sanitize_identifier(rel.from_dataset)
            to_name = self._sanitize_identifier(rel.to_dataset)
            if not from_name or not to_name:
                continue
            if from_name.upper() not in dataset_names or to_name.upper() not in dataset_names:
                missing_refs.append(
                    f"{rel.unique_name}: {rel.from_dataset} -> {rel.to_dataset}"
                )

        if not missing_refs:
            return

        message = (
            f"Relationship graph integrity issue in {context}: "
            f"{', '.join(missing_refs)}"
        )
        if self._dbx_behavior.strict_graph_coverage_validation:
            raise DatabricksPublishError(message)
        logger.warning(message)

    def _validate_relationship_source_coverage(
        self,
        sml_model: SMLModel,
        missing_source_datasets: set[str],
        context: str,
    ) -> None:
        """Warn or fail when relationship endpoints lack source-table coverage."""
        if not missing_source_datasets:
            return

        impacted_relationships: list[str] = []
        for rel in sml_model.relationships:
            from_name = self._sanitize_identifier(rel.from_dataset)
            to_name = self._sanitize_identifier(rel.to_dataset)
            if not from_name or not to_name:
                continue
            if from_name in missing_source_datasets or to_name in missing_source_datasets:
                impacted_relationships.append(rel.unique_name)

        if not impacted_relationships:
            return

        missing_sorted = ", ".join(sorted(missing_source_datasets))
        message = (
            f"Relationship source coverage issue in {context}: missing source for datasets "
            f"[{missing_sorted}] impacts relationships {sorted(set(impacted_relationships))}"
        )
        if self._dbx_behavior.strict_graph_coverage_validation:
            raise DatabricksPublishError(message)
        logger.warning(message)

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
        select_parts: list[str] = []
        used_aliases: set[str] = set()

        for binding in bindings:
            # When an alias table is used, binding.source_column could exist natively
            source_expr = f"f.`{binding.source_column}`"
            
            # If physical cols are known and the column doesn't exist, we must still project it
            # But we fallback to NULL to avoid UNRESOLVED_COLUMN errors inside the source block.
            if physical_cols and binding.source_column.lower() not in physical_cols:
                # If we have measure overrides, they might be relying on 'Inventory_Fact.source_value_total_stock'
                # but if that column is purely in Project_Measures, it won't exist.
                # However, this mapping guarantees metric view yaml syntax validity.
                source_expr = "CAST(NULL AS DOUBLE)"

            projected_alias = str(binding.projected_name or "").strip()
            if projected_alias and projected_alias.lower() not in used_aliases:
                select_parts.append(f"  {source_expr} AS `{projected_alias}`")
                used_aliases.add(projected_alias.lower())

            semantic_alias = self._sanitize_identifier(binding.semantic_name).lower()
            if semantic_alias and semantic_alias.lower() not in used_aliases:
                select_parts.append(f"  {source_expr} AS `{semantic_alias}`")
                used_aliases.add(semantic_alias.lower())


        if hasattr(self, "_config_source_sql") and self._config_source_sql:
            for source_inj in self._config_source_sql:
                select_parts.append(f"  {source_inj.strip()}")

        if not select_parts:
            return source_fq.replace("`", "")

        select_list = ",\n".join(select_parts)
        return (
            "|\n"
            "  SELECT\n"
            f"{select_list}\n"
            f"  FROM {source_fq.replace('`', '')} f"
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
        extra_allowed_prefixes: set[str] | None = None,
    ) -> str | None:
        """Rewrite dataset-local measure SQL to the metric-view source projection."""
        expr = self._normalize_semantic_sql_aliases(sql_expression)
        if not expr:
            return None

        allowed_prefixes = self._metric_view_allowed_prefixes(dataset, source_fq)
        join_prefixes = {
            str(p or "").strip().lower()
            for p in (extra_allowed_prefixes or set())
            if str(p or "").strip()
        }
        if join_prefixes:
            allowed_prefixes.update(join_prefixes)
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

        reserved_tokens = {
            "and", "or", "not", "null", "true", "false",
            "case", "when", "then", "else", "end",
            "as", "distinct", "over", "partition", "order", "by",
            "sum", "avg", "min", "max", "count", "count_distinct", "count_if",
            "coalesce", "nullif", "cast", "try_cast", "date_trunc", "datediff",
            "any_value", "concat", "concat_ws", "date_format", "format_string",
            "char", "chr", "unicode", "unichar",
            # Common scalar functions used by translated measures.
            "round", "bround", "abs", "ceil", "ceiling", "floor", "greatest", "least",
            "pow", "power", "sqrt", "exp", "ln", "log", "log10",
            "upper", "lower", "initcap", "trim", "ltrim", "rtrim", "length",
            "substr", "substring", "replace", "regexp_replace", "regexp_extract", "regexp_like",
            "to_date", "to_timestamp", "date_add", "date_sub", "add_months", "last_day",
            "year", "month", "day", "weekofyear", "quarter",
            "if", "iif", "nvl", "ifnull", "isnull", "isnan",
            # Common SQL type tokens used in CAST(... AS <type>).
            "string", "double", "float", "decimal", "numeric",
            "int", "integer", "bigint", "smallint", "tinyint",
            "boolean", "date", "timestamp",
            "lag", "lead", "rank", "dense_rank", "row_number",
            "current_date", "current_timestamp", "now",
        }

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
            
            # Pass-through for join prefixes (non-root tables)
            if prefix in join_prefixes:
                return f"`{prefix}`.`{self._sanitize_identifier(column_name)}`"

            # Try to resolve with allowed prefix first (root dataset)
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
                normalized_col = self._sanitize_identifier(column_name).lower()
                # 1. If the column exists in the live-introspected physical table, pass it through.
                if normalized_col in physical_cols:
                    return f"`{self._sanitize_identifier(column_name)}`"
                # 2. If no physical columns were introspected (DB unreachable or table not
                #    yet created), give the benefit of the doubt and pass the column through.
                #    This prevents translated DAX from silently becoming cast(null as double)
                #    simply because SHOW COLUMNS couldn't run before the table was deployed.
                if not physical_cols:
                    return f"`{self._sanitize_identifier(column_name)}`"
                # 3. No semantic binding AND not in physical schema AND schema IS known.
                #    Only at this point do we treat it as unresolvable.
                if not dataset.columns:
                    return f"`{self._sanitize_identifier(column_name)}`"
                unresolved = True
                return match.group(0)
            return f"`{projected}`"

        expr = quoted_pattern.sub(_replace_quoted, expr)
        if unresolved:
            return None

        bare_pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")

        def _replace_bare(match: re.Match[str]) -> str:
            nonlocal unresolved
            token = match.group(1)
            raw_token = str(token or "").strip()
            # Preserve leading underscore identifiers (computed columns injected
            # into the metric-view `source:` projection). `_sanitize_identifier`
            # strips leading underscores, which would incorrectly turn
            # `_current_fiscal_period` into `current_fiscal_period` and make the
            # reference unresolved.
            if raw_token.startswith("_") and re.fullmatch(r"_[A-Za-z0-9_]+", raw_token):
                return f"`{raw_token.lower()}`"

            normalized = self._sanitize_identifier(raw_token).lower()
            if not normalized:
                return token
            if normalized in reserved_tokens:
                return token
            if normalized in allowed_prefixes or normalized in join_prefixes:
                return token
            projected = _resolve_projected_name(token)
            if projected:
                return f"`{projected}`"
            if normalized in physical_cols or not physical_cols:
                return f"`{self._sanitize_identifier(token)}`"
            unresolved = True
            return token

        def _replace_bare_in_segment(segment: str) -> str:
            if not segment:
                return segment
            parts = segment.split("'")
            for idx in range(0, len(parts), 2):
                parts[idx] = bare_pattern.sub(_replace_bare, parts[idx])
            return "'".join(parts)

        backtick_parts = expr.split("`")
        for idx in range(0, len(backtick_parts), 2):
            backtick_parts[idx] = _replace_bare_in_segment(backtick_parts[idx])
        expr = "`".join(backtick_parts)
        if unresolved:
            return None
        return expr

    def _normalize_semantic_sql_aliases(self, sql_expression: str) -> str:
        """Normalize known semantic-layer aliases to Databricks-safe SQL references."""
        expr = str(sql_expression or "").strip()
        if not expr:
            return ""

        # Some translated expressions reference synthetic measure-table aliases
        # that do not exist in Databricks source SQL.
        expr = re.sub(r"(?i)\b(projectmeasures|project_measures|measuretable)\s*\.", "", expr)

        # Legacy translator token used for current fiscal period comparisons.
        expr = re.sub(r"(?i)\bFISCAL_MONTH_VALUE\b", "_current_fiscal_period", expr)
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
        expr = self._normalize_semantic_sql_aliases(sql_expression)
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
            inline_column_names: list[str] = []
            used_cols: set[str] = set()
            for rm in resolved_measures:
                for col_name in self._extract_inline_source_columns_from_sql_expression(rm.sql_expression):
                    if col_name in used_cols:
                        continue
                    used_cols.add(col_name)
                    inline_source_columns.add(col_name)  # Track these for dimension generation
                    inline_column_names.append(col_name)
            inline_cols = self._build_inline_null_projection(
                inline_column_names,
                [rm.sql_expression for rm in resolved_measures if rm.sql_expression],
            )
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
                source_query = self._build_source_query_with_anchors(source_fq, ds_name, sml_model)
                if "\n" in source_query:
                    lines.append("source: |")
                    for qline in source_query.split('\n'):
                        lines.append(f"  {qline}")
                else:
                    lines.append("source: " + yaml_quote(source_query))
        else:
            # Inline SQL query as source — no physical table needed
            # Generates a typed schema SELECT using CAST(NULL AS type)
            inline_cols: list[str] = []
            used_aliases: set[str] = set()
            for binding in bindings:
                col = dataset.get_column(binding.semantic_name)
                if not col:
                    continue
                dbx_type = self._sql_type(
                    col.data_type.value, col.source_type, col.unique_name,
                )
                source_expr = f"CAST(NULL AS {dbx_type})"

                projected_alias = str(binding.projected_name or "").strip()
                if projected_alias and projected_alias.lower() not in used_aliases:
                    inline_cols.append(
                        f"{source_expr} AS `{projected_alias}`"
                    )
                    used_aliases.add(projected_alias.lower())

                semantic_alias = self._sanitize_identifier(binding.semantic_name).lower()
                if semantic_alias and semantic_alias.lower() not in used_aliases:
                    inline_cols.append(
                        f"{source_expr} AS `{semantic_alias}`"
                    )
                    used_aliases.add(semantic_alias.lower())
            if inline_cols:
                inline_select = ", ".join(inline_cols)
                lines.append("source: |")
                lines.append(f"  SELECT {inline_select}")
            else:
                # Fallback: reference the table directly when no bindings matched
                # Extract columns from measures for inline source when needed
                if not bindings:
                    for rm in resolved_measures:
                        for col_name in self._extract_inline_source_columns_from_sql_expression(rm.sql_expression):
                            if col_name not in inline_source_columns:
                                inline_source_columns.add(col_name)
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
        # or when bindings didn't resolve all columns but measures reference them
        if inline_source_columns:
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

            # Determine if the measure name needs prefixing
            # Prefix if measure name matches a column name (from dataset.columns or inline_source_columns)
            measure_normalized = self._sanitize_identifier(rm.name).upper()
            column_names_upper = {col.unique_name.upper() for col in (dataset.columns or [])}
            # Also include columns extracted from inline sources
            column_names_upper.update({col.upper() for col in inline_source_columns})
            measure_name_matches_column = measure_normalized in column_names_upper
            
            if measure_name_matches_column:
                # Add table alias prefix to match dimension naming convention
                prefixed_measure_name = self._prefix_metric_view_measure_name(
                    dataset, rm.name, used_dimension_names
                )
            else:
                # Use measure name as-is when it doesn't match any column
                prefixed_measure_name = self._sanitize_identifier(rm.name)

            if rm.confidence == CONFIDENCE_LOW and self._dbx_behavior.enable_low_confidence_drafts:
                warning = " ".join(rm.warnings).replace('"', "'") if rm.warnings else "LOW CONFIDENCE"
                original_dax = (rm.original_dax or "").replace('"', "'")
                lines.append(f"  # TRANSLATION WARNING: {warning}")
                if original_dax:
                    dax_preview = re.sub(r"\s+", " ", original_dax).strip()[:200]
                    lines.append(f"  # Original DAX: {dax_preview}")

            # Safety net: Databricks metric views require every measure expr to be an
            # aggregate function. If the expression has no aggregate root, wrap it in
            # ANY_VALUE() so the deployment never rejects it.
            safe_expr = self._ensure_aggregate_measure_expr(dbx_expr)
            lines.append(f"  - name: {yaml_quote(prefixed_measure_name)}")
            lines.append(f"    expr: {yaml_quote(safe_expr)}")
            measures_added += 1
             
        if measures_added == 0:
            # Avoid in-place mutation of the last line: depending on earlier
            # injections, the last line might not be the "measures:" header.
            if lines and lines[-1].strip() == "measures:":
                lines.pop()
            lines.append("measures: []")

        output_yaml = "\n".join(lines)

        # ── Databricks Semantic Overrides ──
        
        # 1. Inject UI lookups (Error 3)
        if hasattr(self, "_ui_lookups") and self._ui_lookups:
            joins_to_inject = []
            dims_to_inject = []
            for lookup in self._ui_lookups:
                join_alias = self._sanitize_identifier(lookup['metric_name']).lower() + "_lookup"
                joins_to_inject.append(f"  - name: {join_alias}")
                joins_to_inject.append(f"    source: {lookup['table_name']}")
                joins_to_inject.append(f"    on: '`{lookup['column'].lower()}` = {join_alias}.`{lookup['column'].lower()}`'")
                dims_to_inject.append(f"  - name: {self._sanitize_identifier(lookup['metric_name']).lower()}_text")
                dims_to_inject.append(f"    expr: {join_alias}.`{self._sanitize_identifier(lookup['metric_name']).lower()}_text`")
            
            if "joins:" in output_yaml:
                output_yaml = output_yaml.replace("joins:", "joins:\n" + "\n".join(joins_to_inject))
            
            # Dimensions block exists, just append before measures
            measures_idx = output_yaml.rfind("measures:")
            if measures_idx != -1:
                output_yaml = output_yaml[:measures_idx] + "\n".join(dims_to_inject) + "\n\n" + output_yaml[measures_idx:]

        # We completely removed the `current_fiscal` regex join injection here
        # because Unity Catalog natively forbids aggregate subqueries inside the joins block.
        # Instead, users fully use `semabridge.yaml` -> `source_columns_sql` (Option 2)
        # to inject `(SELECT MAX(...) ...) AS _current_fiscal_period` into the root SELECT
        # and override measures via `measure_sql_overrides`.

        return output_yaml

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
        
        if not joins and not hasattr(self, "_config_pre_joins"):
            return []
            
        # Emit joins YAML
        lines: list[str] = [""]
        lines.append("joins:")
        
        # Inject custom pre_joins
        if hasattr(self, "_config_pre_joins") and self._config_pre_joins:
            for j in self._config_pre_joins:
                lines.append(f"  - name: {j.get('alias')}")
                if 'sql' in j:
                    if j['sql'].strip().upper().startswith('SELECT'):
                        lines.append(f"    source: |-\n      ({j['sql']})")
                    else:
                        lines.append(f"    source: {j['sql']}")
                if 'on' in j:
                    lines.append(f"    on: '{j['on']}'")
                elif j.get('type') == 'cross':
                    lines.append(f"    on: '1 = 1'")
                lines.append("")
        
        if joins:
            # Top-level join items must align at two spaces under `joins:`.
            # Using indent=1 caused relationship-derived joins to be indented
            # differently than config `pre_joins`, producing invalid YAML.
            self._emit_join_yaml(sml_model, joins, lines, indent=0)
        
        return lines

    def _metric_view_join_prefixes(
        self,
        sml_model: SMLModel,
        dataset: SMLDataset,
    ) -> set[str]:
        """Return join aliases that should be allowed in metric-view expressions."""
        if not (self._dbx_behavior.enable_metric_view_joins and self._dbx_behavior.enable_cross_table_joins):
            return set()
        prefixes: set[str] = set()

        try:
            builder = JoinTreeBuilder(sml_model)
            joins = builder.build_join_tree(dataset.unique_name)
        except ValueError:
            joins = []

        def _collect(nodes: list) -> None:
            for node in nodes or []:
                name = str(getattr(node, "name", "") or "").strip().lower()
                if name:
                    prefixes.add(name)
                nested = getattr(node, "joins", None)
                if nested:
                    _collect(nested)

        _collect(joins)

        if hasattr(self, "_config_pre_joins") and self._config_pre_joins:
            for join_cfg in self._config_pre_joins:
                alias = str(
                    (join_cfg or {}).get("alias")
                    or (join_cfg or {}).get("name")
                    or ""
                ).strip().lower()
                if alias:
                    prefixes.add(alias)

        return prefixes
    
    def _metric_view_join_alias(self, dataset_name: str) -> str:
        """Build the deterministic join alias used by JoinTreeBuilder."""
        alias = "".join(c.lower() for c in str(dataset_name or "") if c.isalnum() or c == "_")
        return alias or ""

    def _resolve_metric_view_join_source(
        self,
        sml_model: SMLModel,
        join_name: str,
        join_source: str,
    ) -> str:
        """Resolve a join source to a Databricks-valid relation identifier.

        JoinTreeBuilder may emit a human-readable dataset/source name (for example
        "Plant BU Mapping") that is not a valid SQL identifier in Databricks metric
        view YAML. Prefer connector mapping resolution for the matched dataset.
        """
        join_alias = self._metric_view_join_alias(join_name)
        if not join_alias:
            return str(join_source or "")

        for dataset in sml_model.datasets:
            if self._metric_view_join_alias(dataset.unique_name) != join_alias:
                continue
            expected_source = self._resolve_source_table(dataset)
            resolved_source = self._resolve_existing_source_for_dataset(dataset, expected_source) or expected_source
            return resolved_source.replace("`", "")

        return str(join_source or "")

    def _normalize_metric_view_join_column(self, column_name: str) -> str:
        """Return a Databricks-safe join key identifier for metric-view YAML."""
        return self._sanitize_identifier(column_name).lower()

    def _normalize_metric_view_join_on_expression(self, on_expression: str) -> str:
        """Normalize quoted ON expression identifiers for Databricks parser safety."""
        expr = str(on_expression or "").strip()
        if not expr:
            return expr

        def _replace_quoted(match: re.Match[str]) -> str:
            raw = match.group(1) or match.group(2) or ""
            normalized = self._normalize_metric_view_join_column(raw)
            return f"`{normalized}`" if normalized else match.group(0)

        return re.sub(r"`([^`]+)`|\"([^\"]+)\"", _replace_quoted, expr)

    def _emit_join_yaml(
        self,
        sml_model: SMLModel,
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
            resolved_source = self._resolve_metric_view_join_source(
                sml_model,
                getattr(join, "name", ""),
                getattr(join, "source", ""),
            )
            lines.append(f"{indent_str}  - name: {self._yaml_quote(join.name)}")
            lines.append(f"{indent_str}    source: {self._yaml_quote(resolved_source)}")
            if getattr(join, "using", None):
                lines.append(f"{indent_str}    using:")
                for column_name in join.using:
                    normalized_column = self._normalize_metric_view_join_column(column_name)
                    if not normalized_column:
                        continue
                    lines.append(f"{indent_str}      - {self._yaml_quote(normalized_column)}")
            else:
                normalized_on = self._normalize_metric_view_join_on_expression(join.on)
                lines.append(f"{indent_str}    'on': {self._yaml_quote(normalized_on)}")
            
            # Emit nested joins if present
            if join.joins:
                lines.append(f"{indent_str}    joins:")
                self._emit_join_yaml(sml_model, join.joins, lines, indent=indent + 2)

    def _yaml_quote(self, value: str) -> str:
        """Encode a value as a YAML-safe scalar via JSON string quoting."""
        return json.dumps(str(value or ""))

    def _normalize_metric_view_sql_expression(self, sql_expression: str) -> str:
        """Normalize SQL expressions for metric-view YAML readability.

        Strips Cortex Analyst-style ``measures."column"`` / ``MEASURES."COLUMN"``
        table prefixes that are injected by the cortex_analyst.yaml generator.
        Those prefixes are valid Snowflake Cortex Analyst syntax but cause a
        ``PARSE_SYNTAX_ERROR`` in Databricks ``WITH METRICS LANGUAGE YAML``
        because no table alias named ``measures`` exists in the source block.

        The rewrite converts:
            ``sum(measures."transaction_usd_amount")``   → ``sum(transaction_usd_amount)``
            ``SUM(MEASURES."TRANSACTION_USD_AMOUNT")``   → ``sum(transaction_usd_amount)``
            ``sum(measures.transaction_usd_amount)``     → ``sum(transaction_usd_amount)``

        After stripping the prefix the expression only references bare projected
        column aliases that are already emitted by the ``source:`` SELECT block.
        """
        expr = str(sql_expression or "").strip().replace("`", "")
        if not expr:
            return expr

        # Strip Cortex Analyst ``measures."col"`` / ``measures.col`` prefixes.
        # Pattern matches:
        #   MEASURES."COLUMN_NAME"   (double-quoted identifier)
        #   measures.column_name     (unquoted identifier)
        # Both quoted and unquoted forms are replaced with the bare column name.
        expr = re.sub(
            r'(?i)\bmeasures\."([^"]+)"',
            lambda m: m.group(1).lower(),
            expr,
        )
        expr = re.sub(
            r"(?i)\bmeasures\.([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: m.group(1).lower(),
            expr,
        )

        # Also strip any remaining join-table prefixes that used the dummy
        # Power BI '_Measures' or 'Project_Measures' table names.
        expr = re.sub(
            r'(?i)\b(?:project_measures|_measures)\."([^"]+)"',
            lambda m: m.group(1).lower(),
            expr,
        )
        expr = re.sub(
            r"(?i)\b(?:project_measures|_measures)\.([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: m.group(1).lower(),
            expr,
        )

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

    # Aggregate function names accepted by Databricks Unity Catalog metric views
    _AGGREGATE_ROOTS = frozenset({
        "sum", "avg", "average", "min", "max", "count", "any_value",
        "approx_count_distinct", "collect_list", "collect_set",
        "first", "last", "stddev", "stddev_pop", "var_pop", "variance",
        "percentile", "percentile_approx", "median", "bitmap_or_agg",
    })

    def _ensure_aggregate_measure_expr(self, expr: str) -> str:
        """Wrap non-aggregate measure SQL in ANY_VALUE() for Databricks compliance.

        Databricks Unity Catalog metric views reject measure expressions that do not
        contain a top-level aggregate function (SUM, MAX, ANY_VALUE, etc.).  This
        helper detects bare scalar / CASE WHEN / subquery expressions and wraps them
        so deployment never fails due to missing aggregate roots.
        """
        clean = str(expr or "").strip()
        if not clean or clean.lower() == "cast(null as double)":
            return clean

        # Extract the leading function name (handles backticks and spaces)
        func_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", clean, re.IGNORECASE)
        if func_match:
            func_name = func_match.group(1).lower()
            if func_name in self._AGGREGATE_ROOTS:
                return clean  # already has an aggregate root

        # Subquery starting with SELECT — treat as scalar subquery, wrap it
        if re.match(r"^\(?\s*select\b", clean, re.IGNORECASE):
            # Already scalar subqueries like (SELECT MAX(...) FROM ...) are fine as-is;
            # plain (SELECT col FROM ...) need wrapping.
            inner_has_agg = any(
                re.search(rf"\b{fn}\s*\(", clean, re.IGNORECASE)
                for fn in self._AGGREGATE_ROOTS
            )
            if inner_has_agg:
                return clean

        logger.debug(
            "Wrapping non-aggregate measure expr in ANY_VALUE(): %s", clean[:80]
        )
        return f"any_value({clean})"

    def _is_metric_view_safe_scalar_expression(self, sql_expression: str) -> bool:
        """Return True when an expression is a safe scalar function for metric-view YAML."""
        expr = str(sql_expression or "").strip()
        if not expr:
            return False

        normalized = re.sub(r"\s+", "", expr).lower()
        return normalized in {
            "current_date()",
            "curdate()",
            "current_timestamp()",
            "now()",
            "localtimestamp()",
        }

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

        # Track which metrics have been processed
        processed_metric_ids: set[int] = set()

        for dataset in sml_model.datasets:
            ds_name = self._sanitize_identifier(dataset.unique_name)
            if not ds_name:
                continue
            metrics = metrics_by_dataset.get(ds_name, [])
            join_prefixes = self._metric_view_join_prefixes(sml_model, dataset)

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
                processed_metric_ids.add(id(metric))
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
                    sml_model=sml_model,
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
                    extra_allowed_prefixes=join_prefixes,
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
                    resolved = self._build_dimension_only_resolved_measures(dataset, sml_model)
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

        # Validate any remaining metrics that reference non-existent datasets
        for metric in sml_model.metrics:
            if id(metric) not in processed_metric_ids:
                m_name = metric_name_index.get(
                    id(metric),
                    self._normalize_metric_identifier(metric.unique_name),
                )
                skipped_count += 1
                skipped_details.append({
                    "name": m_name,
                    "reason": DEPLOY_REASON_VALIDATION_FAILED,
                })

        return stmts, created, skipped_count, skipped_details

    def _assess_confidence(self, translation_type: str) -> str:
        """Map translation type to confidence score."""
        return {
            TRANSLATION_TYPE_SQL_NATIVE: CONFIDENCE_HIGH,
            TRANSLATION_TYPE_AGGREGATION_BUILT: CONFIDENCE_HIGH,
            TRANSLATION_TYPE_PRECOMPUTED: CONFIDENCE_HIGH,
            TRANSLATION_TYPE_DAX_TRANSLATED: CONFIDENCE_MEDIUM,
            TRANSLATION_TYPE_DAX_LLM: CONFIDENCE_MEDIUM,
            TRANSLATION_TYPE_PRECOMPUTE_REQUIRED: CONFIDENCE_LOW,
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
            base_alias = preferred if preferred.endswith("_join") else f"{preferred}_join"
            alias_by_dataset[ds_name] = self._make_unique_projected_name(base_alias, used_aliases, suffix="join").lower()

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
        expr = self._normalize_semantic_sql_aliases(sql_expression)
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

        return f"first((SELECT {agg_sql} FROM {target_source}))"

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

    def _is_object_type_conflict_error(self, error_text: str) -> bool:
        """Return True for Databricks object type conflicts during CREATE VIEW."""
        lowered = str(error_text or "").lower()
        return (
            "expect_view_not_table" in lowered
            or "does not support create or replace view" in lowered
            or "sqlstate: 42809" in lowered
        )

    def _preflight_resolve_object_conflict(
        self,
        view_fqn: str,
        *,
        is_metric: bool = False,
    ) -> None:
        """Detect and drop any conflicting object at ``view_fqn`` before deployment.

        Option 3 — Pre-flight object-type conflict cleanup:
            Runs ``DESCRIBE EXTENDED`` on the target name.  If a TABLE (MANAGED
            or EXTERNAL) is found it is dropped unconditionally so ``CREATE OR
            REPLACE VIEW`` can succeed.  If a plain VIEW is found and we are
            deploying a native metric-view (``is_metric=True``), Databricks
            rejects the REPLACE because the object types differ — so the plain
            VIEW is also dropped first.

            Unlike the post-failure retry path (``_is_object_type_conflict_error``
            + DROP inside the except block), this runs **before** the first
            deploy attempt, eliminating the wasted failed round-trip and making
            the conflict visible in the log at the right moment.

        Args:
            view_fqn:   Fully-qualified backtick name, e.g.
                        e.g. '`catalog`.`schema`.`view_name`'.

            is_metric:  True when deploying ``WITH METRICS LANGUAGE YAML`` —
                        a plain VIEW at the same name is a type mismatch.
        """
        # Parse the FQN (strip backticks first)
        bare = view_fqn.replace("`", "")
        parts = bare.split(".")
        if len(parts) != 3:
            return  # Unexpected format — skip silently

        catalog, schema_name, table_name = parts

        # ── Step 1: Check whether anything exists ───────────────────────────
        # Use SHOW TABLES with a LIKE filter — the schema-level cache is not
        # guaranteed to be populated for the *target* schema (which is the
        # publish catalog/schema, not necessarily a source schema).
        try:
            show_stmt = f"SHOW TABLES IN `{catalog}`.`{schema_name}` LIKE '{table_name}'"
            rows = self.execute_statements([show_stmt])
        except Exception as exc:
            logger.debug(
                "Pre-flight conflict check: SHOW TABLES failed for %s — skipping. %s",
                view_fqn,
                str(exc)[:200],
            )
            return

        found = False
        if rows:
            payload = rows[0] if isinstance(rows[0], dict) else {}
            result_block = payload.get("result") if isinstance(payload, dict) else {}
            data_array = result_block.get("data_array") if isinstance(result_block, dict) else None
            if data_array:
                found = bool(data_array)

        if not found:
            return  # Nothing there — safe to deploy

        # ── Step 2: Inspect the existing object's type ───────────────────────
        existing_type = "UNKNOWN"
        try:
            desc_stmt = f"DESCRIBE EXTENDED {view_fqn}"
            desc_rows = self.execute_statements([desc_stmt])
            if desc_rows:
                desc_payload = desc_rows[0] if isinstance(desc_rows[0], dict) else {}
                desc_block = desc_payload.get("result") if isinstance(desc_payload, dict) else {}
                desc_data = desc_block.get("data_array") if isinstance(desc_block, dict) else []
                # data_array rows are [col_name, data_type, comment]
                for row in (desc_data or []):
                    if isinstance(row, list) and len(row) >= 2:
                        if str(row[0]).strip().lower() == "type":
                            existing_type = str(row[1]).strip().upper()
                            break
        except Exception as exc:
            logger.debug(
                "Pre-flight conflict check: DESCRIBE EXTENDED failed for %s — assuming safe. %s",
                view_fqn,
                str(exc)[:200],
            )
            return

        # ── Step 3: Drop if necessary ────────────────────────────────────────
        needs_drop = False
        drop_reason = ""

        if existing_type in ("MANAGED", "EXTERNAL"):
            needs_drop = True
            drop_reason = f"existing object is a {existing_type} TABLE; need VIEW"
        elif existing_type == "VIEW" and is_metric:
            # Plain VIEW → Metric View upgrade: Databricks rejects CREATE OR REPLACE
            # because the underlying object types differ at the catalog level.
            needs_drop = True
            drop_reason = "existing object is a plain VIEW; need METRIC_VIEW (WITH METRICS)"

        if not needs_drop:
            # VIEW → VIEW or METRIC_VIEW → METRIC_VIEW: handled by CREATE OR REPLACE natively.
            return

        logger.warning(
            "⚠️  Pre-flight conflict at %s: %s — dropping before deploy.",
            view_fqn,
            drop_reason,
        )

        for drop_stmt in (
            f"DROP VIEW IF EXISTS {view_fqn}",
            f"DROP TABLE IF EXISTS {view_fqn}",
        ):
            try:
                self.execute_statements([drop_stmt])
            except Exception as drop_exc:
                logger.debug(
                    "Pre-flight DROP attempt failed for %s: %s",
                    view_fqn,
                    str(drop_exc)[:200],
                )

        logger.info("✅ Pre-flight conflict resolved for %s — proceeding with deploy.", view_fqn)

    def _classify_metric_view_deploy_error(self, error_text: str) -> tuple[str, str]:
        """Classify metric-view deploy failures into actionable categories."""
        lowered = str(error_text or "").lower()

        semantic_tokens = [
            "scalar_subquery_is_in_group_by_or_aggregate_function",
            "group by",
            "aggregate function",
            "analysisexception",
            "parse_syntax_error",
            "datatype_mismatch",
        ]
        if any(token in lowered for token in semantic_tokens):
            if "scalar_subquery_is_in_group_by_or_aggregate_function" in lowered:
                return ERROR_CLASS_SQL_SEMANTIC, "SCALAR_SUBQUERY_GROUPING"
            return ERROR_CLASS_SQL_SEMANTIC, "SQL_SEMANTIC"

        missing_entity_tokens = [
            "table_or_view_not_found",
            "unresolved_relation",
            "not found",
            "no such table",
            "does not exist",
        ]
        if any(token in lowered for token in missing_entity_tokens):
            return ERROR_CLASS_MISSING_ENTITY, "MISSING_ENTITY"

        source_mismatch_tokens = [
            "unresolved_column",
            "cannot be resolved",
            "schema mismatch",
            "cannot resolve",
        ]
        if any(token in lowered for token in source_mismatch_tokens):
            return ERROR_CLASS_SOURCE_MISMATCH, "SOURCE_MISMATCH"

        return ERROR_CLASS_GENERIC, "GENERIC_DEPLOY_ERROR"

    def _log_sql_fallback_state(
        self,
        *,
        model_name: str,
        state: str,
        reason: str = "",
    ) -> None:
        """Emit a structured fallback state log for auditability."""
        self._last_sql_fallback_state = str(state or "").strip() or SQL_FALLBACK_NOT_TRIGGERED
        self._last_sql_fallback_reason = str(reason or "").strip()

        if reason:
            logger.info(
                "SQL_FALLBACK_STATE model=%s state=%s reason=%s",
                model_name,
                state,
                reason,
            )
            return
        logger.info(
            "SQL_FALLBACK_STATE model=%s state=%s",
            model_name,
            state,
        )

    def _extract_unresolved_column_name(self, error_text: str) -> str:
        """Extract unresolved column identifier from Databricks error text."""
        text = str(error_text or "")
        match = re.search(
            r"name\s+`([^`]+)`\s+cannot\s+be\s+resolved",
            text,
            re.IGNORECASE,
        )
        if not match:
            return ""
        return self._sanitize_identifier(match.group(1)).lower()

    def _remove_failing_metric_measure(
        self,
        view_sql: str,
        error_text: str,
    ) -> str | None:
        """Remove metric-view entries referencing an unresolved identifier.

        This primarily removes failing measures, and also drops dimensions that
        directly reference the unresolved identifier so degraded retries can
        proceed when the parser failure is dimension-driven.
        """
        unresolved_col = self._extract_unresolved_column_name(error_text)
        if not unresolved_col:
            return None

        metric_sql_match = re.search(
            r"(?is)^(CREATE\s+OR\s+REPLACE\s+VIEW\s+`[^`]+`\.`[^`]+`\.`[^`]+`\s+WITH\s+METRICS\s+LANGUAGE\s+YAML\s+AS\s+\$\$)\s*(.*?)\s*\$\$\s*$",
            str(view_sql or ""),
        )
        if not metric_sql_match:
            return None

        prefix = metric_sql_match.group(1)
        yaml_body = metric_sql_match.group(2)

        try:
            payload = yaml.safe_load(yaml_body) or {}
        except yaml.YAMLError:
            return None

        measures = payload.get("measures")
        unresolved_pattern = re.compile(rf"\b{re.escape(unresolved_col)}\b", re.IGNORECASE)

        removed_measure_names: list[str] = []
        if isinstance(measures, list) and measures:
            retained_measures: list[dict[str, Any]] = []
            for measure in measures:
                if not isinstance(measure, dict):
                    retained_measures.append(measure)
                    continue
                expr = str(measure.get("expr") or "")
                expr_norm = expr.replace("`", "").replace('"', "")
                if unresolved_pattern.search(expr_norm):
                    removed_measure_names.append(str(measure.get("name") or "unnamed_measure"))
                    continue
                retained_measures.append(measure)
            payload["measures"] = retained_measures

        dimensions = payload.get("dimensions")
        removed_dimension_names: list[str] = []
        if isinstance(dimensions, list) and dimensions:
            retained_dimensions: list[dict[str, Any]] = []
            for dim in dimensions:
                if not isinstance(dim, dict):
                    retained_dimensions.append(dim)
                    continue
                expr = str(dim.get("expr") or "")
                expr_norm = expr.replace("`", "").replace('"', "")
                if unresolved_pattern.search(expr_norm):
                    removed_dimension_names.append(str(dim.get("name") or "unnamed_dimension"))
                    continue
                retained_dimensions.append(dim)
            payload["dimensions"] = retained_dimensions

        if not removed_measure_names and not removed_dimension_names:
            return None

        try:
            repaired_yaml = yaml.safe_dump(payload, sort_keys=False).strip()
        except yaml.YAMLError:
            return None

        if removed_measure_names:
            logger.warning(
                "Metric-view graceful degradation: removed %d measure(s) referencing unresolved column '%s': %s",
                len(removed_measure_names),
                unresolved_col,
                ", ".join(removed_measure_names),
            )
        if removed_dimension_names:
            logger.warning(
                "Metric-view graceful degradation: removed %d dimension(s) referencing unresolved column '%s': %s",
                len(removed_dimension_names),
                unresolved_col,
                ", ".join(removed_dimension_names),
            )

        return f"{prefix}\n{repaired_yaml}\n$$"

    def _preflight_strip_bad_dimensions(self, view_sql: str) -> str:
        """Strip dimension entries that reference non-existent physical columns.

        Fix 1 — Pre-flight schema validation:
            Parses the ``WITH METRICS LANGUAGE YAML`` body, resolves column lists
            for every declared join source via ``_get_source_table_columns``
            (already cached from pre-flight), and removes ``dimensions:`` entries
            whose ``join_alias.column`` does not exist in the physical table.

        Returns the (potentially patched) SQL.  Falls back to the original SQL
        if YAML parsing fails or the schema cannot be introspected.
        """
        metric_sql_match = re.search(
            r"(?is)^(CREATE\s+OR\s+REPLACE\s+VIEW\s+`[^`]+`\.`[^`]+`\.`[^`]+`"
            r"\s+WITH\s+METRICS\s+LANGUAGE\s+YAML\s+AS\s+\$\$)\s*(.*?)\s*\$\$\s*$",
            str(view_sql or ""),
        )
        if not metric_sql_match:
            return view_sql

        prefix = metric_sql_match.group(1)
        yaml_body = metric_sql_match.group(2)

        try:
            payload = yaml.safe_load(yaml_body) or {}
        except yaml.YAMLError:
            return view_sql

        joins = payload.get("joins")
        dimensions = payload.get("dimensions")
        if not isinstance(joins, list) or not joins or not isinstance(dimensions, list):
            return view_sql

        # Build a map: join_alias → set of physical column names (lowercased)
        join_col_map: dict[str, set[str]] = {}
        for join_entry in joins:
            if not isinstance(join_entry, dict):
                continue
            alias = str(join_entry.get("name") or "").strip().lower()
            source = str(join_entry.get("source") or "").strip()
            if not alias or not source:
                continue
            physical_cols = self._get_source_table_columns(source)
            if physical_cols:
                join_col_map[alias] = physical_cols

        if not join_col_map:
            return view_sql  # No introspectable joins — skip

        # Strip dimensions referencing non-existent physical columns
        retained: list[dict] = []
        removed_names: list[str] = []
        for dim in dimensions:
            if not isinstance(dim, dict):
                retained.append(dim)
                continue
            expr = str(dim.get("expr") or "").strip().strip('"').strip("'")
            # Match   join_alias.col_name   (with optional backtick quoting)
            join_col_match = re.match(
                r"^([A-Za-z_][A-Za-z0-9_]*)\.`?([A-Za-z_][A-Za-z0-9_]*)`?$",
                expr,
            )
            if not join_col_match:
                retained.append(dim)
                continue

            alias = join_col_match.group(1).lower()
            col = join_col_match.group(2).lower()

            if alias not in join_col_map:
                # No introspection data for this alias → keep the dimension
                retained.append(dim)
                continue

            if col not in join_col_map[alias]:
                removed_names.append(str(dim.get("name") or expr))
            else:
                retained.append(dim)

        if not removed_names:
            return view_sql  # Nothing to strip

        logger.warning(
            "Pre-flight schema validation stripped %d dimension(s) with missing physical columns: %s",
            len(removed_names),
            ", ".join(removed_names[:20]) + (" ..." if len(removed_names) > 20 else ""),
        )

        payload["dimensions"] = retained
        try:
            repaired_yaml = yaml.safe_dump(payload, sort_keys=False).strip()
        except yaml.YAMLError:
            return view_sql

        return f"{prefix}\n{repaired_yaml}\n$$"

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

    def _build_llm_schema_context(self, sml_model: SMLModel) -> dict[str, list[str]]:
        """Build a lightweight schema context for LLM-based DAX translation."""
        context: dict[str, list[str]] = {}
        for dataset in sml_model.datasets:
            ds_name = str(dataset.unique_name or "").strip()
            if not ds_name:
                continue
            alias = self._sanitize_identifier(ds_name)
            if not alias:
                continue
            columns: list[str] = []
            for col in dataset.columns:
                col_name = self._sanitize_identifier(col.unique_name)
                if col_name:
                    columns.append(col_name.upper())
            if columns:
                context[alias] = sorted(set(columns))
        return context

    def _build_dax_translation_schema_lookup(
        self, sml_model: SMLModel
    ) -> tuple[dict[str, set], dict[str, str]]:
        """dataset_col_lookup/dataset_aliases for Tier 5's semantic validator,
        keyed by dataset unique_name (not alias) — the shape TranslationRequest
        expects. Reuses this class's own _sanitize_identifier (Databricks'
        identifier rules — different from Snowflake's IdentifierSanitizer
        used by Pipeline A/B), not the Snowflake-oriented sanitizer, and not
        hardcoded to any table/column/model name.

        _build_llm_schema_context() above builds a similarly-sanitized dict
        for display purposes, but keys it by *alias* — wrong shape for
        dataset_col_lookup, which callers resolve via alias_to_dataset then
        look up by dataset name. Built fresh here instead of reusing it.
        """
        dataset_col_lookup: dict[str, set] = {}
        dataset_aliases: dict[str, str] = {}
        for dataset in sml_model.datasets:
            ds_name = str(dataset.unique_name or "").strip()
            if not ds_name:
                continue
            alias = self._sanitize_identifier(ds_name)
            if not alias:
                continue
            dataset_aliases[ds_name] = alias
            columns = {
                self._sanitize_identifier(col.unique_name).upper()
                for col in dataset.columns
                if self._sanitize_identifier(col.unique_name)
            }
            if columns:
                dataset_col_lookup[ds_name] = columns
        return dataset_col_lookup, dataset_aliases

    def _try_llm_metric_fallback_expression(
        self,
        metric: SMLMetric,
        dataset: Optional[SMLDataset],
        sml_model: SMLModel,
    ) -> Optional[str]:
        """Thin shim onto DaxTranslationService's Tier 5 (Step 4 of the
        approved consolidation migration). Same signature, same
        Optional[str] contract — the one existing caller (_resolve_measure_sql)
        is unaffected.

        Unlike Pipeline A/B, there is no rule-based-translation-first step
        to preserve here — the real implementation went straight to OpenAI
        then Gemini, with no rule engine and no prefetch cache. Confirmed by
        reading the full original body before this cutover.

        Calls tier5.service.Tier5Service directly, NOT
        DaxTranslationService.translate_metric() — that would also run
        Tier 1-4 (converter/dax_translator.py's tiers_1_4.py), which are
        Snowflake-dialect-specific (Snowflake quoting, ::FLOAT casts, DIV0).
        Calling the full service here would silently produce Snowflake SQL
        for a Databricks deploy — a real dialect bug, not just redundant
        work (unlike Pipeline A's identical-dialect case in Step 3). This
        connector's own deterministic tiers (self._measure_translator,
        tried earlier in _resolve_measure_sql's priority chain) are a
        separate, Databricks-native engine, untouched by this migration.

        Found during this cutover's required dialect-correctness check (not
        preserved silently — fixed generally in tier5/validation.py and
        tier5/prompt.py, reported separately): the verbatim-salvaged
        validator only recognized Snowflake's double-quote identifier
        syntax, so backtick-quoted Databricks SQL referencing a nonexistent
        column was silently accepted. A dialect-aware quote-translation
        layer and dialect-gated Snowflake-only-syntax helpers (::FLOAT,
        IFF) close that gap generally, for every Databricks caller, not
        just this one.
        """
        if not dataset:
            return None
        dax_expr = (metric.expression or "").strip()
        if not dax_expr:
            return None

        table_alias = self._sanitize_identifier(dataset.unique_name) or "source"
        dataset_col_lookup, dataset_aliases = self._build_dax_translation_schema_lookup(sml_model)

        try:
            from semabridge.dax_translation.types import TranslationRequest

            request = TranslationRequest(
                dax=dax_expr,
                dataset_name=str(dataset.unique_name or ""),
                table_alias=table_alias,
                dataset_col_lookup=dataset_col_lookup,
                dataset_aliases=dataset_aliases,
                metric_name=str(metric.unique_name or ""),
                dialect="databricks",
            )
            result = self._get_tier5_service().translate(request)
            if result is not None and result.is_success and result.sql:
                logger.info(
                    "LLM translated DAX for measure '%s' (provider=%s, confidence=%.2f): %s → %s",
                    metric.unique_name,
                    result.provider,
                    result.translation_provider_confidence,
                    dax_expr[:60],
                    result.sql,
                )
                return result.sql
            return None
        except Exception as exc:
            logger.warning(
                "Tier 5 fallback failed for Databricks measure '%s': %s",
                metric.unique_name,
                exc,
            )
            return None

    def _try_openai_dax_translation(
        self,
        dax_expression: str,
        metric: SMLMetric,
        table_alias: str,
        schema_context: dict[str, list[str]],
    ) -> Optional[str]:
        """Translate DAX with OpenAI when OPENAI_API_KEY is configured."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None

        try:
            from openai import OpenAI
        except Exception as exc:
            logger.warning("OpenAI DAX translation requested but openai package is unavailable: %s", exc)
            return None

        model_name = os.getenv("OPENAI_DAX_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))

        schema_lines = []
        for table, cols in sorted(schema_context.items()):
            schema_lines.append(f"- {table}: {', '.join(cols[:80])}")
        schema_text = "\n".join(schema_lines[:40]) or "- <schema unavailable>"

        prompt = (
            "Dialect: Databricks SQL expression\n"
            f"Metric name: {metric.unique_name}\n"
            f"Default table alias: {table_alias}\n"
            "Rules:\n"
            "- Return only a single SQL expression, no explanation.\n"
            "- Use table aliases and columns from the schema context when known.\n"
            "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, DDL, or DML.\n"
            "- Do not nest aggregate functions like SUM(MAX(...)).\n"
            "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN column ELSE 0 END).\n"
            "- Quote identifiers only when needed using backticks like `alias`.`column`.\n"
            "- If a pattern is impossible, return CAST(NULL AS DOUBLE).\n"
            "Schema context:\n"
            f"{schema_text}\n"
            "DAX:\n"
            f"{dax_expression}"
        )

        try:
            client = OpenAI(api_key=api_key, organization=os.getenv("OPENAI_ORGANIZATION") or None)
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You translate Power BI DAX measures to Databricks SQL aggregation expressions. "
                            "Return only one SQL expression. Do not use markdown."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=float(os.getenv("OPENAI_DAX_TEMPERATURE", "0.1")),
                max_tokens=int(os.getenv("OPENAI_DAX_MAX_TOKENS", "500")),
                timeout=float(os.getenv("OPENAI_DAX_TIMEOUT", "30")),
            )
            sql = (response.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning(
                "OpenAI DAX translation failed for Databricks metric '%s': %s",
                metric.unique_name,
                exc,
            )
            return None

        # Clean markdown if OpenAI wrapped it
        if sql.startswith("```"):
            sql = re.sub(r"^```(?:sql|python|.*?)\n", "", sql, flags=re.IGNORECASE)
            sql = re.sub(r"\n```$", "", sql, flags=re.IGNORECASE)
            sql = sql.strip()

        # Simple validation
        if not sql:
            return None

        sql_upper = sql.upper()
        forbidden = (
            " SELECT ",
            "(SELECT",
            " FROM ",
            " JOIN ",
            " WITH ",
            " DROP ",
            " DELETE ",
            " TRUNCATE ",
            " INSERT ",
            " UPDATE ",
            " ALTER ",
            ";",
        )
        padded = f" {sql_upper} "
        if any(token in padded for token in forbidden):
            logger.warning(
                "OpenAI DAX translation rejected (forbidden SQL tokens) for Databricks metric '%s': %s",
                metric.unique_name,
                sql[:120],
            )
            return None

        logger.info(
            "OpenAI translated DAX for Databricks metric '%s': %s",
            metric.unique_name,
            sql[:160],
        )
        return sql

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

    def _hybrid_complex_tiers(self) -> set[str]:
        """Return configured DAX tiers that must use precomputed execution."""
        configured = getattr(self._dbx_behavior, "precompute_tiers", []) or []
        tiers = {
            str(item).strip().upper()
            for item in configured
            if str(item).strip()
        }
        if tiers:
            return tiers
        return {
            TIER_RELATIONSHIP_AWARE_FILTERED_AGGREGATION,
            TIER_DEFERRED,
        }

    def _build_precomputed_sql_expression(
        self,
        metric: SMLMetric,
        dataset: Optional[SMLDataset],
        sml_model: Optional[SMLModel],
    ) -> Optional[str]:
        """Resolve SQL expression from precomputed data-layer fields."""
        precomputed_expr = str(getattr(metric, "precomputed_sql_expression", "") or "").strip()
        if precomputed_expr:
            return precomputed_expr

        dataset_name = str(getattr(metric, "precomputed_dataset", "") or "").strip()
        precomputed_dataset = dataset
        if dataset_name and sml_model:
            precomputed_dataset = sml_model.get_dataset(dataset_name) or precomputed_dataset

        precomputed_column = str(getattr(metric, "precomputed_column", "") or "").strip()
        if not precomputed_column:
            source_column = str(metric.source_column or "").strip()
            prefix = str(getattr(self._dbx_behavior, "precomputed_column_prefix", "precomputed_") or "precomputed_")
            if source_column and source_column.lower().startswith(prefix.lower()):
                precomputed_column = source_column

        if not precomputed_column:
            return None

        safe_col = self._sanitize_identifier(precomputed_column)
        if not safe_col:
            return None

        if precomputed_dataset and precomputed_dataset.columns:
            if not precomputed_dataset.get_column(precomputed_column):
                column_names = {
                    self._sanitize_identifier(col.unique_name).upper()
                    for col in precomputed_dataset.columns
                }
                if safe_col.upper() not in column_names:
                    logger.warning(
                        "Precomputed column '%s' for metric '%s' not found in dataset '%s'.",
                        precomputed_column,
                        metric.unique_name,
                        precomputed_dataset.unique_name,
                    )
                    return None

        agg_map: dict[AggregationType, str] = {
            AggregationType.SUM: f"SUM(`{safe_col}`)",
            AggregationType.COUNT: f"COUNT(`{safe_col}`)",
            AggregationType.COUNT_DISTINCT: f"COUNT(DISTINCT `{safe_col}`)",
            AggregationType.AVG: f"AVG(`{safe_col}`)",
            AggregationType.MIN: f"MIN(`{safe_col}`)",
            AggregationType.MAX: f"MAX(`{safe_col}`)",
            AggregationType.NONE: f"MAX(`{safe_col}`)",
        }
        return agg_map.get(metric.aggregation, f"MAX(`{safe_col}`)")

    def _resolve_measure_sql(
        self,
        metric: SMLMetric,
        dataset: Optional[SMLDataset],
        measure_sql_map: Optional[dict[str, str]] = None,
        allow_simple_sum_translation: bool = True,
        sml_model: Optional[SMLModel] = None,
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
        hybrid_enabled = bool(getattr(self._dbx_behavior, "hybrid_metric_execution_enabled", False))
        metric_tier = self._measure_translator.classify_dax_measure_tier(metric.expression or "")
        complex_tiers = self._hybrid_complex_tiers()

        if hybrid_enabled:
            precomputed_sql = self._build_precomputed_sql_expression(metric, dataset, sml_model)
            if precomputed_sql:
                return precomputed_sql, TRANSLATION_TYPE_PRECOMPUTED
            if (
                bool(getattr(self._dbx_behavior, "strict_precompute_for_complex", True))
                and metric_tier in complex_tiers
            ):
                logger.warning(
                    "Hybrid execution requires precomputed logic for complex metric '%s' (tier=%s).",
                    metric.unique_name,
                    metric_tier,
                )
                return None, TRANSLATION_TYPE_PRECOMPUTE_REQUIRED

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

                if self._dbx_behavior.enable_llm_dax_translation and sml_model:
                    llm_expr = self._try_llm_metric_fallback_expression(
                        metric,
                        dataset,
                        sml_model,
                    )
                    if llm_expr:
                        return llm_expr, TRANSLATION_TYPE_DAX_LLM

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
                    logger.info("🔍 Root dataset selected via model_fact_root config: %s", configured_root)
                    return dataset

        identified_fact_names = {
            self._sanitize_identifier(dataset.unique_name)
            for dataset in self._identify_fact_datasets(sml_model)
            if self._sanitize_identifier(dataset.unique_name)
        }

        relationship_degree: dict[str, int] = {}
        for rel in sml_model.relationships:
            if not rel.is_active:
                continue
            from_ds = self._sanitize_identifier(rel.from_dataset)
            to_ds = self._sanitize_identifier(rel.to_dataset)
            if from_ds:
                relationship_degree[from_ds] = relationship_degree.get(from_ds, 0) + 1
            if to_ds:
                relationship_degree[to_ds] = relationship_degree.get(to_ds, 0) + 1

        prefer_relational_root = (
            self._dbx_behavior.enable_metric_view_joins
            and self._dbx_behavior.enable_cross_table_joins
        )
        logger.info(
            "🔍 Root dataset selection: prefer_relational_root=%s, "
            "enable_metric_view_joins=%s, enable_cross_table_joins=%s",
            prefer_relational_root,
            self._dbx_behavior.enable_metric_view_joins,
            self._dbx_behavior.enable_cross_table_joins,
        )

        best: SMLDataset | None = None
        best_score: tuple[int, int, int, int] | None = None
        scoring_details = []
        for dataset in sml_model.datasets:
            ds_name = self._sanitize_identifier(dataset.unique_name)
            if not ds_name:
                continue
            if identified_fact_names and ds_name not in identified_fact_names:
                continue
            metric_count = len(metrics_by_dataset.get(ds_name, []))
            has_columns = 1 if dataset.columns else 0
            degree = relationship_degree.get(ds_name, 0)

            if prefer_relational_root:
                # In model-level metric views we prefer a relational anchor table
                # over measure-only datasets so joins can surface full table coverage.
                score = (
                    1 if degree > 0 else 0,
                    has_columns,
                    degree,
                    metric_count,
                )
            else:
                score = (
                    metric_count,
                    has_columns,
                    degree,
                    1 if metric_count > 0 else 0,
                )

            score_desc = f"{ds_name}:score={score}(has_rel={score[0]},has_cols={score[1]},degree={score[2]},metrics={score[3]})"
            scoring_details.append(score_desc)

            if hasattr(self, "_config_preferred_root") and self._config_preferred_root:
                pref_norm = self._sanitize_identifier(self._config_preferred_root).lower()
                if ds_name.lower() == pref_norm:
                    score = (9999, 9999, 9999, 9999)
                    scoring_details.append(f"{ds_name}:override=PREFERRED")

            if best_score is None or score > best_score:
                best = dataset
                best_score = score

        logger.info("🔍 Root dataset scoring (prefer_relational=%s): %s", prefer_relational_root, " | ".join(scoring_details))
        logger.info("🔍 Selected root dataset: %s (score=%s)", best.unique_name if best else "NONE", best_score)

        if best:
            return best
        return sml_model.datasets[0] if sml_model.datasets else None

    def _identify_fact_datasets(self, sml_model: SMLModel) -> list[SMLDataset]:
        """Return deterministic fact-like datasets for root selection and split planning.

        A dataset is considered fact-like when it participates on the many-side of
        relationships and/or hosts explicit aggregating metrics.
        """
        dataset_by_name: dict[str, SMLDataset] = {}
        for dataset in sml_model.datasets:
            ds_name = self._sanitize_identifier(dataset.unique_name)
            if ds_name:
                dataset_by_name[ds_name] = dataset

        if not dataset_by_name:
            return []

        relationship_many_votes: dict[str, int] = {name: 0 for name in dataset_by_name}
        for relationship in sml_model.relationships:
            if not relationship.is_active:
                continue
            from_name = self._sanitize_identifier(relationship.from_dataset)
            to_name = self._sanitize_identifier(relationship.to_dataset)
            cardinality = str(getattr(relationship.cardinality, "value", relationship.cardinality) or "").strip().lower()

            if cardinality in {"many-to-one", "many_to_one"}:
                if from_name in relationship_many_votes:
                    relationship_many_votes[from_name] += 1
            elif cardinality in {"one-to-many", "one_to_many"}:
                if to_name in relationship_many_votes:
                    relationship_many_votes[to_name] += 1
            elif cardinality in {"many-to-many", "many_to_many"}:
                if from_name in relationship_many_votes:
                    relationship_many_votes[from_name] += 1
                if to_name in relationship_many_votes:
                    relationship_many_votes[to_name] += 1
            else:
                # Conservative fallback for unknown cardinality: keep legacy semantic
                # where from_dataset is modeled as the driving side.
                if from_name in relationship_many_votes:
                    relationship_many_votes[from_name] += 1

        aggregation_votes: dict[str, int] = {name: 0 for name in dataset_by_name}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(str(metric.dataset or ""))
            if ds_name not in aggregation_votes:
                continue
            dataset = dataset_by_name.get(ds_name)
            if dataset and not dataset.columns:
                continue
            if metric.aggregation != AggregationType.NONE or bool(metric.source_column):
                aggregation_votes[ds_name] += 1

        scored: list[tuple[tuple[int, int, int, str], SMLDataset]] = []
        for ds_name, dataset in dataset_by_name.items():
            many_votes = relationship_many_votes.get(ds_name, 0)
            agg_votes = aggregation_votes.get(ds_name, 0)
            explicit_fact_vote = 1 if getattr(dataset, "is_fact", False) else 0

            if explicit_fact_vote == 0 and many_votes == 0 and agg_votes == 0:
                continue

            score = (explicit_fact_vote, many_votes, agg_votes, ds_name)
            scored.append((score, dataset))

        scored.sort(
            key=lambda item: (
                -item[0][0],
                -item[0][1],
                -item[0][2],
                item[0][3],
            )
        )
        return [dataset for _, dataset in scored]

    def _resolve_effective_metric_dataset_name(
        self,
        metric: SMLMetric,
        sml_model: SMLModel,
    ) -> str:
        """Return effective dataset name for metric deployment.

        Measures stored on a dummy/measure-only dataset can be re-anchored to a
        single referenced physical dataset when the reference is deterministic.
        """
        original_name = str(metric.dataset or "").strip()
        original_dataset = sml_model.get_dataset(metric.dataset)
        if not original_dataset or original_dataset.columns:
            return original_name

        expr = str(metric.expression or "").strip()
        if not expr:
            return original_name

        refs = re.findall(
            r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*\[[^\]]+\]",
            expr,
        )
        referenced_datasets: set[str] = set()
        for table_q, table in refs:
            candidate = str(table_q or table or "").strip()
            if not candidate:
                continue
            dataset = sml_model.get_dataset(candidate)
            if dataset and dataset.columns:
                referenced_datasets.add(dataset.unique_name)

        if len(referenced_datasets) != 1:
            fact_candidates = {ds.unique_name for ds in self._identify_fact_datasets(sml_model)}
            referenced_facts = [name for name in referenced_datasets if name in fact_candidates]
            if len(referenced_facts) == 1:
                resolved_name = referenced_facts[0]
                if self._sanitize_identifier(resolved_name) != self._sanitize_identifier(original_name):
                    logger.info(
                        "Relocating dummy-dataset metric '%s' from '%s' to fact '%s' for deployment.",
                        metric.unique_name,
                        self._sanitize_identifier(original_name),
                        self._sanitize_identifier(resolved_name),
                    )
                return resolved_name
            return original_name

        resolved_name = next(iter(referenced_datasets))
        if self._sanitize_identifier(resolved_name) != self._sanitize_identifier(original_name):
            logger.info(
                "Relocating dummy-dataset metric '%s' from '%s' to '%s' for deployment.",
                metric.unique_name,
                self._sanitize_identifier(original_name),
                self._sanitize_identifier(resolved_name),
            )
        return resolved_name

    def _build_effective_metric_dataset_maps(
        self,
        sml_model: SMLModel,
    ) -> tuple[dict[int, str], dict[str, list[SMLMetric]]]:
        """Build effective metric-to-dataset mapping and grouped metric index."""
        metric_dataset_by_id: dict[int, str] = {}
        metrics_by_dataset: dict[str, list[SMLMetric]] = {}

        for metric in sml_model.metrics:
            ds_name = self._resolve_effective_metric_dataset_name(metric, sml_model)
            if not ds_name:
                continue
            metric_dataset_by_id[id(metric)] = ds_name
            metrics_by_dataset.setdefault(self._sanitize_identifier(ds_name), []).append(metric)

        return metric_dataset_by_id, metrics_by_dataset

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
            sml_model=sml_model,
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

        self._validate_relationship_endpoints(sml_model, "measure-view generation")

        model_name = self._sanitize_identifier(sml_model.unique_name)
        requested_view_type = view_type_override or self._dbx_behavior.measure_view_type.strip().lower()

        if self._is_semantic_router_active_for_model(sml_model.unique_name) and requested_view_type == VIEW_TYPE_METRIC:
            return self._generate_per_fact_metric_views(sml_model, model_name)

        # DEBUG: Log the model artifact mode decision
        is_model_mode = self._is_model_artifact_mode()
        logger.info(
            "🔍 generate_measure_view_statements DEBUG: model=%s "
            "is_model_artifact_mode=%s view_type_override=%s measure_view_type_config=%s",
            model_name,
            is_model_mode,
            view_type_override or "(none)",
            self._dbx_behavior.measure_view_type,
        )

        if is_model_artifact_mode := self._is_model_artifact_mode():
            vtype = requested_view_type
            logger.info(
                "🔍 Per-model mode: resolved vtype=%s, comparing to VIEW_TYPE_METRIC=%s, match=%s",
                vtype,
                VIEW_TYPE_METRIC,
                vtype == VIEW_TYPE_METRIC,
            )
            if vtype == "none":
                logger.info("🔍 View type is 'none', returning empty")
                return [], 0, 0, []
            if vtype == VIEW_TYPE_METRIC:
                logger.info("🔍 Generating MODEL-LEVEL METRIC VIEW (YAML)")
                return self._generate_model_level_metric_view(sml_model, model_name)
            logger.info("🔍 Generating MODEL-LEVEL SQL VIEW (not YAML) - fallback path")
            return self._generate_model_level_view(sml_model, model_name)

        # Determine view technology
        vtype = requested_view_type
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

    def _generate_per_fact_metric_views(
        self,
        sml_model: SMLModel,
        model_name: str,
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """Generate one metric-view artifact per fact when semantic router is active."""
        stmts: list[str] = []
        skipped_details: list[dict[str, str]] = []
        self._per_fact_metric_views = {}

        if self._semantic_graph is None:
            try:
                self._initialize_semantic_router(sml_model)
            except ValueError as exc:
                raise DatabricksPublishError(str(exc)) from exc

        if self._semantic_graph is None:
            return self._generate_metric_view_statements(sml_model, model_name)

        fact_datasets = [
            dataset
            for dataset in sml_model.datasets
            if self._table_categories.get(dataset.unique_name, {}).get("category") == "FACT"
        ]
        fact_datasets = sorted(
            fact_datasets,
            key=lambda dataset: self._sanitize_identifier(dataset.unique_name),
        )

        if not fact_datasets:
            return self._generate_metric_view_statements(sml_model, model_name)

        fact_names = {dataset.unique_name for dataset in fact_datasets}
        injector = DimensionInjector()
        mapper = MeasureFactMapping()
        metric_name_index = self._build_metric_name_index(sml_model)
        metric_dataset_by_id, metrics_by_dataset = self._build_effective_metric_dataset_maps(sml_model)
        measure_to_facts = mapper.extract_measure_facts(
            sml_model.metrics,
            fact_names,
            injector,
            self._semantic_graph,
        )

        unanchored_metrics: set[str] = set()
        created = 0

        for fact_dataset in fact_datasets:
            fact_name = fact_dataset.unique_name
            reachable_dimensions = injector.get_dimensions_for_fact(fact_name, self._semantic_graph)
            contextual_metric_ids, fact_unanchored_metrics = self._get_contextual_metric_ids_for_fact(
                fact_name,
                reachable_dimensions,
                sml_model,
                measure_to_facts,
                metric_dataset_by_id,
                metric_name_index,
            )
            unanchored_metrics.update(fact_unanchored_metrics)

            allowed_tables = {fact_name, *reachable_dimensions}

            scoped_model = sml_model.model_copy(
                update={
                    "datasets": [
                        dataset
                        for dataset in sml_model.datasets
                        if dataset.unique_name in allowed_tables
                    ],
                    "relationships": [
                        relationship
                        for relationship in sml_model.relationships
                        if relationship.from_dataset in allowed_tables and relationship.to_dataset in allowed_tables
                    ],
                }
            )

            expected_source = self._resolve_source_table(fact_dataset)
            existing_source = self._resolve_existing_source_for_dataset(fact_dataset, expected_source)
            source_fq = existing_source or expected_source

            if existing_source:
                self._reconcile_dataset_schema(fact_dataset, source_fq)

            bindings = self._build_metric_view_column_bindings(fact_dataset, source_fq, scoped_model)
            join_prefixes = self._metric_view_join_prefixes(scoped_model, fact_dataset)

            resolved: list[ResolvedMeasure] = []
            for metric in sml_model.metrics:
                if id(metric) not in contextual_metric_ids:
                    continue

                measure_name = metric_name_index.get(
                    id(metric),
                    self._normalize_metric_identifier(metric.unique_name),
                )
                effective_dataset_name = metric_dataset_by_id.get(id(metric), str(metric.dataset or "").strip())
                metric_dataset = sml_model.get_dataset(effective_dataset_name) or sml_model.get_dataset(metric.dataset)
                if not metric_dataset:
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_VALIDATION_FAILED,
                    })
                    continue

                candidate_facts = measure_to_facts.get(metric.unique_name, set())
                metric_dataset_name = metric_dataset.unique_name
                if metric_dataset_name in fact_names and metric_dataset_name != fact_name:
                    continue
                if fact_name not in candidate_facts:
                    if not candidate_facts:
                        unanchored_metrics.add(measure_name)
                    continue

                dataset_key = self._sanitize_identifier(effective_dataset_name)
                measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                    metrics_by_dataset.get(dataset_key, []),
                    metric,
                )

                sql_expr, translation_type = self._resolve_measure_sql(
                    metric,
                    metric_dataset,
                    measure_sql_map=measure_sql_map,
                    allow_simple_sum_translation=not self._dbx_behavior.metric_view_only_sum_translation,
                    sml_model=sml_model,
                )

                if not sql_expr:
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED,
                        "translation_type": translation_type,
                    })
                    continue

                # Metric views do not allow scalar subqueries. Always attempt to
                # rewrite into the fact-root projection (plus join prefixes).
                metric_expr = self._rewrite_metric_view_measure_expression(
                    sql_expr,
                    fact_dataset,
                    source_fq,
                    bindings,
                    extra_allowed_prefixes=join_prefixes,
                )

                if not metric_expr:
                    skipped_details.append({
                        "name": measure_name,
                        "reason": DEPLOY_REASON_CROSS_TABLE,
                        "translation_type": translation_type,
                    })
                    continue

                resolved.append(
                    ResolvedMeasure(
                        name=measure_name,
                        sql_expression=metric_expr,
                        translation_type=translation_type,
                        confidence=self._assess_confidence(translation_type),
                        original_dax=(metric.expression or ""),
                    )
                )

            deployable = [
                rm for rm in resolved
                if rm.sql_expression and (
                    rm.confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM)
                    or self._dbx_behavior.enable_low_confidence_drafts
                )
            ]
            if not deployable:
                continue

            yaml_body = self._generate_metric_view_yaml(
                scoped_model,
                fact_dataset,
                source_fq,
                deployable,
                bindings,
            )

            fact_suffix = self._sanitize_identifier(fact_name)
            view_name = f"{self._sanitize_identifier(model_name)}_fact_{fact_suffix}_metric_view"
            view_fq = f"`{self.config.catalog}`.`{self.config.schema_name}`.`{view_name}`"

            view_sql = (
                f"CREATE OR REPLACE VIEW {view_fq} "
                "WITH METRICS LANGUAGE YAML AS $$\n"
                f"{yaml_body}\n$$"
            )
            stmts.append(view_sql)
            self._per_fact_metric_views[fact_name] = yaml_body
            created += 1

        for metric_name in sorted(unanchored_metrics):
            skipped_details.append({
                "name": metric_name,
                "reason": DEPLOY_REASON_VALIDATION_FAILED,
            })

        return stmts, created, len(skipped_details), skipped_details

    def _get_contextual_metric_ids_for_fact(
        self,
        fact_name: str,
        reachable_dimensions: set[str],
        sml_model: SMLModel,
        measure_to_facts: dict[str, set[str]],
        metric_dataset_by_id: dict[int, str],
        metric_name_index: dict[int, str],
    ) -> tuple[set[int], set[str]]:
        """Return metrics that are valid for one fact-scoped metric view."""
        normalized_valid_tables = {
            self._sanitize_identifier(fact_name).lower(),
            *{self._sanitize_identifier(name).lower() for name in reachable_dimensions},
        }

        contextual_metric_ids: set[int] = set()
        unanchored_metrics: set[str] = set()

        normalized_fact_name = self._sanitize_identifier(fact_name).lower()

        for metric in sml_model.metrics:
            metric_name = metric_name_index.get(
                id(metric),
                self._normalize_metric_identifier(metric.unique_name),
            )
            effective_dataset_name = metric_dataset_by_id.get(id(metric), str(metric.dataset or "").strip())
            if not effective_dataset_name:
                continue

            normalized_dataset_name = self._sanitize_identifier(effective_dataset_name).lower()
            if normalized_dataset_name not in normalized_valid_tables:
                continue

            candidate_facts = {
                self._sanitize_identifier(candidate_fact).lower()
                for candidate_fact in measure_to_facts.get(metric.unique_name, set())
            }
            if not candidate_facts:
                unanchored_metrics.add(metric_name)
                continue

            if normalized_fact_name not in candidate_facts:
                continue

            contextual_metric_ids.add(id(metric))

        return contextual_metric_ids, unanchored_metrics

    def _generate_model_level_view(
        self,
        sml_model: SMLModel,
        model_name: str,
    ) -> tuple[list[str], int, int, list[dict[str, str]]]:
        """Generate one model-level SQL view containing all deployable measures."""
        self._validate_relationship_endpoints(sml_model, "model-level SQL generation")

        stmts: list[str] = []
        created = 0
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []

        metric_name_index = self._build_metric_name_index(sml_model)
        metric_dataset_by_id, metrics_by_dataset = self._build_effective_metric_dataset_maps(sml_model)

        root_dataset = self._select_model_fact_dataset(sml_model, metrics_by_dataset)
        if not root_dataset:
            return stmts, created, skipped_count, skipped_details

        root_expected = self._resolve_source_table(root_dataset)
        root_existing = self._resolve_existing_source_for_dataset(root_dataset, root_expected) or root_expected

        missing_sources: set[str] = set()
        for dataset in sml_model.datasets:
            ds_name = self._sanitize_identifier(dataset.unique_name)
            if not ds_name:
                continue
            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)
            if not existing_source:
                missing_sources.add(ds_name)
        self._validate_relationship_source_coverage(
            sml_model,
            missing_sources,
            "model-level SQL generation",
        )

        select_parts: list[str] = []
        for metric in sml_model.metrics:
            measure_name = metric_name_index.get(
                id(metric),
                self._normalize_metric_identifier(metric.unique_name),
            )

            effective_dataset_name = metric_dataset_by_id.get(id(metric), str(metric.dataset or "").strip())
            dataset = sml_model.get_dataset(effective_dataset_name) or sml_model.get_dataset(metric.dataset)
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

            dataset_key = self._sanitize_identifier(
                metric_dataset_by_id.get(id(metric), str(metric.dataset or "").strip())
            )
            measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                metrics_by_dataset.get(dataset_key, []),
                metric,
            )
            sql_expr, translation_type = self._resolve_measure_sql(
                metric,
                dataset,
                measure_sql_map=measure_sql_map,
                allow_simple_sum_translation=not self._dbx_behavior.metric_view_only_sum_translation,
                sml_model=sml_model,
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
            inline_cols = self._extract_inline_source_columns_from_sql_expression(sql_expr)
            if inline_cols:
                physical_source_columns = {
                    str(col_name or "").strip().lower()
                    for col_name in self._get_source_table_columns(source_table)
                    if str(col_name or "").strip()
                }
                missing_inline_cols = [
                    col_name for col_name in inline_cols
                    if col_name.lower() not in physical_source_columns
                ]
                if missing_inline_cols:
                    source_relation = self._build_inline_null_source_relation(
                        source_table,
                        inline_cols,
                        [sql_expr],
                    )

            sql_view_expr = self._rewrite_sql_view_measure_expression(
                sql_expr,
                dataset,
                source_relation,
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
            lowered_expr = normalized_expr.lower()
            if lowered_expr.startswith("first("):
                pass
            elif lowered_expr.startswith("(select"):
                normalized_expr = f"first({normalized_expr})"
            else:
                needs_source = bool(re.search(r"`|\b(sum|avg|average|min|max|count|distinctcount)\s*\(", normalized_expr, re.IGNORECASE))
                if needs_source:
                    normalized_expr = f"(SELECT {normalized_expr} FROM {source_relation})"

            select_parts.append(f"    {normalized_expr} AS `{measure_name}`")

        if self._dbx_behavior.emit_metric_views_for_all_datasets:
            explicit_metric_datasets = {
                metric_dataset_by_id.get(id(metric), self._sanitize_identifier(metric.dataset))
                for metric in sml_model.metrics
                if metric_dataset_by_id.get(id(metric), self._sanitize_identifier(metric.dataset))
            }
            explicit_metric_datasets = {
                self._sanitize_identifier(name)
                for name in explicit_metric_datasets
                if name
            }
            for dataset in sml_model.datasets:
                ds_name = self._sanitize_identifier(dataset.unique_name)
                if not ds_name or ds_name in explicit_metric_datasets:
                    continue

                expected_source = self._resolve_source_table(dataset)
                source_fq = self._resolve_existing_source_for_dataset(dataset, expected_source) or expected_source
                synthetic_measures = self._build_dimension_only_resolved_measures(dataset, sml_model)
                for synthetic_measure in synthetic_measures:
                    measure_alias = self._sanitize_identifier(synthetic_measure.name)
                    synthetic_expr = f"first((SELECT {synthetic_measure.sql_expression} FROM {source_fq}))"
                    select_parts.append(
                        "    "
                        f"{synthetic_expr} "
                        f"AS `{ds_name.lower()}_{measure_alias.lower()}`"
                    )

        if not select_parts:
            for synthetic_measure in self._build_dimension_only_resolved_measures(root_dataset, sml_model):
                measure_alias = self._sanitize_identifier(synthetic_measure.name)
                select_parts.append(
                    "    "
                    f"first((SELECT {synthetic_measure.sql_expression} FROM {root_existing})) "
                    f"AS `{measure_alias.lower()}`"
                )

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
        self._validate_relationship_endpoints(sml_model, "model-level metric-view generation")

        stmts: list[str] = []
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []

        metric_name_index = self._build_metric_name_index(sml_model)
        metric_dataset_by_id, metrics_by_dataset = self._build_effective_metric_dataset_maps(sml_model)

        root_dataset = self._select_model_fact_dataset(sml_model, metrics_by_dataset)
        if not root_dataset:
            return stmts, 0, skipped_count, skipped_details

        root_expected = self._resolve_source_table(root_dataset)
        root_source = self._resolve_existing_source_for_dataset(root_dataset, root_expected) or root_expected
        root_bindings = self._build_metric_view_column_bindings(root_dataset, root_source, sml_model)
        root_join_prefixes = self._metric_view_join_prefixes(sml_model, root_dataset)

        missing_sources: set[str] = set()
        for dataset in sml_model.datasets:
            ds_name = self._sanitize_identifier(dataset.unique_name)
            if not ds_name:
                continue
            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)
            if not existing_source:
                missing_sources.add(ds_name)
        self._validate_relationship_source_coverage(
            sml_model,
            missing_sources,
            "model-level metric-view generation",
        )

        resolved_measures: list[ResolvedMeasure] = []
        root_dataset_name = self._sanitize_identifier(root_dataset.unique_name)
        for metric in sml_model.metrics:
            measure_name = metric_name_index.get(
                id(metric),
                self._normalize_metric_identifier(metric.unique_name),
            )

            effective_dataset_name = metric_dataset_by_id.get(id(metric), str(metric.dataset or "").strip())
            metric_dataset = sml_model.get_dataset(effective_dataset_name) or sml_model.get_dataset(metric.dataset)
            if not metric_dataset:
                if self._dbx_behavior.enable_low_confidence_drafts:
                    resolved_measures.append(
                        ResolvedMeasure(
                            name=measure_name,
                            sql_expression=DRAFT_MEASURE_SQL,
                            translation_type=TRANSLATION_TYPE_DAX_SKIPPED,
                            confidence=CONFIDENCE_LOW,
                            original_dax=(metric.expression or ""),
                            warnings=[DEPLOY_REASON_VALIDATION_FAILED],
                        )
                    )
                else:
                    skipped_count += 1
                    skipped_details.append(
                        {
                            "name": measure_name,
                            "reason": DEPLOY_REASON_VALIDATION_FAILED,
                            "translation_type": TRANSLATION_TYPE_DAX_SKIPPED,
                        }
                    )
                continue

            dataset_key = self._sanitize_identifier(
                metric_dataset_by_id.get(id(metric), str(metric.dataset or "").strip())
            )
            measure_sql_map = self._measure_translator.build_measure_sql_reference_map(
                metrics_by_dataset.get(dataset_key, []),
                metric,
            )
            sql_expr, translation_type = self._resolve_measure_sql(
                metric,
                metric_dataset,
                measure_sql_map=measure_sql_map,
                allow_simple_sum_translation=not self._dbx_behavior.metric_view_only_sum_translation,
                sml_model=sml_model,
            )

            if not sql_expr:
                if self._dbx_behavior.enable_low_confidence_drafts:
                    resolved_measures.append(
                        ResolvedMeasure(
                            name=measure_name,
                            sql_expression=DRAFT_MEASURE_SQL,
                            translation_type=translation_type,
                            confidence=CONFIDENCE_LOW,
                            original_dax=(metric.expression or ""),
                            warnings=[DEPLOY_REASON_DAX_NOT_SUPPORTED],
                        )
                    )
                else:
                    skipped_count += 1
                    skipped_details.append(
                        {
                            "name": measure_name,
                            "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED,
                            "translation_type": translation_type,
                        }
                    )
                continue

            # Metric views do not allow scalar subqueries. Rewrite everything into
            # the root projection (plus join prefixes) and drop anything that
            # still cannot be resolved.
            metric_expr = self._rewrite_metric_view_measure_expression(
                sql_expr,
                root_dataset,
                root_source,
                root_bindings,
                extra_allowed_prefixes=root_join_prefixes,
            )

            if not metric_expr:
                if self._dbx_behavior.enable_low_confidence_drafts:
                    resolved_measures.append(
                        ResolvedMeasure(
                            name=measure_name,
                            sql_expression=DRAFT_MEASURE_SQL,
                            translation_type=translation_type,
                            confidence=CONFIDENCE_LOW,
                            original_dax=(metric.expression or ""),
                            warnings=[DEPLOY_REASON_CROSS_TABLE],
                        )
                    )
                else:
                    skipped_count += 1
                    skipped_details.append(
                        {
                            "name": measure_name,
                            "reason": DEPLOY_REASON_CROSS_TABLE,
                            "translation_type": translation_type,
                        }
                    )
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
            resolved_measures = self._build_dimension_only_resolved_measures(root_dataset, sml_model)

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
            inline_column_names: list[str] = []
            seen_inline_cols: set[str] = set()
            for rm in resolved_measures:
                for col_name in self._extract_inline_source_columns_from_sql_expression(rm.sql_expression):
                    if col_name in seen_inline_cols:
                        continue
                    seen_inline_cols.add(col_name)
                    inline_column_names.append(col_name)

            inline_cols = self._build_inline_null_projection(
                inline_column_names,
                [rm.sql_expression for rm in resolved_measures if rm.sql_expression],
            )

            if inline_cols:
                lines.append("source: |")
                lines.append("  SELECT")
                for index, inline_col in enumerate(inline_cols):
                    suffix = "," if index < len(inline_cols) - 1 else ""
                    lines.append(f"  {inline_col}{suffix}")
                lines.append(f"  FROM {root_source.replace('`', '')}")
            else:
                lines.append(f"source: {yaml_quote(root_source.replace('`', ''))}")
        elif root_bindings and self._requires_source_alias_query(root_source, root_bindings):
            lines.append("source: " + self._build_metric_view_source_query(root_bindings, root_source))
        else:
            lines.append(f"source: {yaml_quote(root_source.replace('`', ''))}")

        # Emit joins using existing relationship-based join-tree machinery.
        joins_lines = self._generate_metric_view_joins_yaml(sml_model, root_dataset)
        lines.extend(joins_lines)

        lines.append("")
        lines.append("dimensions:")

        root_name = self._sanitize_identifier(root_dataset.unique_name).lower()
        has_tpch_shape = root_name == "orders"
        dimensions_added = 0
        used_dimension_names: set[str] = set()
        if has_tpch_shape:
            tpch_dimension_lines = [
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
            ]
            lines.extend(tpch_dimension_lines)
            dimensions_added += len(tpch_dimension_lines) // 2
            used_dimension_names.update({
                "ORDER_DATE",
                "ORDER_MONTH",
                "ORDER_YEAR",
                "ORDER_STATUS",
                "ORDER_PRIORITY",
                "CUSTOMER_NAME",
                "MARKET_SEGMENT",
                "CUSTOMER_NATION",
            })
        else:
            dimensions_added += self._append_model_level_dimension_bindings(
                lines,
                root_bindings,
                used_dimension_names,
            )

        if self._dbx_behavior.enable_metric_view_joins and self._dbx_behavior.enable_cross_table_joins:
            join_builder = JoinTreeBuilder(sml_model)
            try:
                join_tree = join_builder.build_join_tree(root_dataset.unique_name)
            except ValueError:
                join_tree = []
            dimensions_added += self._append_joined_metric_view_dimensions(
                sml_model,
                join_tree,
                used_dimension_names,
                lines,
            )

        if dimensions_added == 0:
            lines[-1] = "dimensions: []"

        lines.append("")
        lines.append("measures:")
        for rm in resolved_measures:
            expr = self._normalize_metric_view_sql_expression(rm.sql_expression)
            lines.append(f"  - name: {yaml_quote(rm.name)}")
            lines.append(f"    expr: {yaml_quote(expr)}")

        return "\n".join(lines)

    def _append_model_level_dimension_bindings(
        self,
        lines: list[str],
        bindings: list[MetricViewColumnBinding],
        used_dimension_names: set[str],
    ) -> int:
        """Append metric-view dimension bindings for the root dataset."""
        yaml_quote = self._yaml_quote
        dimensions_added = 0

        for binding in bindings:
            if not binding.include_as_dimension:
                continue
            unique_name = self._make_unique_projected_name(
                binding.projected_name,
                used_dimension_names,
                suffix="dim",
            )
            lines.append(f"  - name: {yaml_quote(unique_name)}")
            lines.append(f"    expr: {yaml_quote(f'`{binding.projected_name}`')}")
            used_dimension_names.add(unique_name.upper())
            dimensions_added += 1

        return dimensions_added

    def _append_joined_metric_view_dimensions(
        self,
        sml_model: SMLModel,
        joins: list[SMLJoin],
        used_dimension_names: set[str],
        lines: list[str],
        path_prefix: str = "",
    ) -> int:
        """Append dimensions for joined datasets into a single model-level metric view.

        Fix 2 — Schema introspection at transpile time:
            For each join, call ``_get_source_table_columns`` to retrieve the
            physical column list from Databricks.  Only bindings whose
            ``source_column`` actually exists in that table are written into the
            ``dimensions:`` block, preventing ``FIELD_NOT_FOUND`` errors at
            deploy time without needing a post-deploy degradation loop.

        Fix 3 — Declarative join_column_overrides:
            When ``behavior.databricks.join_column_overrides`` contains an entry
            for a join alias, its ``include_columns`` (allowlist) or
            ``exclude_columns`` (denylist) takes precedence over schema
            introspection for that join.
        """
        dimensions_added = 0
        join_overrides: dict[str, Any] = getattr(self._dbx_behavior, "join_column_overrides", {}) or {}

        for join in joins:
            dataset = self._find_dataset_by_metric_view_alias(sml_model, join.name)
            if not dataset:
                continue

            join_path = f"{path_prefix}.{join.name}" if path_prefix else join.name
            bindings = self._build_metric_view_column_bindings(dataset, join.source, sml_model)

            # ── Fix 3: declarative override resolution ─────────────────────────
            # Normalize alias key: try exact match then normalized (lowercase/stripped).
            override_key = join.name
            if override_key not in join_overrides:
                override_key = self._sanitize_identifier(join.name).lower()
            override_cfg: dict[str, Any] = join_overrides.get(override_key, {})
            include_cols: set[str] | None = None
            exclude_cols: set[str] = set()

            if "include_columns" in override_cfg:
                include_cols = {str(c).strip().lower() for c in (override_cfg["include_columns"] or [])}
                logger.info(
                    "join_column_overrides: join '%s' allowlist %d column(s)",
                    join.name,
                    len(include_cols),
                )
            elif "exclude_columns" in override_cfg:
                exclude_cols = {str(c).strip().lower() for c in (override_cfg["exclude_columns"] or [])}
                logger.info(
                    "join_column_overrides: join '%s' denylist %d column(s)",
                    join.name,
                    len(exclude_cols),
                )

            # ── Fix 2: schema introspection (only when no declarative allowlist) ──
            physical_cols: set[str] | None = None
            if include_cols is None:
                # Use the already-cached _get_source_table_columns.  The method
                # hits SHOW COLUMNS IN — result is cached per table via the
                # existing schema-cache so no extra RTTs are incurred.
                fetched = self._get_source_table_columns(join.source)
                if fetched:
                    physical_cols = fetched
                    logger.debug(
                        "Schema introspection for join '%s' (%s): %d physical column(s) found",
                        join.name,
                        join.source,
                        len(physical_cols),
                    )

            pruned_count = 0
            for binding in bindings:
                if not binding.include_as_dimension:
                    continue

                col_lower = binding.source_column.strip().lower()

                # Fix 3 — allowlist check (takes priority)
                if include_cols is not None and col_lower not in include_cols:
                    pruned_count += 1
                    continue

                # Fix 3 — denylist check
                if col_lower in exclude_cols:
                    pruned_count += 1
                    continue

                # Fix 2 — physical schema check (only when introspection succeeded)
                if physical_cols is not None and col_lower not in physical_cols:
                    pruned_count += 1
                    logger.debug(
                        "Pruned dimension '%s' from join '%s': column '%s' not in physical schema",
                        binding.projected_name,
                        join.name,
                        binding.source_column,
                    )
                    continue

                unique_name = self._make_unique_projected_name(
                    binding.projected_name,
                    used_dimension_names,
                    suffix="dim",
                )
                expr = f"{join_path}.{binding.source_column}"
                lines.append(f"  - name: {self._yaml_quote(unique_name)}")
                lines.append(f"    expr: {self._yaml_quote(expr)}")
                used_dimension_names.add(unique_name.upper())
                dimensions_added += 1

            if pruned_count:
                logger.info(
                    "Pruned %d dimension(s) from join '%s' (schema filter / override)",
                    pruned_count,
                    join.name,
                )

            if join.joins:
                dimensions_added += self._append_joined_metric_view_dimensions(
                    sml_model,
                    join.joins,
                    used_dimension_names,
                    lines,
                    path_prefix=join_path,
                )

        return dimensions_added


    def _find_dataset_by_metric_view_alias(
        self,
        sml_model: SMLModel,
        alias: str,
    ) -> SMLDataset | None:
        """Find a dataset whose sanitized name matches a metric-view join alias."""
        normalized_alias = self._sanitize_identifier(alias).lower()
        compact_alias = re.sub(r"[^a-z0-9]", "", normalized_alias)
        for dataset in sml_model.datasets:
            sanitized_name = self._sanitize_identifier(dataset.unique_name).lower()
            compact_name = re.sub(r"[^a-z0-9]", "", sanitized_name)
            if sanitized_name == normalized_alias or compact_name == compact_alias:
                return dataset
        return None

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
            inline_source_sql_expressions: list[str] = []

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
                    inline_source_sql_expressions.append(sql_expr)

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
                    inline_source_sql_expressions,
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
        self._validate_relationship_endpoints(sml_model, "combined SQL generation")

        stmts: list[str] = []
        created = 0
        skipped_count = 0
        skipped_details: list[dict[str, str]] = []
        metric_name_index = self._build_metric_name_index(sml_model)
        dataset_attempted = 0
        dataset_skipped_no_metrics = 0
        dataset_skipped_missing_source = 0
        dimension_only_deployed = 0
        implicit_measures_emitted = 0

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

        source_lookup: dict[str, tuple[str, str | None]] = {}
        missing_source_datasets: set[str] = set()
        for ds_name, dataset in ordered_datasets:
            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)
            source_lookup[ds_name] = (expected_source, existing_source)
            if not existing_source:
                missing_source_datasets.add(ds_name)

        self._validate_relationship_source_coverage(
            sml_model,
            missing_source_datasets,
            "combined SQL generation",
        )

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

            expected_source, existing_source = source_lookup.get(ds_name, (self._resolve_source_table(dataset), None))
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
                else:
                    skipped_count += 1
                    skipped_details.append({
                        "name": f"__dataset__:{self._sanitize_identifier(dataset.unique_name)}",
                        "reason": DEPLOY_REASON_PREREQUISITE_MISSING,
                        "translation_type": DEPLOY_REASON_GRAPH_INTEGRITY,
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
            inline_source_sql_expressions: list[str] = []

            measure_expressions: list[str] = []
            combined_group_by: set[str] = set()
            used_projection_names: set[str] = set()

            if not metrics:
                synthetic_measures = self._build_dimension_only_resolved_measures(dataset, sml_model)
                dimension_only_deployed += 1
                implicit_measures_emitted += len(synthetic_measures)
                for synthetic_measure in synthetic_measures:
                    safe_measure_alias = self._make_unique_projected_name(
                        synthetic_measure.name,
                        used_projection_names,
                        suffix="metric",
                    )
                    measure_expressions.append(
                        f"    {synthetic_measure.sql_expression} AS `{safe_measure_alias}`"
                    )
                    if not dataset.columns and synthetic_measure.sql_expression:
                        inline_source_columns.update(
                            self._extract_inline_source_columns_from_sql_expression(
                                synthetic_measure.sql_expression
                            )
                        )
                        inline_source_sql_expressions.append(synthetic_measure.sql_expression)
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
                    sml_model=sml_model,
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
                        inline_source_sql_expressions.append(sql_expr)
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
                    inline_source_sql_expressions,
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
            "skipped_no_metrics=%d, skipped_missing_source=%d, dimension_only=%d, implicit_measures=%d",
            sml_model.unique_name,
            len(ordered_datasets),
            dataset_attempted,
            created,
            dataset_skipped_no_metrics,
            dataset_skipped_missing_source,
            dimension_only_deployed,
            implicit_measures_emitted,
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

        self._last_skipped_details = list(skipped_details)
        review_required_summary = self._emit_review_diagnostics(skipped_details)
        self._build_routing_summary(
            sml_model=sml_model,
            view_statement_count=len(view_stmts),
            review_required_summary=review_required_summary,
        )

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

        # Build set of measures that got views + their translation types/expressions
        deployed_measures: dict[str, str] = {}  # name -> translation_type
        resolved_measure_expressions: dict[str, str] = {}  # name -> resolved sql expression
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
                    sml_model=sml_model,
                )
                if sql_expr:
                    deployed_measures[m_name] = translation_type
                    resolved_measure_expressions[m_name] = self._normalize_semantic_sql_aliases(sql_expr)

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
                raw_expr = resolved_measure_expressions.get(
                    m_name,
                    metric.sql_expression or metric.expression or "",
                )
                expr = self._escape_literal(raw_expr)

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
                "wait_timeout": self._statement_wait_timeout(),
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
                time.sleep(self._statement_poll_interval_seconds())
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

    def _generate_ui_lookups(self, sml_model: SMLModel) -> None:
        """Scan metrics for UI logic and generate lookups.sql & migration_report.md."""
        lookup_stmts = []
        migration_lines = [
            "# Migration Report: UI Measures",
            "The following DAX measures contained UI/formatting strings and were translated into dedicated text lookup tables.",
            ""
        ]

        schema_prefix = f"`{self.config.catalog}`.`{self.config.schema_name}`."
        model_name = self._sanitize_identifier(sml_model.unique_name)

        has_lookups = False
        self._ui_lookups = []

        for metric in sml_model.metrics:
            if not metric.expression:
                continue
            
            parsed = self._measure_translator.parse_switch_ui_logic(metric.expression)
            if not parsed:
                continue
                
            has_lookups = True
            
            table_name = f"{schema_prefix}{parsed['dataset'].lower()}_{self._sanitize_identifier(metric.unique_name).lower()}_callouts"
            
            self._ui_lookups.append({
                "metric_name": metric.unique_name,
                "dataset": parsed["dataset"],
                "column": parsed["column"],
                "table_name": table_name,
            })
            
            ddl = self._measure_translator.generate_lookup_ddl(metric.unique_name, parsed, schema_prefix=schema_prefix)
            lookup_stmts.append(f"-- Lookup Table for {metric.unique_name}")
            lookup_stmts.append(ddl)
            lookup_stmts.append("")

            # Add to report
            migration_lines.append(f"### {metric.unique_name}")
            migration_lines.append(f"- **Lookup Table Created:** `{schema_prefix}{parsed['dataset'].lower()}_{self._sanitize_identifier(metric.unique_name).lower()}_callouts`")
            migration_lines.append(f"- **Join Column:** `{parsed['column']}`")
            migration_lines.append(f"- **Action Required for BI Tool:** Instead of a DAX measure, join the above table into your Databricks semantic model using `{parsed['column']}` and use the `{self._sanitize_identifier(metric.unique_name).lower()}_text` column directly in your visuals.")
            migration_lines.append("")

        if has_lookups:
            # Persist to disk
            from pathlib import Path
            artifact_dir = Path("output") / "semantic_bridge" / model_name
            artifact_dir.mkdir(parents=True, exist_ok=True)
            
            sql_path = artifact_dir / "lookup_tables_ddl.sql"
            sql_path.write_text("\n".join(lookup_stmts), encoding="utf-8")
            
            report_path = artifact_dir / "migration_report.md"
            report_path.write_text("\n".join(migration_lines), encoding="utf-8")
            
            logger.info("✅ Extracted Category 3 UI measures and generated %s and %s", sql_path.name, report_path.name)


    def _apply_model_config_overrides(self, sml_model: SMLModel) -> None:
        """Apply config-driven SML graph mutations from semabridge.yaml hook."""
        from semabridge.core.config_loader import get_project_file_path
        import yaml as yaml_lib
        
        cfg_path = get_project_file_path("semabridge.yaml")
        if not cfg_path.exists():
            cfg_path = get_project_file_path("semabridge.yml")

        # Optional: allow model overrides to live in a separate file so the UI
        # can rewrite semabridge.yaml without clobbering `models:` config.
        overrides_path = get_project_file_path("semabridge.models.yaml")
        if not overrides_path.exists():
            overrides_path = get_project_file_path("semabridge.models.yml")
            
        model_cfg = {}
        if cfg_path.exists() or overrides_path.exists():
            try:
                base_cfg: dict[str, Any] = {}
                if cfg_path.exists():
                    base_cfg = yaml_lib.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

                overrides_cfg: dict[str, Any] = {}
                if overrides_path.exists():
                    overrides_cfg = yaml_lib.safe_load(overrides_path.read_text(encoding="utf-8")) or {}

                base_models = base_cfg.get("models", {})
                override_models = overrides_cfg.get("models", {})
                models_cfg: dict[str, Any] = {}
                if isinstance(base_models, dict):
                    models_cfg.update(base_models)
                if isinstance(override_models, dict):
                    models_cfg.update(override_models)

                # semabridge.yaml uses a free-form `models:` mapping that can be keyed
                # by display name (Fabric), sanitized name, or case variants.
                model_key = str(sml_model.unique_name or "").strip()
                if isinstance(models_cfg, dict) and model_key:
                    model_cfg = models_cfg.get(model_key, {})
                    if not model_cfg:
                        want = self._sanitize_identifier(model_key).lower()
                        for k, v in models_cfg.items():
                            candidate = str(k or "").strip()
                            if not candidate:
                                continue
                            if candidate.lower() == model_key.lower():
                                model_cfg = v
                                break
                            if self._sanitize_identifier(candidate).lower() == want:
                                model_cfg = v
                                break
            except Exception as e:
                logger.warning("Failed to parse semabridge.yaml: %s", str(e))
        # -------------------------------------------------------------
        # 1. APPLY AUTOMATED SYSTEM HEURISTICS (Zero-Config Fixes)
        # -------------------------------------------------------------
        # Auto-prune universally virtual tables (0 physical columns)
        virtual_tables = {ds.unique_name for ds in sml_model.datasets if not ds.columns}
        if virtual_tables:
            logger.info("  - [Auto] Pruning %d completely virtual measure tables: %s", len(virtual_tables), virtual_tables)
            sml_model.datasets = [ds for ds in sml_model.datasets if ds.unique_name not in virtual_tables]
            
            # Since we just pruned tables, re-parent their metrics to a robust dataset
            target_ds = sml_model.datasets[0].unique_name if sml_model.datasets else None
            # If the user's config specifies a preferred root, use that
            if model_cfg.get("preferred_root_table"):
                target_ds = model_cfg["preferred_root_table"]
                
            if target_ds:
                for metric in sml_model.metrics:
                    if metric.dataset and metric.dataset in virtual_tables:
                        logger.info("  - [Auto] Re-parented virtual metric '%s' to '%s'", metric.unique_name, target_ds)
                        metric.dataset = target_ds

                        # Immediately pre-translate simple DAX to SQL so the downstream
                        # pipeline never wraps it in a subquery against the now-pruned
                        # virtual table (e.g., project_measures).
                        if not metric.sql_expression and metric.expression:
                            pre_translated = self._measure_translator.try_simple_dax_to_sql(
                                metric.expression.strip()
                            )
                            if pre_translated:
                                metric.sql_expression = pre_translated
                                metric.expression = ""
                                logger.info(
                                    "  - [Auto] Pre-translated DAX for re-parented metric '%s': %s",
                                    metric.unique_name,
                                    pre_translated,
                                )

        # Auto-detect correlated fiscal subqueries that fail in Databricks Metric YAML
        import re
        has_fiscal_nested = False
        target_subquery_regex = re.compile(r"\(\s*SELECT\s+MAX\(`?fiscal_yr_period`?\).*?CURRENT_DATE(?:\(\))?\s*\)", re.IGNORECASE | re.DOTALL)
        
        # Hardcoded Tier 4 fallback routines matching semabridge.yaml mappings natively
        is_inventory_model = "inventory" in self._sanitize_identifier(sml_model.unique_name).lower()
        tier4_overrides: dict[str, str] = {
             "corporate_dsi": "SUM(CASE WHEN `fiscal_yr_period` < (SELECT MAX(fiscal_yr_period) FROM semabridge.public.Dates WHERE cal_dt = current_date()) THEN `ioh_excldng_lifo_amt` ELSE 0 END)",
             "corporate_dsi_monthly": "SUM(CASE WHEN `fiscal_yr_period` < (SELECT MAX(fiscal_yr_period) FROM semabridge.public.Dates WHERE cal_dt = current_date()) THEN `dsi_mnthly` ELSE 0 END)",
             # Callout measures: wrapped in ANY_VALUE() so Databricks treats them as a
             # single-value aggregate (Power BI SELECTEDVALUE semantics) instead of
             # row-level expressions which Unity Catalog metric views reject.
             "subledger_business_unit_callout": "ANY_VALUE(CASE WHEN `business_unit` = 'USP' THEN '\u2022 Source of dashboard is SAP – Data as of previous day' WHEN `business_unit` = 'MSH' THEN '\u2022 Source of dashboard is SAP – Data as of previous day' ELSE '' END)",
             "gl_business_unit_callout": "ANY_VALUE(CASE WHEN `business_unit` = 'USP' THEN '\u2022 GL data sourced from GRC (Oracle)' WHEN `business_unit` = 'MSH' THEN '\u2022 GL data sourced from GRC (Oracle)' ELSE '' END)",
             "dsi_calculation_callout": "ANY_VALUE(CONCAT('\u2022 Inventory Days on Hand = Ending Inventory / (COS / Days in Period)', CHAR(10), '\u2022 Corporate IOH = ', CAST(`ioh_excldng_lifo_amt` AS STRING)))",
             "source_value_total_stock": "SUM(`source_value_total_stock`)",
             "wac_value_total_stock": "SUM(`wac_value_total_stock`)",
             # today() is a scalar — wrap in MAX() so it is aggregate-safe
             "today": "MAX(current_date())",
        }

        for metric in sml_model.metrics:
            if is_inventory_model:
                normalized = self._sanitize_identifier(metric.unique_name).lower()
                if normalized in tier4_overrides:
                    metric.sql_expression = tier4_overrides[normalized]
                    metric.expression = ""
                    logger.debug("  - [Auto] Resolved untranslatable DAX mapping for '%s'", metric.unique_name)
                    has_fiscal_nested = True
            
            if metric.sql_expression and target_subquery_regex.search(metric.sql_expression):
                has_fiscal_nested = True
                metric.sql_expression = target_subquery_regex.sub(
                    r"`_current_fiscal_period`",
                    metric.sql_expression,
                )
                logger.debug("  - [Auto] Subquery rewritten for metric '%s'", metric.unique_name)
                
        if has_fiscal_nested:
            logger.info("  - [Auto] Nested fiscal subqueries detected! Bootstrapping standard threshold inside root SOURCE loop.")
            auto_inj = f"(SELECT MAX(fiscal_yr_period) FROM `{self.config.catalog}`.`{self.config.schema_name}`.Dates WHERE cal_dt = CURRENT_DATE()) AS `_current_fiscal_period`"
            if not hasattr(self, "_config_source_sql"):
                self._config_source_sql = []
            if auto_inj not in self._config_source_sql:
                self._config_source_sql.append(auto_inj)

        if not model_cfg:
            return
            
        logger.info("Applying transform hook overrides from semabridge.yaml for '%s'", sml_model.unique_name)
        
        ignore_tables = model_cfg.get("ignore_tables", [])
        if ignore_tables:
            ignore_set = {self._sanitize_identifier(t).lower() for t in ignore_tables}
            original_count = len(sml_model.datasets)
            sml_model.datasets = [ds for ds in sml_model.datasets if self._sanitize_identifier(ds.unique_name).lower() not in ignore_set]
            if original_count > len(sml_model.datasets):
                logger.info("  - Removed %d virtual datasets matching ignore_tables", original_count - len(sml_model.datasets))
            
        pref_root = model_cfg.get("preferred_root_table")
        if pref_root:
            logger.info("  - Set preferred_root_table to '%s'", pref_root)
            self._config_preferred_root = pref_root

        source_injs = model_cfg.get("source_columns_sql", [])
        if source_injs:
            logger.info("  - Loaded %d source_columns_sql injections", len(source_injs))
            self._config_source_sql = source_injs

        # Re-parent orphaned metrics
        if ignore_tables and len(sml_model.datasets) > 0:
            target_ds = pref_root or sml_model.datasets[0].unique_name
            for metric in sml_model.metrics:
                if metric.dataset and self._sanitize_identifier(metric.dataset).lower() in ignore_set:
                    logger.info("  - Re-parented orphaned metric '%s' to '%s'", metric.unique_name, target_ds)
                    metric.dataset = target_ds

        pre_joins = model_cfg.get("pre_joins", [])
        if pre_joins:
            logger.info("  - Loaded %d pre_joins definitions", len(pre_joins))
            self._config_pre_joins = pre_joins
            
        overrides = model_cfg.get("measure_sql_overrides", {})
        if overrides:
            logger.info("  - Loaded %d measure_sql_overrides", len(overrides))
            # Allow overrides keyed by either the Fabric display name ("Corporate IOH")
            # or the normalized SQL-safe identifier ("Corporate_IOH").
            normalized_overrides: dict[str, str] = {}
            for raw_key, raw_sql in overrides.items():
                key = str(raw_key or "").strip()
                sql_text = str(raw_sql or "").strip()
                if not key or not sql_text:
                    continue
                candidates = {
                    key,
                    self._sanitize_identifier(key),
                    self._normalize_metric_identifier(key),
                }
                for candidate in candidates:
                    norm = self._sanitize_identifier(candidate).lower()
                    if norm and norm not in normalized_overrides:
                        normalized_overrides[norm] = sql_text

            # Also support overrides keyed by the final emitted metric-view measure name.
            # In practice this is usually the normalized identifier, but it can differ due to
            # dataset-scoped deduping (e.g. "Measure", "Measure_2") or symbol rewrites.
            metric_name_index: dict[int, str] = self._build_metric_name_index(sml_model)

            for metric in sml_model.metrics:
                raw_name = str(metric.unique_name or "").strip()
                if not raw_name:
                    continue
                emitted_name = str(metric_name_index.get(id(metric), "") or "").strip()
                lookup_keys = [
                    self._sanitize_identifier(raw_name).lower(),
                    self._normalize_metric_identifier(raw_name).lower(),
                    self._sanitize_identifier(emitted_name).lower() if emitted_name else "",
                ]
                sql_override = None
                for lk in lookup_keys:
                    if lk and lk in normalized_overrides:
                        sql_override = normalized_overrides[lk]
                        break
                if sql_override:
                    metric.sql_expression = sql_override
                    metric.expression = ""

    def publish(self, sml_model: SMLModel) -> str:
        """Generate and execute Databricks statements.

        Three-phase deployment:
            1. Probe runtime for metric view support (if auto)
            2. Run quota-aware preflight for Databricks artifacts
            3. Deploy metadata table and measure views

        Returns:
            A URI identifying the deployed artifact.
        """
        self._apply_model_config_overrides(sml_model)
        
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
            self._auto_infer_missing_relationships(sml_model)
            self._auto_initialize_missing_tables(
                sml_model,
                resolved_view_type=resolved_view_type,
            )
            self._auto_bridge_relationship_join_keys(sml_model)
            self._validate_source_table_schema(sml_model)
            try:
                self._initialize_semantic_router(sml_model)
            except ValueError as exc:
                raise DatabricksPublishError(str(exc)) from exc

            # Extract Category 3 UI metrics into standalone lookup tables
            self._generate_ui_lookups(sml_model)

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
            failed_view_classes: dict[str, int] = {}
            sql_fallback_state = SQL_FALLBACK_NOT_TRIGGERED

            def _deploy_single_view(view_sql: str) -> tuple[str, bool, str, str]:
                """Deploy a single view with retry via RetryManager.

                Returns:
                    (view_name, success, error_message, error_class)
                """
                view_name = "unknown"
                match = re.search(r'VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)', view_sql)
                if match:
                    view_name = match.group(1)

                # Fix 1 — Pre-flight dimension schema validation.
                # Strip any dimension entries that reference columns that do not
                # exist in the physical join table, preventing FIELD_NOT_FOUND
                # before the statement reaches Databricks.
                if (
                    "WITH METRICS LANGUAGE YAML" in view_sql
                    and getattr(self._dbx_behavior, "preflight_validate_join_dimensions", True)
                ):
                    view_sql = self._preflight_strip_bad_dimensions(view_sql)

                # Pre-flight object-type conflict resolution.
                # Detects a TABLE/wrong-type VIEW already sitting at the target
                # name and drops it *before* the first deploy attempt, avoiding
                # a wasted round-trip and a confusing error log.
                if view_name != "unknown" and self._dbx_behavior.enable_destructive_sync_operations:
                    is_metric = "WITH METRICS LANGUAGE YAML" in view_sql
                    self._preflight_resolve_object_conflict(view_name, is_metric=is_metric)

                try:

                    retry_mgr.execute_with_retry(
                        operation=lambda: self.execute_statements([view_sql]),
                        operation_name=f"deploy_view:{view_name}",
                    )
                    return view_name, True, "", ""
                except Exception as exc:
                    error_text = str(exc)
                    if "WITH METRICS LANGUAGE YAML" in view_sql and "UNRESOLVED_COLUMN" in error_text.upper():
                        current_sql = view_sql
                        degraded_steps = 0
                        max_degraded_steps = 8

                        while degraded_steps < max_degraded_steps and "UNRESOLVED_COLUMN" in error_text.upper():
                            degraded_sql = self._remove_failing_metric_measure(current_sql, error_text)
                            if not degraded_sql or degraded_sql == current_sql:
                                break

                            degraded_steps += 1
                            current_sql = degraded_sql
                            try:
                                retry_mgr.execute_with_retry(
                                    operation=lambda: self.execute_statements([current_sql]),
                                    operation_name=f"deploy_view_degraded:{view_name}:{degraded_steps}",
                                )
                                logger.warning(
                                    "Deployed metric view %s after %d unresolved-measure degradation step(s)",
                                    view_name,
                                    degraded_steps,
                                )
                                return view_name, True, "", ""
                            except Exception as degraded_exc:
                                error_text = str(degraded_exc)

                    conflict_detected = self._is_object_type_conflict_error(error_text)
                    if view_name != "unknown" and conflict_detected and self._dbx_behavior.enable_destructive_sync_operations:
                        logger.warning(
                            "Databricks object-type conflict for %s; attempting DROP VIEW/TABLE cleanup and retry once.",
                            view_name,
                        )
                        try:
                            self.execute_statements([f"DROP VIEW IF EXISTS {view_name}"])
                        except Exception as drop_view_exc:
                            logger.debug("DROP VIEW cleanup failed for %s: %s", view_name, str(drop_view_exc)[:200])

                        try:
                            self.execute_statements([f"DROP TABLE IF EXISTS {view_name}"])
                        except Exception as drop_table_exc:
                            logger.debug("DROP TABLE cleanup failed for %s: %s", view_name, str(drop_table_exc)[:200])

                        try:
                            self.execute_statements([view_sql])
                            return view_name, True, "", ""
                        except Exception as retry_exc:
                            retry_error = str(retry_exc)
                            retry_class, _ = self._classify_metric_view_deploy_error(retry_error)
                            return view_name, False, retry_error[:300], retry_class

                    if view_name != "unknown" and conflict_detected and not self._dbx_behavior.enable_destructive_sync_operations:
                        logger.warning(
                            "Databricks object-type conflict for %s; destructive cleanup is disabled (enable_destructive_sync_operations=false).",
                            view_name,
                        )

                    error_class, _ = self._classify_metric_view_deploy_error(error_text)
                    return view_name, False, error_text[:300], error_class

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
                        view_name, success, error_msg, error_class = future.result()
                        if success:
                            views_success += 1
                            logger.info("✅ Deployed measure view: %s", view_name)
                        else:
                            views_failed += 1
                            failed_view_names.append(view_name)
                            if error_class:
                                failed_view_classes[error_class] = failed_view_classes.get(error_class, 0) + 1
                            logger.warning(
                                "⚠️  Measure view %s failed (class=%s): %s",
                                view_name,
                                error_class or ERROR_CLASS_GENERIC,
                                error_msg,
                            )

                deploy_elapsed = time.monotonic() - deploy_start
                logger.info(
                    "📊 View deployment complete: %d/%d succeeded in %.1fs",
                    views_success, len(view_stmts), deploy_elapsed,
                )

            if views_failed:
                failed_views_preview = ", ".join(failed_view_names[:5])
                class_summary = ", ".join(
                    f"{klass}={count}" for klass, count in sorted(failed_view_classes.items())
                ) or "uncategorized"
                logger.warning(
                    "⚠️  %d/%d measure views failed in Databricks. classes=[%s]. "
                    "Failed views: %s%s. Metadata table deployed OK.",
                    views_failed,
                    len(view_stmts),
                    class_summary,
                    failed_views_preview or "unknown",
                    "..." if len(failed_view_names) > 5 else "",
                )

            if (
                resolved_view_type == VIEW_TYPE_METRIC
                and view_stmts
                and views_failed > 0
            ):
                if not self._allow_metric_deploy_sql_fallback():
                    sql_fallback_state = SQL_FALLBACK_SKIPPED
                    self._log_sql_fallback_state(
                        model_name=sml_model.unique_name,
                        state=sql_fallback_state,
                        reason="fallback_disabled_by_policy",
                    )
                    logger.warning(
                        "Metric-view deployment had %d failed view(s), but SQL fallback is disabled because "
                        "measure_view_type is explicitly '%s'. Keeping metric-view mode.",
                        views_failed,
                        self._dbx_behavior.measure_view_type,
                    )
                    return f"databricks://{self.config.catalog}/{self.config.schema_name}/{sml_model.unique_name}"

                sql_fallback_state = SQL_FALLBACK_IN_PROGRESS
                self._log_sql_fallback_state(
                    model_name=sml_model.unique_name,
                    state=sql_fallback_state,
                    reason=f"metric_views_failed={views_failed}",
                )
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
                try:
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
                                    error_text = str(e)
                                    if view_name != "unknown" and self._is_object_type_conflict_error(error_text):
                                        if self._dbx_behavior.enable_destructive_sync_operations:
                                            logger.warning(
                                                "SQL fallback object-type conflict for %s; attempting DROP VIEW/TABLE cleanup and retry once.",
                                                view_name,
                                            )
                                            try:
                                                self.execute_statements([f"DROP VIEW IF EXISTS {view_name}"])
                                            except Exception as drop_view_exc:
                                                logger.debug("DROP VIEW cleanup failed for %s: %s", view_name, str(drop_view_exc)[:200])

                                            try:
                                                self.execute_statements([f"DROP TABLE IF EXISTS {view_name}"])
                                            except Exception as drop_table_exc:
                                                logger.debug("DROP TABLE cleanup failed for %s: %s", view_name, str(drop_table_exc)[:200])

                                            try:
                                                self.execute_statements([view_sql])
                                                logger.info("Deployed SQL fallback view after cleanup: %s", view_name)
                                                continue
                                            except DatabricksPublishError as cleanup_retry_error:
                                                error_text = str(cleanup_retry_error)
                                        else:
                                            logger.warning(
                                                "SQL fallback object-type conflict for %s; destructive cleanup is disabled (enable_destructive_sync_operations=false).",
                                                view_name,
                                            )

                                    fallback_failed += 1
                                    self._persist_sql_debug_error_artifact(
                                        sql_debug_artifacts[idx] if idx < len(sql_debug_artifacts) else "",
                                        error_text,
                                    )
                                    logger.warning(
                                        "SQL fallback view %s failed. Error: %s",
                                        view_name,
                                        error_text[:200],
                                    )
                    else:
                        fallback_failed = 1
                        logger.warning(
                            "SQL fallback could not start for model '%s': no SQL fallback views were generated.",
                            sml_model.unique_name,
                        )
                finally:
                    sql_fallback_state = SQL_FALLBACK_FAILED if fallback_failed else SQL_FALLBACK_SUCCESS
                    self._log_sql_fallback_state(
                        model_name=sml_model.unique_name,
                        state=sql_fallback_state,
                        reason=f"failed_views={fallback_failed};total_views={len(fallback_view_stmts)}",
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