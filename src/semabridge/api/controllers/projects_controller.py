import json
import zipfile
import os
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Any

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response

from semabridge.api.services.projects_service import (
    create_project_compat,
    delete_project_compat,
    get_project_compat,
    get_project_config_compat,
    list_projects_compat,
    list_project_discovery_compat,
    patch_project_compat,
    save_project_config_compat,
)
from semabridge.api.services.project_runs_service import (
    capture_manual_snapshots_compat,
    compare_project_snapshots_compat,
    list_project_snapshots_compat,
    list_snapshot_groups_compat,
    restore_project_version_compat,
    get_project_runs_compat,
    delete_project_snapshots_compat,
    apply_project_retention_policy,
    get_project_storage_stats,
)

router = APIRouter()

# ── Helper Ownership Scoping Functions ──────────────────────────────────

def _get_user_id(request: Request) -> str | None:
    if os.environ.get("AUTH_ENABLED", "").lower() == "true":
        return getattr(request.state, "user_id", None)
    return None


def _get_allowed_account_ids(user_id: str | None) -> set[str] | None:
    if not user_id:
        return None
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.models import Account
    from sqlalchemy import select
    with db_manager.get_session() as session:
        ids = session.execute(
            select(Account.id).where(Account.owner_id == int(user_id))
        ).scalars().all()
        return {str(i) for i in ids}


def _is_project_owned(project_id: str, allowed_accounts: set[str] | None) -> bool:
    if allowed_accounts is None:
        return True
    from semabridge.api.services.project_shared import _compat_projects, _compat_ensure_loaded
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        return False
    
    account_id = project.get("account_id")
    if not account_id:
        src = project.get("source")
        if isinstance(src, dict):
            account_id = src.get("identity_id") or src.get("account_id")
    if not account_id:
        tgt = project.get("target")
        if isinstance(tgt, dict):
            account_id = tgt.get("identity_id") or tgt.get("account_id")
            
    return account_id and str(account_id) in allowed_accounts


# ── Project Route Handlers with Server-Side Ownership Enforcement ───────

