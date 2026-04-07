"""Compatibility exports for the legacy core API surface.

New code should import from the smaller feature service modules directly.
This module stays as a bridge so existing imports keep working during the
incremental refactor.
"""

from semabridge.api.services.core_domain_service import (
    _discovery_cache,
    _last_snapshot_hash,
    _normalize_yaml_windows_path_fields,
    _resolve_models_path,
    validate_live,
)
from semabridge.api.services.config_service import (
    generate_config,
    get_config,
    get_global_config,
    save_global_config,
    validate_config,
)
from semabridge.api.services.discovery_service import (
    discover_fabric_models,
    discover_fabric_models_by_workspace,
    discover_multi_workspace,
    discover_repository,
    discover_semantic,
    discover_snowflake,
)
from semabridge.api.services.health_service import health_check
from semabridge.api.services.history_service import get_history
from semabridge.api.services.model_service import get_model, save_model
from semabridge.api.services.semantic_service import (
    semantic_refresh,
    semantic_sync,
    sync_models,
)
