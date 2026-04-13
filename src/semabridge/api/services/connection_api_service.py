"""Compatibility exports for the legacy connection API surface.

New route/controller code should import the smaller connection services
directly. This module remains as a bridge for legacy imports and shared
startup hooks.
"""

from semabridge.api.services.connection_domain_service import (
    _extract_bearer_token,
    _get_msal_app,
    _resolve_fabric_access_token,
)
from semabridge.api.services.connection_session_store import (
    _last_poll_time,
    _poll_sessions,
    _poll_sessions_lock,
)
from semabridge.api.services.connections_service import (
    delete_connection,
    get_connections_status,
    list_workspaces,
    save_connection,
    snowflake_oauth_test,
    test_connection,
)
from semabridge.api.services.databricks_connection_service import (
    databricks_auth_status,
    databricks_device_code_poll,
    databricks_logout,
    databricks_native_oauth_login,
    databricks_oauth_callback,
)
from semabridge.api.services.fabric_connection_service import (
    debug_token_header,
    fabric_auth_status,
    fabric_device_code_login,
    fabric_device_code_poll,
    fabric_get_default_workspace,
    fabric_list_workspaces,
    fabric_logout,
    fabric_select_workspace,
)
