"""Fabric workspace discovery and selection.

Lists Fabric workspaces, resolves a default workspace, persists the selected
workspace to credentials + config, and a token-header debug helper. Depends on
``_resolve_fabric_access_token`` (auth module) and ``_extract_bearer_token`` (shared).
Re-exported through ``connection_domain_service``.
"""
from typing import Dict, Optional
import asyncio
import os
from fastapi import Depends, Query
from sqlalchemy.orm import Session

from semabridge.api.deps import get_db
from semabridge.domain.exceptions import (
    AuthenticationError,
    ExternalServiceError,
    InternalError,
    SemaBridgeError,
    ValidationError,
)
from semabridge.api.services.connection_shared import logger, settings, _extract_bearer_token
from semabridge.api.services.connection_fabric_auth_impl import _resolve_fabric_access_token


async def list_workspaces(
    db: "Session" = Depends(get_db),
    identity_id: Optional[str] = Query(None),
):
    """List available Fabric workspaces for the selected Fabric account."""
    import httpx

    access_token: str | None = None
    if not identity_id:
        raise ValidationError("account_id is required")

    try:
        import anyio
        access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, None, identity_id)
        logger.info("list_workspaces: using account '%s'", identity_id)
    except SemaBridgeError:
        raise
    except Exception as exc:
        logger.exception("list_workspaces: account-scoped lookup failed for %s: %s", identity_id, exc)
        raise ExternalServiceError("Workspace discovery temporarily unavailable")

    if not access_token:
        logger.warning("list_workspaces: no valid default account token — returning 401")
        raise AuthenticationError("token_missing")

    # --- Call Fabric API with the live token ---
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                "https://api.fabric.microsoft.com/v1/workspaces",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            return [
                {"id": ws.get("id", ""), "name": ws.get("displayName", "")}
                for ws in resp.json().get("value", [])
                if ws.get("id")
            ]
        logger.warning(
            "list_workspaces: Fabric API returned %s: %s",
            resp.status_code, resp.text[:200],
        )
    except Exception as exc:
        logger.warning("list_workspaces: Fabric API call failed: %s", exc)

    return []


async def fabric_list_workspaces(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
    connection_id: Optional[str] = Query(None, alias="connectionId"),
):
    """Discover all Fabric workspaces for the selected Fabric account."""
    import httpx

    import anyio
    resolved_identity_id = (identity_id or connection_id or "").strip() or None
    if not resolved_identity_id and not bearer_token:
        # No credentials provided — return empty list instead of 400.
        # Callers that probe this endpoint without auth (e.g. Explore tab workspace name
        # resolution) should receive an empty list, not an error.
        return {"workspaces": []}
    access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, bearer_token, resolved_identity_id)
    logger.info("Calling Fabric workspaces API with resolved access token (Identity: %s)", resolved_identity_id)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.fabric.microsoft.com/v1/workspaces",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 401:
            logger.error(f"Microsoft Fabric API rejected the token with 401: {resp.text}")
            raise AuthenticationError(str({"status": "expired", "message": "Token rejected by Microsoft. Please sign in again."}))

        if resp.status_code != 200:
            raise InternalError(f"Fabric API error: {resp.text}")

        data = resp.json()
        workspaces = data.get("value", [])

        result = [
            {
                "id": ws.get("id", ""),
                "name": ws.get("displayName", "Unknown"),
                "displayName": ws.get("displayName", "Unknown"),
                "type": ws.get("type", ""),
                "capacityId": ws.get("capacityId", ""),
            }
            for ws in workspaces
        ]

        logger.info(f"Discovered {len(result)} Fabric workspaces")
        return {"workspaces": result}

    except httpx.RequestError as exc:
        logger.warning("fabric_list_workspaces: Fabric API request failed, returning empty workspace list: %s", exc)
        return {"workspaces": []}


async def debug_token_header(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
):
    """Debug helper for validating Authorization header wiring in development."""
    if not bearer_token:
        return {
            "received_authorization_header": False,
            "token_present": False,
            "message": "No Authorization header provided",
        }

    return {
        "received_authorization_header": True,
        "token_present": True,
        "token_preview": f"{bearer_token[:8]}...",
        "message": "Bearer token parsed successfully",
    }


