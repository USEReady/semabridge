import logging
import os
import uuid
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)
from fastapi import File, UploadFile

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
from semabridge.domain.exceptions import NotFoundError, ValidationError
from semabridge.utils.upload_storage import upload_storage_root as _upload_storage_root
from semabridge.api.services.project_shared import (
    _compat_load_project_yaml_text,
    _compat_save_project_yaml_text,
)

def _compat_set_project_pbix_path(project_id: str, pbix_path: str) -> None:
    """Persist PBIX path in project metadata and cached project YAML."""
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        raise NotFoundError("Project not found")

    normalized_pbix_path = str(Path(pbix_path).resolve()).replace("\\", "/")
    project["pbix_file_path"] = normalized_pbix_path
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project

    from semabridge.api.services.project_config_store import get_project_config_text, save_project_config

    yaml_text = (
        get_project_config_text(project_id)
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
    # The single config writer (DB version + cache + YAML export). Never the
    # shared repo YAML: it is the fallback config for other projects.
    save_project_config(project_id, rebuilt, reason="upload")
    _compat_save_store()
    from semabridge.api.services.project_shared import _compat_raise_if_store_failed
    _compat_raise_if_store_failed()

_UPLOAD_CHUNK_BYTES = 1024 * 1024
_DEFAULT_MAX_UPLOAD_MB = 512


def _max_upload_bytes() -> int:
    raw = os.environ.get("SEMABRIDGE_MAX_UPLOAD_MB", "").strip()
    try:
        mb = int(raw) if raw else _DEFAULT_MAX_UPLOAD_MB
    except ValueError:
        mb = _DEFAULT_MAX_UPLOAD_MB
    return max(1, mb) * 1024 * 1024


def _save_uploaded_pbix_file(upload: UploadFile, target_dir: Path) -> Path:
    """Store an uploaded PBIX or Tableau file in the target directory."""
    filename = str(upload.filename or "").strip()
    allowed_exts = (".pbix", ".tds", ".tdsx", ".twb", ".twbx")
    if not any(filename.lower().endswith(ext) for ext in allowed_exts):
        raise ValidationError(f"Only {', '.join(allowed_exts)} files are supported")

    safe_name = Path(filename).name
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"{uuid.uuid4().hex}_{safe_name}"

    # Stream to disk in chunks (never hold the whole file in memory) and
    # enforce SEMABRIDGE_MAX_UPLOAD_MB (default 512).
    max_bytes = _max_upload_bytes()
    written = 0
    try:
        with destination.open("wb") as out:
            while True:
                chunk = upload.file.read(_UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValidationError(
                        f"Uploaded file exceeds the {max_bytes // (1024 * 1024)} MB limit "
                        "(set SEMABRIDGE_MAX_UPLOAD_MB to change it)"
                    )
                out.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if written == 0:
        destination.unlink(missing_ok=True)
        raise ValidationError("Uploaded file is empty")
    return destination.resolve()

async def upload_pbix_temp(file: UploadFile = File(...)):
    # Persistent, not the OS temp dir: projects sync from these paths later
    # (see semabridge.utils.upload_storage).
    temp_root = _upload_storage_root() / "files"
    saved = _save_uploaded_pbix_file(file, temp_root)
    return {"path": str(saved).replace("\\", "/")}

async def upload_project_pbix(project_id: str, file: UploadFile = File(...)):
    pid = str(project_id or "").strip()
    if not pid or pid in {".", ".."} or Path(pid).name != pid or "\\" in pid:
        raise ValidationError("Invalid project id")
    project_root = _upload_storage_root() / "projects" / pid
    saved = _save_uploaded_pbix_file(file, project_root)
    _compat_set_project_pbix_path(project_id, str(saved))
    return {
        "project_id": project_id,
        "path": str(saved).replace("\\", "/"),
    }
