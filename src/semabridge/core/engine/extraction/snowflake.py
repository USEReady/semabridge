from __future__ import annotations
import time
import uuid
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Literal, Optional
import yaml
from pydantic import Field
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.core.settings import Settings, get_settings
from semabridge.core.config_loader import get_project_file_path
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.run_summary import (
    STEP_NAMES,
    RunStatus,
    RunSummary,
    StepStatus,
    create_run_summary,
)
from semabridge.core.source_format import (
    SourceFormat,
    from_fabric_tmsl,
    from_pbix_tmsl,
    from_snowflake_metadata,
)
from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel, SMLRelationship
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.exceptions import (
    ConfigValidationError,
    AuthenticationError,
    ExtractionError,
    SourceFormatError,
    ConversionError,
    PersistenceError,
    DeploymentError,
)

logger = get_logger(__name__)
def _extract_snowflake_scoped(
    self,
    context: RunContext,
    dataset_id: Optional[str],
    identity_id: str,
) -> SourceFormat:
    """Run Snowflake extraction under scoped account credentials.

    Looks up the Account row by ``identity_id``, decrypts its credential
    bundle, injects credentials via ``scoped_account_env``, and delegates
    to the standard extraction pipeline.

    Args:
        context: Current run context.
        dataset_id: Optional dataset scope.
        identity_id: Account row UUID.

    Returns:
        Extracted SourceFormat.

    Raises:
        ExtractionError: When identity cannot be resolved.
    """
    from sqlalchemy import select
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.auth.account_credential_resolver import scoped_account_env
    try:
        with db_manager.get_session() as session:
            account = session.execute(
                select(Account).where(
                    Account.connector_type == "SNOWFLAKE",
                    Account.id == identity_id,
                )
            ).scalars().first()

            if not account:
                raise ExtractionError(
                    f"No Snowflake account found for identity_id '{identity_id}'. "
                    "Please link this account in the Connections panel."
                )

            with scoped_account_env(account, session):
                logger.info(
                    "Snowflake extraction scoped to account %s (%s)",
                    account.tag, identity_id,
                )
                # Reload settings to pick up injected env vars
                from semabridge.core.settings import reload_settings
                scoped_settings = reload_settings()
                context = RunContext(
                    project_id=context.project_id,
                    run_id=context.run_id,
                    config=scoped_settings,
                    source_type=context.source_type,
                    target_type=context.target_type,
                    behavior=context.behavior,
                )
                # Clear identity_id to prevent infinite recursion
                return self._extract_snowflake_unscoped(context, dataset_id)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            f"Failed to resolve Snowflake credentials for identity '{identity_id}': {exc}"
        ) from exc
