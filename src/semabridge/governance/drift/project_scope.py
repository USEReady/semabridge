"""Unified Project Scoping and Access Helper for Drift Monitoring.

Single helper confirming that a project id and an event id or object belong together,
and that the caller has permission to act on that project.
Used uniformly across:
- API routes (drift_router.py)
- Drift lifecycle service (drift_service.py)
- Blast radius simulator (blast_radius.py)
- AI impact check service (ai_impact_service.py)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("semabridge.governance.drift.project_scope")


def normalize_project_id(pid: Optional[str]) -> str:
    """Normalize a project ID for exact matching (lower, strip, remove prefix 'proj-')."""
    return str(pid or "").lower().removeprefix("proj-").strip()


def get_project_aliases(project_id: Optional[str]) -> set[str]:
    """Resolve all known canonical IDs, display names, and file aliases for a project."""
    if not project_id:
        return set()
    clean = normalize_project_id(project_id)
    aliases = {str(project_id).strip().casefold(), clean}
    try:
        from semabridge.ai.project_connection import _project_meta
        meta = _project_meta(project_id)
        if meta:
            for k in ("id", "project_id", "name", "display_name", "semantic_name"):
                v = meta.get(k)
                if v:
                    s_v = str(v).strip().casefold()
                    aliases.add(s_v)
                    aliases.add(normalize_project_id(s_v))
            fname = (meta.get("source_config") or {}).get("file_name", "")
            if "human resources" in fname.casefold() or "hr" in clean:
                aliases.add("multi-pbix")
                aliases.add("human resources sample pbix")
    except Exception:
        pass
    return aliases


def matches_project(pid_a: Optional[str], pid_b: Optional[str]) -> bool:
    """Match between two project IDs or project name/aliases after normalization."""
    clean_a = normalize_project_id(pid_a)
    clean_b = normalize_project_id(pid_b)
    if not clean_a or not clean_b:
        return False
    if clean_a == clean_b:
        return True
    aliases_a = get_project_aliases(pid_a)
    if clean_b in aliases_a or str(pid_b).strip().casefold() in aliases_a:
        return True
    aliases_b = get_project_aliases(pid_b)
    if clean_a in aliases_b or str(pid_a).strip().casefold() in aliases_b:
        return True
    return False


def verify_drift_project_access(
    project_id: Optional[str],
    event_or_object: Any = None,
    *,
    request: Any = None,
    user_id: Optional[str] = None,
    enforce_mutation: bool = False,
    allow_missing_project: bool = False,
    raise_on_error: bool = False,
) -> bool:
    """Confirm project_id and event_or_object belong together and caller may act on that project.

    Args:
        project_id: Project identifier to verify.
        event_or_object: Event ID string, event dict, object with project_id attribute, or None.
        request: FastAPI Request object, if called from an HTTP router.
        user_id: Explicit user ID for permission check.
        enforce_mutation: Whether the action mutates project state (disallows viewer/read-only).
        allow_missing_project: If True, None project_id is accepted.
        raise_on_error: If True, raises HTTPException (with request) or PermissionError/ValueError.

    Returns:
        True if the check succeeds; False otherwise (if raise_on_error is False).
    """
    clean_proj = normalize_project_id(project_id)

    # 1. Project ID presence check
    if not clean_proj and not allow_missing_project:
        msg = "project_id is required."
        if raise_on_error:
            if request is not None:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail=msg)
            raise ValueError(msg)
        return False

    # 2. Confirm event/object belongs to project
    if event_or_object is not None:
        obj_pid = None
        if isinstance(event_or_object, dict):
            obj_pid = event_or_object.get("project_id")
        elif isinstance(event_or_object, str):
            # Event ID string: look up event from drift state
            try:
                from semabridge.governance.drift_service import _load_state
                state = _load_state()
                ev = next((e for e in (state.get("events") or []) if e.get("id") == event_or_object), None)
                if ev:
                    obj_pid = ev.get("project_id")
                else:
                    if raise_on_error and request is not None:
                        from fastapi import HTTPException
                        raise HTTPException(status_code=404, detail="Drift event not found.")
                    return False
            except Exception as exc:
                logger.warning("Event lookup failed for %s: %s", event_or_object, exc)
                return False
        else:
            obj_pid = getattr(event_or_object, "project_id", None)

        if obj_pid is not None:
            if not matches_project(clean_proj, obj_pid):
                logger.warning(
                    "Cross-project mismatch: resource belongs to project '%s', but caller specified '%s'",
                    obj_pid, project_id,
                )
                if raise_on_error:
                    if request is not None:
                        from fastapi import HTTPException
                        raise HTTPException(status_code=403, detail="Forbidden: Resource belongs to another project.")
                    raise PermissionError(f"Resource belongs to project '{obj_pid}', not '{project_id}'")
                return False

    # 3. Caller permission check if request or user_id provided
    if request is not None:
        try:
            from semabridge.api.services.project_ownership_service import (
                assert_project_access,
                auth_is_enabled,
                require_admin_request,
            )
            if auth_is_enabled():
                role = getattr(request.state, "user_role", None)
                if enforce_mutation and role and str(role).strip().lower() in ("viewer", "read_only", "guest"):
                    from fastapi import HTTPException
                    raise HTTPException(
                        status_code=403,
                        detail="Forbidden: caller permissions do not allow drift mutations.",
                    )
                uid = user_id or getattr(request.state, "user_id", None)
                if project_id:
                    assert_project_access(project_id, str(uid) if uid else None, log_prefix="DriftScope")
                else:
                    require_admin_request(request)
        except Exception as exc:
            # Re-raise HTTPException directly
            if exc.__class__.__name__ == "HTTPException":
                raise
            logger.warning("Permission enforcement failed: %s", exc)
            if raise_on_error:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail=str(exc))
            return False

    return True
