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
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def resolve_source_pbix_path(path_or_name: Any) -> Optional[str]:
    """Resolve a PBIX path or model name to an existing file on disk.

    Handles:
    1. Existing files directly.
    2. Temp files that were already persisted to upload_storage_root() / "files".
    3. Missing temp files that exist under a persistent root (matching exact name or clean stem).
    4. Model names / stems matching persistent uploads or Examples.
    """
    text = str(path_or_name or "").strip()
    if not text:
        return None
    if len(text) >= 2 and ((text[0] == '"' and text[-1] == '"') or (text[0] == "'" and text[-1] == "'")):
        text = text[1:-1].strip()
    text = os.path.expandvars(os.path.expanduser(text))

    p = Path(text)
    if p.is_file():
        return str(p.resolve()).replace("\\", "/")

    dest_dir = upload_storage_root() / "files"
    if (dest_dir / p.name).is_file():
        return str((dest_dir / p.name).resolve()).replace("\\", "/")

    stem = p.stem
    clean_stem = re.sub(r"^[0-9a-fA-F]{32}_", "", stem).strip()

    search_roots = [
        upload_storage_root() / "files",
        upload_storage_root() / "projects",
        upload_storage_root(),
        Path(__file__).resolve().parents[3] / "output" / "uploads",
        Path.cwd() / "output" / "uploads",
        Path.cwd() / "Examples",
    ]

    candidates: List[Path] = []
    seen: set = set()

    for root in search_roots:
        if not root.is_dir():
            continue
        if p.name:
            try:
                for c in root.rglob(p.name):
                    if c.is_file() and str(c.resolve()) not in seen:
                        seen.add(str(c.resolve()))
                        candidates.append(c)
            except OSError:
                pass

        if clean_stem:
            patterns = [f"*_{clean_stem}.pbix", f"{clean_stem}.pbix", f"*{clean_stem}*.pbix"]
            for pat in patterns:
                try:
                    for c in root.rglob(pat):
                        if c.is_file() and str(c.resolve()) not in seen:
                            seen.add(str(c.resolve()))
                            candidates.append(c)
                except OSError:
                    pass

    if candidates:
        candidates.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return str(candidates[0].resolve()).replace("\\", "/")

    return None


def persist_temp_upload(path_value: Any) -> Optional[str]:
    """If ``path_value`` is an existing upload in the OS temp dir, copy it to
    persistent storage (same file name, so the same upload id) and return
    the new path. If the temp file no longer exists, attempt to resolve it
    from persistent storage. Returns None when there is nothing to do."""
    text = str(path_value or "").strip()
    if not text:
        return None
    src = Path(text).expanduser()
    dest_dir = upload_storage_root() / "files"
    dest = dest_dir / src.name

    if _is_under(src, legacy_temp_upload_root()):
        if src.is_file():
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                    shutil.copy2(src, dest)
                return str(dest.resolve()).replace("\\", "/")
            except OSError as exc:
                logger.warning("Could not move upload %s to persistent storage: %s", src, exc)
                return None
        if dest.is_file():
            return str(dest.resolve()).replace("\\", "/")
        resolved = resolve_source_pbix_path(src)
        if resolved:
            return resolved
        return None

    if not src.is_file() and (src.suffix.lower() == ".pbix" or ".pbix" in text.lower()):
        resolved = resolve_source_pbix_path(src)
        if resolved:
            return resolved

    return None


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