def _extract_snowflake(
    self,
    context: RunContext,
    dataset_id: Optional[str] = None,
) -> SourceFormat:
    """Extract from Snowflake.

    When ``identity_id`` is present in the source config, credentials are
    resolved from the linked Account row and injected via
    ``scoped_account_env`` for the duration of this extraction.
    Otherwise, falls back to global env vars (backward compatibility).
    """
    from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
    from semabridge.utils.cache import MetadataCache

    config = context.config
    source_config = getattr(config, "source", None)
    identity_id: str = str(getattr(source_config, "identity_id", "") or "").strip()

    # Resolve per-account credentials when identity_id is specified
    if identity_id:
        return self._extract_snowflake_scoped(context, dataset_id, identity_id)

    cache = MetadataCache(config.model.cache_dir) if config.model.cache_enabled else None

    # BACKUP (old behavior): serial extraction + only env-based include list
    # extractor = SnowflakeExtractor(
    #     config=config.snowflake,
    #     cache=cache,
    #     exclude_tables=config.model.excluded_table_list,
    #     include_tables=config.model.included_table_list,
    # )
    # metadata = extractor.extract_all()

    include_tables, include_source = self._resolve_snowflake_include_tables(context)
    semantic_view_name: Optional[str] = None
    semantic_view_ddl: Optional[str] = None

    # If a specific model was requested for this run, scope extraction to that model.
    # For Snowflake this may be a semantic view name or a table name.
    if dataset_id:
        scope_probe = SnowflakeExtractor(
            config=config.snowflake,
            cache=cache,
            exclude_tables=config.model.excluded_table_list,
            include_tables=None,
        )
        scoped_tables, semantic_view_name, semantic_view_ddl = self._resolve_snowflake_dataset_scope(
            dataset_id,
            scope_probe,
        )
        if scoped_tables:
            include_tables = scoped_tables
            include_source = "dataset_id.semantic_view" if semantic_view_name else "dataset_id.table"

    parallel_enabled, max_workers = self._resolve_snowflake_parallelism(context)

    logger.info(
        "Snowflake extraction plan: include_tables=%s source=%s parallel=%s workers=%s",
        len(include_tables) if include_tables else 0,
        include_source or "none",
        parallel_enabled,
        max_workers,
    )

    extractor = SnowflakeExtractor(
        config=config.snowflake,
        cache=cache,
        exclude_tables=config.model.excluded_table_list,
        include_tables=include_tables,
    )

    metadata = extractor.extract_all(
        parallel=parallel_enabled,
        max_workers=max_workers,
    )

    # BACKUP (old behavior): no compatibility retry when include filter matched zero tables
    # semantic_data = extractor.read_semantic_tables()
    # metadata["semantic_tables"] = semantic_data

    # Backward compatibility guard:
    # if include list came from project YAML/UI and resolves to zero tables,
    # retry without include filter to preserve legacy "full-schema" behavior.
    strict_sources = {
        "source.models",
        "selection.model_ids",
        "dataset_id.semantic_view",
        "dataset_id.table",
    }
    if include_tables and not metadata.get("tables"):
        if include_source in strict_sources:
            raise ExtractionError(
                f"Include filter from {include_source} matched 0 tables for '{dataset_id}'. "
                "Refine the selected model or verify semantic view/table names."
            )
        if include_source != "model.include_tables":
            logger.warning(
                "Include filter from %s matched 0 tables; retrying full schema extraction for compatibility",
                include_source,
            )
            extractor = SnowflakeExtractor(
                config=config.snowflake,
                cache=cache,
                exclude_tables=config.model.excluded_table_list,
                include_tables=None,
            )
            metadata = extractor.extract_all(
                parallel=parallel_enabled,
                max_workers=max_workers,
            )

    semantic_data = extractor.read_semantic_tables()
    metadata["semantic_tables"] = semantic_data

    source_format = from_snowflake_metadata(
        project_id=context.project_id,
        run_id=context.run_id,
        metadata=metadata,
        semantic_view_name=semantic_view_name or dataset_id,
        semantic_view_ddl=semantic_view_ddl,
    )

    table_count = len(source_format.tables)
    self._record_step(4, StepStatus.SUCCESS, f"Extracted {table_count} tables")

    return source_format

def _extract_snowflake_unscoped(
    self,
    context: RunContext,
    dataset_id: Optional[str] = None,
) -> SourceFormat:
    """Run Snowflake extraction without identity_id resolution.

    Called by ``_extract_snowflake_scoped`` after credentials have been
    injected into ``os.environ``. Delegates to the main extraction body
    but skips the identity_id check to prevent infinite recursion.
    """
    from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
    from semabridge.utils.cache import MetadataCache

    config = context.config
    cache = MetadataCache(config.model.cache_dir) if config.model.cache_enabled else None

    include_tables, include_source = self._resolve_snowflake_include_tables(context)
    semantic_view_name: Optional[str] = None
    semantic_view_ddl: Optional[str] = None

    if dataset_id:
        scope_probe = SnowflakeExtractor(
            config=config.snowflake,
            cache=cache,
            exclude_tables=config.model.excluded_table_list,
            include_tables=None,
        )
        scoped_tables, semantic_view_name, semantic_view_ddl = self._resolve_snowflake_dataset_scope(
            dataset_id,
            scope_probe,
        )
        if scoped_tables:
            include_tables = scoped_tables
            include_source = "dataset_id.semantic_view" if semantic_view_name else "dataset_id.table"

    parallel_enabled, max_workers = self._resolve_snowflake_parallelism(context)

    extractor = SnowflakeExtractor(
        config=config.snowflake,
        cache=cache,
        exclude_tables=config.model.excluded_table_list,
        include_tables=include_tables,
    )
    metadata = extractor.extract_all(
        parallel=parallel_enabled,
        max_workers=max_workers,
    )

    strict_sources = {
        "source.models",
        "selection.model_ids",
        "dataset_id.semantic_view",
        "dataset_id.table",
    }
    if include_tables and not metadata.get("tables"):
        if include_source in strict_sources:
            raise ExtractionError(
                f"Include filter from {include_source} matched 0 tables for '{dataset_id}'. "
                "Refine the selected model or verify semantic view/table names."
            )
        if include_source != "model.include_tables":
            extractor = SnowflakeExtractor(
                config=config.snowflake,
                cache=cache,
                exclude_tables=config.model.excluded_table_list,
                include_tables=None,
            )
            metadata = extractor.extract_all(
                parallel=parallel_enabled,
                max_workers=max_workers,
            )

    semantic_data = extractor.read_semantic_tables()
    metadata["semantic_tables"] = semantic_data

    source_format = from_snowflake_metadata(
        project_id=context.project_id,
        run_id=context.run_id,
        metadata=metadata,
        semantic_view_name=semantic_view_name or dataset_id,
        semantic_view_ddl=semantic_view_ddl,
    )

    table_count = len(source_format.tables)
    self._record_step(4, StepStatus.SUCCESS, f"Extracted {table_count} tables (scoped)")

    return source_format

