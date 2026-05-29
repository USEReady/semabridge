import logging
import tempfile
import uuid
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)
from fastapi import File, HTTPException, UploadFile

from semabridge.api.services.project_domain_service import (
    _compat_default_project_yaml,
    _compat_ensure_loaded,
    _compat_load_repo_yaml_text,
    _compat_now_iso,
    _compat_project_configs,
    _compat_projects,
    _compat_repo_yaml_path,
    _compat_save_store,
    browse_pbix_files,
    import_pbix,
)


def _compat_set_project_pbix_path(project_id: str, pbix_path: str) -> None:
    """Persist PBIX path in project metadata and cached project YAML."""
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    normalized_pbix_path = str(Path(pbix_path).resolve()).replace("\\", "/")
    project["pbix_file_path"] = normalized_pbix_path
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project

    yaml_text = (
        _compat_project_configs.get(project_id)
        or _compat_load_repo_yaml_text()
        or _compat_default_project_yaml(project)
    )
    parsed = yaml.safe_load(yaml_text) if yaml_text else {}
    if not isinstance(parsed, dict):
        parsed = {}

    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    source_cfg["type"] = "pbix"
    source_cfg["pbix_path"] = normalized_pbix_path
    source_cfg["pbix_file_path"] = normalized_pbix_path
    parsed["source"] = source_cfg

    rebuilt = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)
    _compat_project_configs[project_id] = rebuilt
    try:
        _compat_repo_yaml_path().write_text(rebuilt, encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write PBIX config to repo YAML: %s", exc)
    _compat_save_store()


def _save_uploaded_pbix_file(upload: UploadFile, target_dir: Path) -> Path:
    """Store an uploaded PBIX file in the target directory."""
    filename = str(upload.filename or "").strip()
    if not filename.lower().endswith(".pbix"):
        raise HTTPException(status_code=400, detail="Only .pbix files are supported")

    safe_name = Path(filename).name
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"{uuid.uuid4().hex}_{safe_name}"

    content = upload.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    destination.write_bytes(content)
    return destination.resolve()


async def upload_pbix_temp(file: UploadFile = File(...)):
    temp_root = Path(tempfile.gettempdir()) / "semabridge" / "uploads"
    saved = _save_uploaded_pbix_file(file, temp_root)
    return {"path": str(saved).replace("\\", "/")}


async def upload_project_pbix(project_id: str, file: UploadFile = File(...)):
    project_root = Path(tempfile.gettempdir()) / "semabridge" / "projects" / project_id
    saved = _save_uploaded_pbix_file(file, project_root)
    _compat_set_project_pbix_path(project_id, str(saved))
    return {
        "project_id": project_id,
        "path": str(saved).replace("\\", "/"),
    }
