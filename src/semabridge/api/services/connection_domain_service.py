"""Aggregated connection-domain exports.

The implementation now lives in smaller modules so connection work can be split
across files without changing the public import surface:

- ``connection_shared`` — bootstrap, singletons, constants, ``_extract_bearer_token``;
- ``connection_fabric_auth_impl`` — MSAL device-code auth + Fabric token resolution;
- ``connection_fabric_workspaces_impl`` — Fabric workspace discovery/selection;
- ``connection_crud_impl`` — connection CRUD, status, and connectivity tests.

This module preserves the original import surface (public functions used by the
``connections_service`` / ``fabric_connection_service`` façades and the private helpers
imported by ``core_shared``, ``connection_api_service`` and ``auth.token_resolver``).
It contains no FastAPI app or routing.
"""
from semabridge.api.services.connection_shared import (
    db_manager,
    engine,
    settings,
    scheduler_service,
    version_control_service,
    logger,
    _last_snapshot_hash,
    _extract_bearer_token,
    _FABRIC_PUBLIC_CLIENT_ID,
    _FABRIC_SCOPES,
)
from semabridge.api.services.connection_fabric_auth_impl import (
    _get_msal_http_client,
    _get_msal_app,
    clear_msal_cache,
    _run_background_msal_poll,
    fabric_device_code_login,
    fabric_device_code_poll,
    fabric_auth_status,
    fabric_logout,
    _clear_fabric_from_config,
    _get_valid_fabric_token,
    _resolve_fabric_access_token,
    _refresh_account_token,
    _try_silent_refresh,
)
from semabridge.api.services.connection_fabric_workspaces_impl import (
    list_workspaces,
    fabric_list_workspaces,
    debug_token_header,
    fabric_get_default_workspace,
    fabric_select_workspace,
    _sync_workspace_to_config,
)
from semabridge.api.services.connection_crud_impl import (
    get_connections_status,
    save_connection,
    delete_connection,
    test_connection,
    snowflake_oauth_test,
)

__all__ = [
    # shared singletons / constants / helpers
    "db_manager",
    "engine",
    "settings",
    "scheduler_service",
    "version_control_service",
    "logger",
    "_last_snapshot_hash",
    "_extract_bearer_token",
    "_FABRIC_PUBLIC_CLIENT_ID",
    "_FABRIC_SCOPES",
    # fabric auth + token resolution
    "_get_msal_http_client",
    "_get_msal_app",
    "clear_msal_cache",
    "_run_background_msal_poll",
    "fabric_device_code_login",
    "fabric_device_code_poll",
    "fabric_auth_status",
    "fabric_logout",
    "_clear_fabric_from_config",
    "_get_valid_fabric_token",
    "_resolve_fabric_access_token",
    "_refresh_account_token",
    "_try_silent_refresh",
    # fabric workspaces
    "list_workspaces",
    "fabric_list_workspaces",
    "debug_token_header",
    "fabric_get_default_workspace",
    "fabric_select_workspace",
    "_sync_workspace_to_config",
    # connection CRUD / status / tests
    "get_connections_status",
    "save_connection",
    "delete_connection",
    "test_connection",
    "snowflake_oauth_test",
]
