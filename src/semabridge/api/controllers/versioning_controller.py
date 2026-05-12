from typing import Optional
from fastapi import APIRouter, Query, Body

from semabridge.api.services.versioning_service import (
    compare_model_versions,
    delete_model_versions,
    get_version_snapshot,
    list_model_versions,
    rollback_model_version,
)
from semabridge.api.services.version_control_impl import version_backend
from semabridge.api.services.project_projects_impl import _normalize_model_manifest, _compat_load_project_yaml_text
from fastapi import HTTPException
import yaml

router = APIRouter()
router.get('/api/model-versions')(list_model_versions)
router.get('/api/model-versions/compare')(compare_model_versions)
router.delete('/api/model-versions')(delete_model_versions)
router.get('/api/model-versions/snapshot')(get_version_snapshot)
router.post('/api/model-versions/rollback')(rollback_model_version)

@router.get('/api/version-control/stats')
async def get_vc_stats(project_id: str):
    return await version_backend.get_storage_stats(project_id)

@router.post('/api/version-control/retention')
async def run_retention_policy(
    project_id: str,
    days_to_keep: int = Query(default=30, ge=1),
    min_versions_to_keep: int = Query(default=5, ge=1),
    strategy: str = Query(default="days"),
    max_snapshots: Optional[int] = Query(default=None, ge=1),
    prune_manual: bool = Query(default=False),
):
    """
    Apply retention policy to prune old snapshots.
    
    Args:
        project_id: Project ID.
        days_to_keep: Days to keep (for 'days' strategy).
        min_versions_to_keep: Minimum versions to keep (for 'count' strategy).
        strategy: Policy strategy ('count', 'days', 'unlimited').
        max_snapshots: Max snapshots per connector.
        prune_manual: Whether to prune manual snapshots.
    """
    return await version_backend.apply_retention_policy(
        project_id,
        days_to_keep=days_to_keep,
        min_versions_to_keep=min_versions_to_keep,
        strategy=strategy,
        max_snapshots=max_snapshots,
        prune_manual=prune_manual,
    )

@router.put('/api/version-control/retention-policy')
async def set_retention_policy(
    project_id: str,
    strategy: str = Query(default="unlimited"),
    max_snapshots: Optional[int] = Query(default=None, ge=1),
    max_age_days: Optional[int] = Query(default=None, ge=1),
    prune_manual: bool = Query(default=False),
):
    """
    Set or update retention policy for a project.
    
    Args:
        project_id: Project ID.
        strategy: Policy strategy ('count', 'days', 'unlimited').
        max_snapshots: Max snapshots per connector (for 'count' strategy).
        max_age_days: Max age in days (for 'days' strategy).
        prune_manual: Whether to prune manual snapshots.
    """
    return await version_backend.set_retention_policy(
        project_id,
        strategy=strategy,
        max_snapshots=max_snapshots,
        max_age_days=max_age_days,
        prune_manual=prune_manual,
    )

@router.get('/api/version-control/retention-policy')
async def get_retention_policy(project_id: str):
    """Get retention policy for a project."""
    return await version_backend.get_retention_policy(project_id)


@router.post('/api/contract/preflight')
async def contract_preflight(project_id: str, payload: dict):
    """
    Preflight a proposed model_manifest for a project.

    Expects payload to contain a `model_manifest` dict (the proposed manifest).
    Returns detected breaking/non-breaking changes, unresolved dependency pins,
    and recommended downstream actions. If breaking changes are present and
    `deprecation_date` is not supplied, this endpoint will return 400.
    """
    try:
        proposed = payload.get("model_manifest") if isinstance(payload, dict) else None
        if not isinstance(proposed, dict):
            raise HTTPException(status_code=400, detail="payload.model_manifest must be an object")

        # Normalize proposed manifest (reuses existing validation rules)
        norm_proposed = _normalize_model_manifest(project_id, {"model_manifest": proposed})

        # Load current manifest from repo (if any)
        existing_text = _compat_load_project_yaml_text(project_id)
        if existing_text and existing_text.strip():
            try:
                existing_parsed = yaml.safe_load(existing_text) or {}
            except Exception:
                existing_parsed = {}
        else:
            existing_parsed = {}

        norm_existing = None
        if existing_parsed:
            try:
                norm_existing = _normalize_model_manifest(project_id, existing_parsed)
            except Exception:
                norm_existing = None

        breaking: list[str] = []
        non_breaking: list[str] = []

        # Compare contract versions
        if norm_existing:
            if norm_existing.get("contract_version") != norm_proposed.get("contract_version"):
                breaking.append(
                    f"Contract version change: {norm_existing.get('contract_version')} -> {norm_proposed.get('contract_version')}"
                )

            # Dependencies removed -> breaking
            existing_deps = set(norm_existing.get("dependencies") or [])
            proposed_deps = set(norm_proposed.get("dependencies") or [])
            removed = existing_deps - proposed_deps
            added = proposed_deps - existing_deps
            for r in sorted(removed):
                breaking.append(f"Dependency removed: {r}")
            for a in sorted(added):
                non_breaking.append(f"Dependency added: {a}")

            # Dependency pins changed in incompatible ways
            existing_pins = norm_existing.get("dependency_pins") or {}
            proposed_pins = norm_proposed.get("dependency_pins") or {}
            for dep, pinned in proposed_pins.items():
                prev = existing_pins.get(dep)
                if prev and prev != pinned:
                    # Changing a pinned version is considered breaking for consumers
                    breaking.append(f"Dependency pin changed for {dep}: {prev} -> {pinned}")

        # Require deprecation_date when there are breaking changes
        requires_deprecation = False
        if breaking:
            deprecation = str(norm_proposed.get("deprecation_date") or "").strip()
            if not deprecation:
                requires_deprecation = True

        # Resolve dependency pins against published contract versions of dependency projects
        unresolved_pins: dict = {}
        for dep, pinned in (norm_proposed.get("dependency_pins") or {}).items():
            dep_yaml = _compat_load_project_yaml_text(dep)
            resolved = False
            if dep_yaml and dep_yaml.strip():
                try:
                    parsed = yaml.safe_load(dep_yaml) or {}
                    dep_manifest = _normalize_model_manifest(dep, parsed)
                    published = set(dep_manifest.get("published_contract_versions") or [])
                    if str(pinned) in published:
                        resolved = True
                except Exception:
                    resolved = False

            if not resolved:
                unresolved_pins[dep] = pinned

        downstream_actions: list[str] = []
        if breaking:
            downstream_actions.append("Create a new coexisting contract version with deprecation metadata for the previous version")
            downstream_actions.append("Notify downstream consumers and require pin updates before deprecation window")
        else:
            downstream_actions.append("Safe to publish non-breaking contract update; consider publishing and notifying consumers")

        result = {
            "is_breaking": bool(breaking),
            "breaking_changes": breaking,
            "non_breaking_changes": non_breaking,
            "requires_deprecation_date": requires_deprecation,
            "unresolved_dependency_pins": unresolved_pins,
            "downstream_actions": downstream_actions,
        }

        if requires_deprecation:
            raise HTTPException(status_code=400, detail={"message": "Breaking changes require deprecation_date in model_manifest", "result": result})

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
