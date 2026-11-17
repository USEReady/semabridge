"""Fabric credentials for the legacy /sync/jobs path (sync/orchestrator.py).

The legacy orchestrator used ``FabricExtractor(get_settings().fabric)`` --
the server-wide FABRIC_* environment (tenant / client secret / workspace) --
for every caller. This builds the extractor from the JOB's own workspace and
identity through ``resolve_fabric_credentials`` instead:

* the workspace must be given (``fabric_workspace_id``); no default;
* the identity must resolve to a per-user / per-account token (origin
  ``identity`` or ``account``). The shared interactive login and the
  server-wide service principal are refused with a reason;
* FabricConfig is built with explicit empty credentials so no FABRIC_* value
  from the environment is picked up.
"""

from __future__ import annotations

from typing import Any, Optional

from semabridge.sources.base import SourceAuthError, SourceConfigError

_PER_CALLER_ORIGINS = ("identity", "account")


def fabric_extractor_for_sync(workspace_id: Optional[str], identity_id: Optional[str], *, account_token: Optional[str] = None) -> Any:
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.core.settings import FabricConfig
    from semabridge.sources.fabric.credentials import apply_to_extractor, resolve_fabric_credentials

    ws = str(workspace_id or "").strip()
    if not ws:
        raise SourceConfigError("Legacy /sync/jobs: fabric_workspace_id is required (no server default workspace).")
    creds = resolve_fabric_credentials(identity_id=identity_id, account_token=account_token)
    if creds.origin not in _PER_CALLER_ORIGINS or not creds.access_token:
        raise SourceAuthError(
            "Legacy /sync/jobs: a Fabric identity is required (fabric_identity_id); the server-wide "
            "FABRIC_* credentials and the shared interactive login are not used for this path."
        )
    cfg = FabricConfig(workspace_id=ws, tenant_id=None, client_id=None, client_secret=None)
    return apply_to_extractor(FabricExtractor(cfg, access_token=creds.access_token), creds)


def fabric_publisher_for_sync(workspace_id: Optional[str], identity_id: Optional[str], *, account_token: Optional[str] = None) -> Any:
    """FabricPublisher for the legacy Snowflake -> Fabric publish step, same rules as above."""
    from semabridge.connectors.fabric_publisher import FabricPublisher
    from semabridge.core.settings import FabricConfig
    from semabridge.sources.fabric.credentials import resolve_fabric_credentials

    ws = str(workspace_id or "").strip()
    if not ws:
        raise SourceConfigError("Legacy /sync/jobs: fabric_workspace_id is required to publish to Fabric (no server default).")
    creds = resolve_fabric_credentials(identity_id=identity_id, account_token=account_token)
    if creds.origin not in _PER_CALLER_ORIGINS or not creds.access_token:
        raise SourceAuthError(
            "Legacy /sync/jobs: a Fabric identity is required (fabric_identity_id) to publish; the "
            "server-wide FABRIC_* credentials are not used for this path."
        )
    publisher = FabricPublisher(FabricConfig(workspace_id=ws, tenant_id=None, client_id=None, client_secret=None))
    publisher._access_token = creds.access_token
    publisher._token_expiry = creds.expires_at or 0
    return publisher