def _resolve_snowflake_dataset_scope(
    self,
    dataset_id: str,
    extractor: Any,
) -> tuple[Optional[list[str]], Optional[str], Optional[str]]:
    """Resolve a run-scoped Snowflake dataset selector to include tables.

    Tries semantic-view DDL parsing first, then falls back to table-name scope.
    """
    selector = str(dataset_id or "").strip()
    if not selector:
        return None, None, None

    try:
        from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter

        ddl = extractor.extract_semantic_view_ddl(selector)
        converter = SemanticViewToOSIConverter()
        table_map = converter._parse_tables_clause(ddl)
        include_tables = sorted(
            {
                v.get("table_name", "").upper()
                for v in table_map.values()
                if v.get("table_name")
            }
        )
        logger.info(
            "Resolved Snowflake model scope from semantic view '%s': %s base table(s)",
            selector,
            len(include_tables),
        )
        return include_tables or None, selector, ddl
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Dataset selector '%s' not resolved as semantic view (%s); using table-name scope fallback",
            selector,
            exc,
        )

    return [selector.upper()], None, None

def _resolve_snowflake_include_tables(self, context: RunContext) -> tuple[Optional[list[str]], Optional[str]]:
    """Resolve include-tables from env/settings first, then project YAML/UI config.

    Priority order:
    1) MODEL_INCLUDE_TABLES / settings.model.include_tables
    2) source.include_tables
    3) source.tables
    4) source.models (UI-selected tables/models)
    5) selection.model_ids
    """
    include_from_model = context.config.model.included_table_list
    if include_from_model:
        return include_from_model, "model.include_tables"

    config_path = get_project_file_path("semabridge.yaml")
    if not config_path.exists():
        return None, None

    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not parse semabridge.yaml for include tables: %s", exc)
        return None, None

    source_cfg = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
    if str(source_cfg.get("type") or "").strip().lower() != "snowflake":
        return None, None

    def _normalize_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw = [v.strip() for v in value.split(",") if v and v.strip()]
        elif isinstance(value, list):
            raw = [str(v).strip() for v in value if str(v).strip()]
        else:
            raw = []
        seen: set[str] = set()
        normalized: list[str] = []
        for item in raw:
            key = item.upper()
            if key not in seen:
                seen.add(key)
                normalized.append(key)
        return normalized

    candidates: list[tuple[str, Any]] = [
        ("source.include_tables", source_cfg.get("include_tables")),
        ("source.tables", source_cfg.get("tables")),
        ("source.models", source_cfg.get("models")),
        ("selection.model_ids", (cfg.get("selection") or {}).get("model_ids") if isinstance(cfg.get("selection"), dict) else None),
    ]

    for source_name, source_value in candidates:
        include_tables = _normalize_list(source_value)
        if include_tables:
            return include_tables, source_name

    return None, None

def _resolve_snowflake_parallelism(self, context: RunContext) -> tuple[bool, int]:
    """Resolve Snowflake extraction parallel settings with env overrides.

    Environment overrides (optional):
    - SNOWFLAKE_EXTRACT_PARALLEL=true|false
    - SNOWFLAKE_EXTRACT_MAX_WORKERS=<int>
    """
    cpu_count = os.cpu_count() or 4
    default_workers = max(2, min(16, cpu_count))

    configured_workers = context.config.concurrency.max_workers
    max_workers = configured_workers if configured_workers > 0 else default_workers

    env_workers = os.getenv("SNOWFLAKE_EXTRACT_MAX_WORKERS", "").strip()
    if env_workers.isdigit():
        max_workers = int(env_workers)

    max_workers = max(1, min(max_workers, 32))

    env_parallel = os.getenv("SNOWFLAKE_EXTRACT_PARALLEL", "").strip().lower()
    if env_parallel in {"0", "false", "no", "off"}:
        parallel_enabled = False
    elif env_parallel in {"1", "true", "yes", "on"}:
        parallel_enabled = True
    else:
        # Default to parallel extraction for Snowflake to reduce long-running sync times.
        parallel_enabled = True

    return parallel_enabled, max_workers
