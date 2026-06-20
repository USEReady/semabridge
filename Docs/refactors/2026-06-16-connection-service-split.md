# Refactor: split `connection_domain_service.py` into a modular family

**Date:** 2026-06-16
**Branch:** `chore/api-services-decruft`
**Type:** structural modularization — **pure relocation, no behavior change, no public-surface change**

## Why

`src/semabridge/api/services/connection_domain_service.py` was a ~1.3k-line monolith —
the last connection-family file that never adopted the split pattern the `core_*` and
`project_*` families already use:

- `*_shared.py` — singletons + bootstrap + shared helpers,
- `*_*_impl.py` — focused implementation modules,
- `*_domain_service.py` — a **thin aggregator** that re-exports the full surface so
  callers' imports never change.

A single 1.3k-line file mixing Fabric MSAL auth, token resolution, workspace discovery,
and connection CRUD is hard to navigate and extend. This change applies the established
pattern to the connection family.

## What changed — module map

| New module | Lines | Contents |
|---|---:|---|
| `connection_shared.py` | ~94 | `.env` load + Windows asyncio policy + `install_websocket_alert_handler()`; singletons `db_manager`, `engine`, `settings`, `scheduler_service`, `version_control_service`, `logger`, `_last_snapshot_hash`; constants `_FABRIC_PUBLIC_CLIENT_ID`, `_FABRIC_SCOPES`; `_extract_bearer_token`. |
| `connection_fabric_auth_impl.py` | ~697 | Owns the shared session/MSAL state (`_fabric_session_token*`, `_msal_app_cache`, `_msal_http_client`). MSAL helpers (`_get_msal_http_client`, `_get_msal_app`, `clear_msal_cache`, `_run_background_msal_poll`); device-code (`fabric_device_code_login`, `fabric_device_code_poll`, `fabric_auth_status`, `fabric_logout`, `_clear_fabric_from_config`); token resolution (`_get_valid_fabric_token`, `_resolve_fabric_access_token`, `_refresh_account_token`, `_try_silent_refresh`). |
| `connection_fabric_workspaces_impl.py` | ~332 | `list_workspaces`, `fabric_list_workspaces`, `debug_token_header`, `fabric_get_default_workspace`, `fabric_select_workspace`, `_sync_workspace_to_config`. |
| `connection_crud_impl.py` | ~361 | `get_connections_status`, `save_connection`, `delete_connection`, `test_connection`, `snowflake_oauth_test`. |
| `connection_domain_service.py` | ~95 | **Thin aggregator** — re-exports everything above and declares `__all__`. |

## Why the boundaries are where they are

- **Shared-state cohesion.** The mutable globals `_fabric_session_token(_expires_at)`,
  `_msal_app_cache`, and `_msal_http_client` are mutated by `clear_msal_cache`,
  `fabric_device_code_poll`, `fabric_logout`, and `_get_msal_http_client`, and read by
  the token-resolution helpers. Keeping **all** of those together in
  `connection_fabric_auth_impl.py` means the `global` statements keep working unchanged —
  no state relocation, no `global`-write rewrites.
- **Acyclic imports (verified):** `auth_impl → shared`; `workspaces_impl → shared,
  auth_impl` (calls `_resolve_fabric_access_token` / `_extract_bearer_token`);
  `crud_impl → shared` + `connectors.factory` only (independent of Fabric auth);
  `domain_service → all`.

## Preserved import surface

`connection_domain_service` is imported by `connections_service`,
`fabric_connection_service`, `connection_api_service`, `core_shared`, and
`auth/token_resolver` (lazy). The aggregator continues to export every previously
importable name (now also enumerated in `__all__`):

- **Public:** `get_connections_status`, `save_connection`, `delete_connection`,
  `test_connection`, `snowflake_oauth_test`, `list_workspaces`, `fabric_list_workspaces`,
  `fabric_get_default_workspace`, `fabric_select_workspace`, `fabric_device_code_login`,
  `fabric_device_code_poll`, `fabric_auth_status`, `fabric_logout`, `debug_token_header`.
- **Private:** `_extract_bearer_token`, `_get_msal_app`, `_resolve_fabric_access_token`,
  `_try_silent_refresh`, `_refresh_account_token` (plus the singletons/constants).

The four downstream façades and `core_shared` / `token_resolver` were **not edited**.

## Incidental cleanup

- Dropped `_discovery_cache` / `_DISCOVERY_CACHE_TTL` (defined in the old monolith but
  never referenced and not imported anywhere).

## Test update (required by the split)

`Tests/test_fabric_connection_workspaces_fallback.py` monkeypatched
`connection_domain_service._resolve_fabric_access_token`. After the split,
`fabric_list_workspaces` resolves that name in its **own** module, so the patch must
target `connection_fabric_workspaces_impl._resolve_fabric_access_token` (patch where the
name is looked up). Updated accordingly — the only test change. The two other importers'
tests were unaffected because they patch source modules (e.g. `credential_manager`).

## Verification performed

| Check | Result |
|---|---|
| Aggregator exposes the full public + private surface | ✅ all names resolve |
| `connections_service` / `fabric_connection_service` / `connection_api_service` / `core_shared` / `token_resolver` import | ✅ OK |
| `import semabridge.api.main` (no circular import) | ✅ OK |
| Route inventory | ✅ **182 routes**, unchanged |
| AST unused-import audit (4 impl/shared modules) | ✅ clean (aggregator imports are intentional re-exports, declared in `__all__`) |
| `Tests/test_fabric_connection_workspaces_fallback.py` | ✅ passes after the patch-target fix |

### Pre-existing, unrelated

`Tests/test_connection_status_fallback.py` fails in this environment because it is an
`@pytest.mark.asyncio` test and **`pytest-asyncio` is not installed** ("async def
functions are not natively supported"). This is independent of the refactor — every
async test fails here, and the test's patch target (`credential_manager.CredentialManager`)
is a source module the refactor left untouched.

## Out of scope / follow-ups

- The duplicated singleton/bootstrap pattern across `*_shared` modules (kept to match
  precedent).
- Installing/configuring `pytest-asyncio` so the async connection test can run.
- The broader `api/services/` folder reorg / renames.
