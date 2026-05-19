"""Aggregated core-domain exports.

The original monolithic core API service has been split into smaller feature
modules so config/discovery/sync work can evolve in parallel without editing
one giant file. This module preserves the old import surface.
"""

from semabridge.api.services.core_shared import (
    _discovery_cache,
    _last_snapshot_hash,
    _normalize_yaml_windows_path_fields,
    _resolve_models_path,
    db_manager,
    engine,
    logger,
    scheduler_service,
    settings,
    version_control_service,
)
from semabridge.api.services.core_health_impl import health_check
from semabridge.api.services.core_discovery_impl import (
    discover_fabric_models,
    discover_fabric_models_by_workspace,
    discover_multi_workspace,
    discover_repository,
    discover_snowflake,
    discover_snowflake_databases,
    discover_snowflake_schemas,
    discover_snowflake_warehouses,
)
from semabridge.api.services.core_semantic_impl import (
    discover_semantic,
    semantic_refresh,
    semantic_sync,
)
from semabridge.api.services.core_models_impl import get_model, save_model
from semabridge.api.services.core_config_impl import (
    _check_inline_secrets,
    _global_config_path,
    generate_config,
    get_config,
    get_global_config,
    get_history,
    save_global_config,
    validate_config,
    validate_live,
)
from semabridge.api.services.core_sync_impl import sync_models
