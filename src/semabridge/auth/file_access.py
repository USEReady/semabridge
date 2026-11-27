"""Allow-list for user-supplied local file paths (PBIX import / browse / sync).

API callers may only point the server at files under an allowed root:

- the persistent upload root (``semabridge.utils.upload_storage.upload_storage_root()``)
  and the legacy OS-temp upload root (older projects still reference it);
- active configured local folders (``local_folders`` table);
- the configured local models path (``SEMABRIDGE_LOCAL_MODELS_PATH`` /
  semabridge config, see ``project_shared._resolve_models_path``);
- the repository ``Examples/`` directory;
- any extra roots in ``SEMABRIDGE_ALLOWED_SOURCE_ROOTS`` (``os.pathsep``-separated).

Paths are resolved (symlinks followed, ``..`` collapsed) *before* the
containment check, so neither traversal nor a symlink pointing outside a root
escapes the allow-list.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, List, Optional

from semabridge.domain.exceptions import PermissionError, ValidationError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _safe_resolve(path: Path) -> Optional[Path]:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def _local_folder_roots() -> List[Path]:
    try:
        from sqlalchemy import select

        from semabridge.repository.orm.models import LocalFolder
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
            rows = session.execute(
                select(LocalFolder.absolute_path).where(LocalFolder.is_active.is_(True))
            ).all()
        return [Path(str(row[0])) for row in rows if row[0]]
    except Exception as exc:  # noqa: BLE001 - DB unavailable must not widen access
        logger.debug("Could not load local_folders for path allow-list: %s", exc)
        return []


def _models_path_root() -> List[Path]:
    try:
        from semabridge.api.services.project_shared import _resolve_models_path

        return [_resolve_models_path()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not resolve models path for path allow-list: %s", exc)
        return []


def allowed_source_roots() -> List[Path]:
    """Resolved, de-duplicated list of directories user paths may live under."""
    from semabridge.utils.upload_storage import legacy_temp_upload_root, upload_storage_root

    candidates: List[Path] = [
        upload_storage_root(),
        legacy_temp_upload_root(),
        _REPO_ROOT / "Examples",
    ]
    candidates.extend(_models_path_root())
    candidates.extend(_local_folder_roots())
    extra = os.environ.get("SEMABRIDGE_ALLOWED_SOURCE_ROOTS", "")
    candidates.extend(Path(p) for p in extra.split(os.pathsep) if p.strip())

    roots: List[Path] = []
    for candidate in candidates:
        resolved = _safe_resolve(Path(candidate))
        if resolved is not None and resolved not in roots:
            roots.append(resolved)
    return roots


def _is_within(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def resolve_allowed_path(
    raw_path: str,
    *,
    expect: str = "file",
    allowed_suffixes: Optional[Iterable[str]] = None,
    roots: Optional[List[Path]] = None,
) -> Path:
    """Resolve ``raw_path`` and ensure it lies under an allowed root.

    Args:
        raw_path: User-supplied path (absolute, or relative to the server CWD).
        expect: ``"file"``, ``"dir"`` or ``"any"`` (existence not required).
        allowed_suffixes: Optional lower-case suffixes (e.g. ``{".pbix"}``).
        roots: Pre-computed roots (defaults to :func:`allowed_source_roots`).

    Raises:
        ValidationError: empty / malformed path, wrong type, or missing file.
        PermissionError: path resolves outside every allowed root.
    """
    text = str(raw_path or "").strip()
    if not text:
        raise ValidationError("A path is required")
    if "\x00" in text:
        raise ValidationError("Invalid path")

    resolved = _safe_resolve(Path(text))
    if resolved is None:
        raise ValidationError("Invalid path")

    effective_roots = roots if roots is not None else allowed_source_roots()
    if not _is_within(resolved, effective_roots):
        logger.warning("[FileAccess] rejected path outside allowed roots: %s", resolved)
        raise PermissionError(
            "Path is outside the allowed folders. Upload the file, or add its folder "
            "under Settings > Local folders."
        )

    if allowed_suffixes is not None:
        suffixes = {s.lower() for s in allowed_suffixes}
        if resolved.suffix.lower() not in suffixes:
            raise ValidationError(f"Only {', '.join(sorted(suffixes))} files are supported")

    if expect == "file" and not resolved.is_file():
        raise ValidationError(f"File not found: {resolved}")
    if expect == "dir" and not resolved.is_dir():
        raise ValidationError(f"Directory not found: {resolved}")
    return resolved


def is_path_allowed(raw_path: str, roots: Optional[List[Path]] = None) -> bool:
    try:
        resolve_allowed_path(raw_path, expect="any", roots=roots)
        return True
    except (PermissionError, ValidationError):
        return False


# Config/payload keys that carry a local filesystem path the server will read.
# (``source_path`` is deliberately absent: in mappings it is a table/column
# path, not a filesystem path.)
SOURCE_FILE_KEYS = frozenset({
    "pbix_path",
    "pbix_file_path",
    "pbix_paths",
    "pbix_files",
    "pbix_folder",
    "file_path",
    "twb_path",
    "twbx_path",
    "tds_path",
    "tdsx_path",
})


def iter_source_file_paths(node: Any) -> Iterable[str]:
    """Yield every local source path found under :data:`SOURCE_FILE_KEYS`."""
    if isinstance(node, dict):
        # Power BI Desktop sources connect live to msmdsrv and are not restricted
        # by the local folder allowlist. Skip traversing pbi_desktop nodes.
        stype = str(
            node.get("type")
            or node.get("source_type")
            or node.get("source_connector")
            or node.get("sourceConnector")
            or node.get("adapter")
            or ""
        ).strip().lower()
        if not stype:
            src = node.get("source")
            if isinstance(src, dict):
                stype = str(src.get("type") or src.get("source_type") or src.get("adapter") or "").strip().lower()
            elif isinstance(src, str):
                stype = src.strip().lower()
        if not stype:
            src_cfg = node.get("source_config")
            if isinstance(src_cfg, dict):
                stype = str(src_cfg.get("type") or src_cfg.get("source_type") or src_cfg.get("adapter") or "").strip().lower()
        if stype in ("pbi_desktop", "pbidesktop"):
            return

        for key, value in node.items():
            if str(key).lower() in SOURCE_FILE_KEYS:
                if isinstance(value, str) and value.strip():
                    yield value
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip():
                            yield item
                        elif isinstance(item, dict):
                            yield from iter_source_file_paths(item)
                elif isinstance(value, dict):
                    yield from iter_source_file_paths(value)
            else:
                yield from iter_source_file_paths(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_source_file_paths(item)


def validate_source_paths(config: Any, roots: Optional[List[Path]] = None) -> None:
    """Raise :class:`PermissionError` if any source path in ``config`` lies
    outside the allowed roots (existence is not required)."""
    effective_roots = roots
    for raw_path in iter_source_file_paths(config):
        if effective_roots is None:
            effective_roots = allowed_source_roots()
        resolve_allowed_path(raw_path, expect="any", roots=effective_roots)


def validate_file_path(
    raw: Optional[str],
    *,
    expect: str = "any",
    field: str = "path",
    roots: Optional[List[Path]] = None,
    allowed_suffixes: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Resolve and validate a user-supplied path against allowed roots.

    Returns the resolved string path (or None if raw is empty).
    Raises HTTPException(403) on DomainPermissionError.
    Raises HTTPException(400) on DomainValidationError.
    """
    if raw is None or not str(raw).strip():
        return None
    from fastapi import HTTPException
    from semabridge.domain.exceptions import PermissionError as DomainPermissionError
    from semabridge.domain.exceptions import ValidationError as DomainValidationError

    try:
        resolved = resolve_allowed_path(
            os.path.expandvars(os.path.expanduser(str(raw))),
            expect=expect,
            roots=roots,
            allowed_suffixes=allowed_suffixes,
        )
        return str(resolved)
    except DomainPermissionError as exc:
        raise HTTPException(status_code=403, detail=f"{field}: {exc}" if field else str(exc)) from exc
    except DomainValidationError as exc:
        raise HTTPException(status_code=400, detail=f"{field}: {exc}" if field else str(exc)) from exc

