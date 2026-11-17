from semabridge.api.services.project_shared import *
from semabridge.domain.exceptions import ConfigurationError, InternalError, ValidationError

async def import_pbix(payload: Dict[str, Any]):
    """Import a local .pbix file and extract its semantic model.

    Request body:
        {"pbix_path": "C:/path/to/model.pbix"}

    Returns:
        Extracted metadata: tables, measures, relationships, connections.
    """
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.core.exceptions import PBIXParsingError

    from semabridge.auth.file_access import resolve_allowed_path

    pbix_path = payload.get("pbix_path", "")
    if not pbix_path:
        raise ValidationError("pbix_path is required")
    # Only files under the upload root / configured local folders / models
    # path / Examples may be read (symlinks + traversal resolved first).
    pbix_path = str(
        await asyncio.to_thread(
            resolve_allowed_path, pbix_path, expect="file", allowed_suffixes={".pbix", ".pbit"}
        )
    )

    try:
        connector = LocalPBIXConnector({"pbix_path": pbix_path})
        connector.authenticate()
        result = connector.discover()

        logger.info(f"PBIX import: {len(result.get('tables', []))} tables extracted from {pbix_path}")
        return {
            "status": "success",
            "source": pbix_path,
            "tables": result.get("tables", []),
            "measures": result.get("measures", []),
            "relationships": result.get("relationships", []),
            "connections": result.get("connections", []),
            "m_code": result.get("m_code", []),
            "models": result.get("models", []),
            "metadata": result.get("metadata", {}),
        }
    except PBIXParsingError as e:
        raise ConfigurationError(str(e))
    except Exception as e:
        logger.exception(f"PBIX import failed: {e}")
        raise InternalError(str(e))

_BROWSE_SKIP_DIRS = frozenset({"node_modules", "__pycache__", ".git", ".venv", "venv", ".mypy_cache", ".pytest_cache"})


def _browse_result_cap() -> int:
    try:
        return max(1, int(os.environ.get("SEMABRIDGE_PBIX_BROWSE_LIMIT", "1000")))
    except ValueError:
        return 1000


def _scan_pbix_files(scan_dir: Path, limit: int) -> Dict[str, Any]:
    """Walk ``scan_dir`` for .pbix files (no symlink following, hidden and
    dependency dirs skipped), stopping after ``limit`` results."""
    files: List[Dict[str, Any]] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(scan_dir, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _BROWSE_SKIP_DIRS]
        for filename in filenames:
            if not filename.lower().endswith(".pbix"):
                continue
            candidate = Path(dirpath) / filename
            try:
                if candidate.is_symlink():
                    continue
                stat = candidate.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": candidate.name,
                    "path": str(candidate.resolve()),
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
            if len(files) >= limit:
                truncated = True
                return {"files": files, "truncated": truncated}
    return {"files": files, "truncated": truncated}


async def browse_pbix_files(directory: str = ""):
    """List .pbix files in a directory for the file picker.

    Args:
        directory: Directory to scan. Defaults to configured local_models_path.
            Must resolve under an allowed root (see
            ``semabridge.auth.file_access``).

    Returns:
        List of .pbix file paths found (capped by SEMABRIDGE_PBIX_BROWSE_LIMIT,
        default 1000; ``truncated`` is true when the cap was hit).
    """
    from semabridge.auth.file_access import resolve_allowed_path

    if directory:
        scan_dir = await asyncio.to_thread(resolve_allowed_path, directory, expect="any")
    else:
        scan_dir = _resolve_models_path()
    if not scan_dir.is_dir():
        return {"files": [], "directory": str(scan_dir), "truncated": False}

    result = await asyncio.to_thread(_scan_pbix_files, scan_dir, _browse_result_cap())
    return {"files": result["files"], "directory": str(scan_dir), "truncated": result["truncated"]}
