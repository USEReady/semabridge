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

    pbix_path = payload.get("pbix_path", "")
    if not pbix_path:
        raise ValidationError("pbix_path is required")

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

async def browse_pbix_files(directory: str = ""):
    """List .pbix files in a directory for the file picker.

    Args:
        directory: Directory to scan. Defaults to configured local_models_path.

    Returns:
        List of .pbix file paths found.
    """
    scan_dir = Path(directory) if directory else _resolve_models_path()
    if not scan_dir.exists():
        return {"files": [], "directory": str(scan_dir)}

    pbix_files = [
        {
            "name": f.name,
            "path": str(f.resolve()),
            "size_bytes": f.stat().st_size,
            "modified": f.stat().st_mtime,
        }
        for f in scan_dir.rglob("*.pbix")
    ]

    return {"files": pbix_files, "directory": str(scan_dir)}

