"""Strict Fabric workspace resolution (no alias fallback).

A workspace value is either a GUID (used as-is) or a workspace NAME, which is
resolved by exact display-name match among the workspaces the project's
credentials can see. Nothing else is tried: no switch to the stored
credential's workspace or the .env workspace, no case-folding, no partial
match. Not found / ambiguous -> SourceNotFoundError / SourceConfigError naming
the value.
"""

from __future__ import annotations

import re
from typing import Any

from semabridge.sources.base import SourceConfigError, SourceNotFoundError

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def is_workspace_guid(value: Any) -> bool:
    return bool(_GUID_RE.match(str(value or "").strip()))


def resolve_workspace_strict(extractor: Any, value: Any) -> str:
    """GUID for ``value`` using ``extractor`` (already carrying the project's credentials)."""
    ws = str(value or "").strip()
    if not ws:
        raise SourceConfigError("No Fabric workspace given: set source.workspace_id (no default workspace is used).")
    if is_workspace_guid(ws):
        return ws
    matches = [w for w in (extractor.list_workspaces() or []) if str(w.get("displayName") or "") == ws]
    if not matches:
        raise SourceNotFoundError(
            f"Fabric workspace '{ws}' is not a workspace id and no workspace with exactly that name is visible "
            f"to this project's credentials."
        )
    if len(matches) > 1:
        ids = ", ".join(str(w.get("id")) for w in matches)
        raise SourceConfigError(f"Fabric workspace name '{ws}' matches {len(matches)} workspaces ({ids}); use the workspace id.")
    guid = str(matches[0].get("id") or "")
    if not is_workspace_guid(guid):
        raise SourceConfigError(f"Fabric workspace '{ws}' was listed without a valid id ({guid!r}).")
    return guid