@router.get('/api/projects')
async def list_projects(request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    
    projects = await list_projects_compat()
    if allowed_accounts is not None:
        filtered = []
        for p in projects:
            pid = p.get("id") or p.get("project_id")
            if pid and _is_project_owned(pid, allowed_accounts):
                filtered.append(p)
        return filtered
    return projects


@router.get('/api/projects/discovery')
async def list_project_discovery(request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    
    entries = await list_project_discovery_compat()
    if allowed_accounts is not None:
        filtered = []
        for e in entries:
            pid = e.get("id") or e.get("project_id")
            if pid and _is_project_owned(pid, allowed_accounts):
                filtered.append(e)
        return filtered
    return entries


@router.post('/api/projects')
async def create_project(request: Request, payload: dict):
    user_id = _get_user_id(request)
    if user_id:
        src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        identity_id = src.get("identity_id") or payload.get("account_id")
        if identity_id:
            from semabridge.repository.orm.session_factory import db_manager
            from semabridge.repository.orm.models import Account
            from sqlalchemy import select
            with db_manager.get_session() as session:
                owner_id = session.execute(
                    select(Account.owner_id).where(Account.id == identity_id)
                ).scalar_one_or_none()
                if owner_id is not None and owner_id != int(user_id):
                    raise HTTPException(status_code=403, detail="Forbidden: connection identity belongs to another user")
    return await create_project_compat(payload)


@router.get('/api/projects/{project_id}')
async def get_project(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await get_project_compat(project_id)


@router.patch('/api/projects/{project_id}')
async def patch_project(project_id: str, payload: dict, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await patch_project_compat(project_id, payload)


@router.delete('/api/projects/{project_id}')
async def delete_project(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await delete_project_compat(project_id)


@router.get('/api/projects/{project_id}/config')
async def get_project_config(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await get_project_config_compat(project_id)


@router.put('/api/projects/{project_id}/config')
async def save_project_config(project_id: str, payload: dict, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await save_project_config_compat(project_id, payload)


@router.get('/api/projects/{project_id}/snapshots')
async def list_project_snapshots(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await list_project_snapshots_compat(project_id)


@router.delete('/api/projects/{project_id}/snapshots')
async def delete_snapshots(project_id: str, snapshot_ids: List[str], request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await delete_project_snapshots_compat(project_id, snapshot_ids)


@router.get('/api/projects/{project_id}/snapshot-groups')
async def list_snapshot_groups(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await list_snapshot_groups_compat(project_id)


@router.post('/api/projects/{project_id}/snapshots/capture')
async def capture_snapshots(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await capture_manual_snapshots_compat(project_id)


@router.get('/api/projects/{project_id}/snapshots/compare')
async def compare_snapshots(project_id: str, base_id: str, target_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await compare_project_snapshots_compat(project_id, base_id, target_id)


@router.post('/api/projects/{project_id}/restore-version')
async def restore_project_version(project_id: str, payload: dict, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await restore_project_version_compat(project_id, payload)


@router.get('/api/projects/{project_id}/runs')
async def get_project_runs(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await get_project_runs_compat(project_id)


@router.get('/api/projects/{project_id}/vc/stats')
async def get_project_vc_stats(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await get_project_storage_stats(project_id)


@router.post('/api/projects/{project_id}/vc/retention')
async def run_project_retention(project_id: str, days: int = 30, request: Request = None):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await apply_project_retention_policy(project_id, days)


@router.get('/api/projects/{project_id}/export')
async def export_project(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
        
    project = await get_project_config_compat(project_id)
    config_yaml = str(project.get('config_yaml') or '').strip()
    if not config_yaml:
        raise HTTPException(status_code=404, detail=f'No exportable config found for project {project_id}')

    filename = f'{project_id}.yaml'
    return Response(
        content=config_yaml.encode('utf-8'),
        media_type='application/x-yaml',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@router.post('/api/projects/export-bulk')
async def export_projects_bulk(project_ids: List[str], request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    
    buffer = BytesIO()
    exported = []
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for project_id in project_ids:
            if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
                continue
            try:
                project = await get_project_config_compat(project_id)
                config_yaml = str(project.get('config_yaml') or '').strip()
                if not config_yaml:
                    continue
                zf.writestr(f'{project_id}.yaml', config_yaml)
                exported.append(project_id)
            except Exception:
                continue

        manifest = yaml.safe_dump({'exported_projects': exported}, sort_keys=False)
        zf.writestr('manifest.yaml', manifest)

    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="semabridge_projects.zip"'},
    )


@router.post('/api/projects/import')
async def import_projects(request: Request, files: List[UploadFile] = File(...)):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    
    imported = []
    errors = []

    for upload in files:
        filename = Path(upload.filename or 'project.yaml').name
        try:
            raw = await upload.read()
            text = raw.decode('utf-8')
            parsed = yaml.safe_load(text)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            if not isinstance(parsed, dict):
                raise ValueError('Project file must contain a mapping/object')

            project_id = str(
                parsed.get('project_id')
                or parsed.get('id')
                or Path(filename).stem
            ).strip()
            if not project_id:
                raise ValueError('Unable to determine project_id')

            # Verify target connection ownership for imported project
            src = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
            identity_id = src.get("identity_id") or parsed.get("account_id")
            if allowed_accounts is not None and identity_id and str(identity_id) not in allowed_accounts:
                raise HTTPException(status_code=403, detail=f"Forbidden: imported connection {identity_id} belongs to another user")

            project_payload = {
                'id': project_id,
                'project_id': project_id,
                'name': parsed.get('project_name') or parsed.get('name') or project_id,
                'config_yaml': text,
                'source': parsed.get('source') if isinstance(parsed.get('source'), dict) else {},
                'target': parsed.get('target') if isinstance(parsed.get('target'), dict) else {},
            }
            created = await create_project_compat(project_payload)
            imported.append({
                'filename': filename,
                'project_id': created.get('project_id') or project_id,
                'name': created.get('name') or project_id,
            })
        except Exception as exc:
            errors.append({'filename': filename, 'error': str(exc)})

    status_code = 207 if errors and imported else 400 if errors and not imported else 200
    return JSONResponse(
        status_code=status_code,
        content={'imported': imported, 'errors': errors},
    )