async def fabric_get_default_workspace(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
):
    """Return the primary workspace for the selected Fabric account."""
    import httpx

    import anyio
    if not identity_id:
        raise ValidationError("account_id is required")
    access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, bearer_token, identity_id)

    if not access_token:
        return {
            "workspace_id": "",
            "workspace_name": "",
            "configured": False,
            "source": "none",
            "all_workspaces": [],
        }

    # --- Step 2: Dynamic Fetching ---
    _PROJECT_NAME = "semabridge"
    normalized = []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.fabric.microsoft.com/v1/workspaces",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 200:
            raw_workspaces = resp.json().get("value", [])
            normalized = [
                {
                    "id": ws.get("id", ""),
                    "name": ws.get("displayName", ""),
                    "type": ws.get("type", ""),
                }
                for ws in raw_workspaces
                if ws.get("id")
            ]
    except Exception as exc:
        logger.debug("Fabric API workspace auto-detect failed: %s", exc)

    # --- Step 3 & 4: UI Population and Default Selection ---
    if normalized:
        # Priority a: exact name match with project name (case-insensitive)
        matched = next(
            (ws for ws in normalized if ws["name"].lower() == _PROJECT_NAME.lower()),
            None,
        )
        # Priority b: first non-Personal workspace
        if matched is None:
            matched = next(
                (ws for ws in normalized if ws.get("type", "").lower() != "personal" and ws["name"] != "My workspace"),
                None,
            )
        # Priority c: any workspace
        if matched is None:
            matched = normalized[0]

        logger.info(
            "Auto-detected Fabric workspace: %s (%s)",
            matched["name"],
            matched["id"],
        )
        return {
            "workspace_id": matched["id"],
            "workspace_name": matched["name"],
            "configured": True,
            "source": "auto",
            "all_workspaces": normalized,
        }

    return {
        "workspace_id": "",
        "workspace_name": "",
        "configured": False,
        "source": "none",
        "all_workspaces": [],
    }



async def fabric_select_workspace(payload: Dict[str, str]):
    """Save selected workspace and sync it to the config YAML file.

    Args:
        payload: Dict with workspace_id and optionally workspace_name.
    """
    from semabridge.repository.credential_manager import CredentialManager

    workspace_id = payload.get("workspace_id", "").strip()
    workspace_name = payload.get("workspace_name", "").strip()

    if not workspace_id:
        raise ValidationError("workspace_id is required")

    # Save to DuckDB credentials (single source of truth)
    cm = CredentialManager()
    cm.save_credentials("fabric", {
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
    })

    # Sync to config.yaml for CLI/offline compatibility
    await _sync_workspace_to_config(workspace_id, workspace_name)

    # Inject into live process environment so FabricConfig picks it up
    # without requiring a server restart. This is safe because env vars
    # are process-scoped and never persisted to disk.
    os.environ["FABRIC_WORKSPACE_ID"] = workspace_id

    # Clear the cached Settings singleton so the next FabricConfig()
    # instantiation reads the freshly injected env var.
    from semabridge.core.settings import get_settings
    get_settings.cache_clear()

    logger.info(f"Set active Fabric workspace: {workspace_name} ({workspace_id})")
    return {
        "status": "saved",
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
    }


async def _sync_workspace_to_config(workspace_id: str, workspace_name: str) -> None:
    """Update the local config.yaml with the selected Fabric workspace.

    Uses PyYAML to modify and rewrite the file, preserving existing content.
    """
    import yaml
    from pathlib import Path

    # Locate config file â€” check common paths
    candidates = [
        Path.cwd() / "config" / "config.yaml",
        Path.cwd() / "config" / "semabridge.yaml",
        Path.cwd() / "config.yaml",
        Path.cwd() / "semabridge.yaml",
        Path.cwd().parent / "config" / "config.yaml",
        Path.cwd().parent / "config" / "semabridge.yaml",
        Path.cwd().parent / "config.yaml",
        Path.cwd().parent / "semabridge.yaml",
    ]

    # Also check the config_path stored in the current config
    if hasattr(settings, "config_path") and settings.config_path:
        candidates.insert(0, Path(settings.config_path))

    config_path = None
    for candidate in candidates:
        if candidate.exists():
            config_path = candidate
            break

    if not config_path:
        logger.warning("No config.yaml found to sync workspace_id")
        return

    try:
        def _read_config() -> dict:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

        config_data = await asyncio.to_thread(_read_config)

        # Ensure the fabric section exists
        if "fabric" not in config_data:
            config_data["fabric"] = {}

        config_data["fabric"]["workspace_id"] = workspace_id
        if workspace_name:
            config_data["fabric"]["workspace_name"] = workspace_name

        def _write_config() -> None:
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

        await asyncio.to_thread(_write_config)

        logger.info(f"Config file updated: {config_path}")
    except Exception as exc:
        logger.error(f"Failed to sync workspace to config: {exc}")
