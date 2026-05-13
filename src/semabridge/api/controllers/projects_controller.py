import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import List

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
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
router.get('/api/projects')(list_projects_compat)
router.get('/api/projects/discovery')(list_project_discovery_compat)
router.post('/api/projects')(create_project_compat)
router.get('/api/projects/{project_id}')(get_project_compat)
router.patch('/api/projects/{project_id}')(patch_project_compat)
router.delete('/api/projects/{project_id}')(delete_project_compat)
router.get('/api/projects/{project_id}/config')(get_project_config_compat)
router.put('/api/projects/{project_id}/config')(save_project_config_compat)
router.get('/api/projects/{project_id}/snapshots')(list_project_snapshots_compat)
@router.delete('/api/projects/{project_id}/snapshots')
async def delete_snapshots(project_id: str, snapshot_ids: List[str]):
    return await delete_project_snapshots_compat(project_id, snapshot_ids)
router.get('/api/projects/{project_id}/snapshot-groups')(list_snapshot_groups_compat)
router.post('/api/projects/{project_id}/snapshots/capture')(capture_manual_snapshots_compat)
router.get('/api/projects/{project_id}/snapshots/compare')(compare_project_snapshots_compat)
router.post('/api/projects/{project_id}/restore-version')(restore_project_version_compat)
router.get('/api/projects/{project_id}/runs')(get_project_runs_compat)

@router.get('/api/projects/{project_id}/vc/stats')
async def get_project_vc_stats(project_id: str):
    return await get_project_storage_stats(project_id)

@router.post('/api/projects/{project_id}/vc/retention')
async def run_project_retention(project_id: str, days: int = 30):
    return await apply_project_retention_policy(project_id, days)


@router.get('/api/projects/{project_id}/export')
async def export_project(project_id: str):
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
async def export_projects_bulk(project_ids: List[str]):
    buffer = BytesIO()
    exported = []
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for project_id in project_ids:
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
async def import_projects(files: List[UploadFile] = File(...)):
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

            project_payload = {
                'id': project_id,
                'project_id': project_id,
                'name': parsed.get('project_name') or parsed.get('name') or project_id,
                'config_yaml': text,
                'source': parsed.get('source') if isinstance(parsed.get('source'), dict) else {},
                'target': parsed.get('target') if isinstance(parsed.get('target'), dict) else {},
            }
            created = await create_project_compat(project_payload)
            await save_project_config_compat(project_id, {'config_yaml': text})
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
