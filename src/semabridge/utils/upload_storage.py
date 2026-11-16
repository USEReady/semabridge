"""Where uploaded source files (PBIX / Tableau) live.

Projects sync straight from their uploaded files' paths, so those files must
outlive the session. They used to live in the OS temp dir, which macOS purges
(reboot and periodic cleanup) -- leaving saved projects pointing at deleted
files. Uploads now go to a persistent root, and any project still referencing
a temp-dir upload gets that file copied there when its config is saved.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SOURCE_PATH_KEYS = ("pbix_path", "pbix_file_path", "path", "file_path")


def upload_storage_root() -> Path:
    """Persistent root for uploads. Override with SEMABRIDGE_UPLOAD_DIR;
    defaults to <repo>/output/uploads (git-ignored)."""
    configured = os.environ.get("SEMABRIDGE_UPLOAD_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[3] / "output" / "uploads"


def legacy_temp_upload_root() -> Path:
    """Where uploads used to be stored (OS temp dir)."""
    return Path(tempfile.gettempdir()) / "semabridge"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def persist_temp_upload(path_value: Any) -> Optional[str]:
    """If ``path_value`` is an existing upload in the OS temp dir, copy it to
    persistent storage (same file name, so the same upload id) and return
    the new path. Returns None when there is nothing to do."""
    text = str(path_value or "").strip()
    if not text:
        return None
    src = Path(text).expanduser()
    if not _is_under(src, legacy_temp_upload_root()) or not src.is_file():
        return None
    dest_dir = upload_storage_root() / "files"
    dest = dest_dir / src.name
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
    except OSError as exc:
        logger.warning("Could not move upload %s to persistent storage: %s", src, exc)
        return None
    return str(dest.resolve()).replace("\\", "/")


def persist_source_uploads(parsed: Dict[str, Any]) -> bool:
    """Rewrite a parsed project config so its source files point at
    persistent storage (copying any still in the temp dir). Keeps
    source.model_views keyed by the new paths. Returns True if changed."""
    source = parsed.get("source") if isinstance(parsed.get("source"), dict) else None
    if not source:
        return False
    changed = False
    renamed: Dict[str, str] = {}

    models = source.get("models")
    if isinstance(models, list):
        new_models = []
        for entry in models:
            if isinstance(entry, str):
                moved = persist_temp_upload(entry)
                if moved:
                    renamed[entry] = moved
                    entry = moved
                    changed = True
            elif isinstance(entry, dict):
                for key in _SOURCE_PATH_KEYS:
                    moved = persist_temp_upload(entry.get(key))
                    if moved:
                        renamed[str(entry[key])] = moved
                        entry = {**entry, key: moved}
                        changed = True
            new_models.append(entry)
        source["models"] = new_models

    for key in _SOURCE_PATH_KEYS:
        moved = persist_temp_upload(source.get(key))
        if moved:
            renamed[str(source[key])] = moved
            source[key] = moved
            changed = True

    views = source.get("model_views")
    if renamed and isinstance(views, dict):
        source["model_views"] = {renamed.get(k, k): v for k, v in views.items()}

    if changed:
        logger.info("Moved %d project source file(s) from the temp dir to %s", len(renamed), upload_storage_root())
    return changed
