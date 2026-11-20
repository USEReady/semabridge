"""Read Fabric models as OSI for the ontology review before the dry run.

Mirrors ontology_review_service._read_pbix for Fabric: each selected model is
resolved against the workspace (validate_fabric_models, project credentials),
its definition is fetched (FabricExtractor.get_model_definition) and converted
with TMSLToOSIConverter. Every selected model is read; one model that fails
is reported in ``errors`` and does not hide the others.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from semabridge.sources.base import ModelIdentity, SourceConfigError, SourceCredentials, SourceError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def _extractor(identity: ModelIdentity, creds: Optional[SourceCredentials]) -> Any:
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.core.settings import FabricConfig
    from semabridge.sources.fabric.credentials import apply_to_extractor

    ext = FabricExtractor(FabricConfig(workspace_id=identity.workspace_id))
    if creds is not None:
        apply_to_extractor(ext, creds)
    return ext


def _read_fabric(
    identity: ModelIdentity,
    creds: Optional[SourceCredentials],
    *,
    extractor_factory: Callable[[ModelIdentity, Optional[SourceCredentials]], Any] = _extractor,
) -> Any:
    """One Fabric model (by its resolved identity) as an OSI model."""
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter

    tmsl = extractor_factory(identity, creds).get_model_definition(identity.model_id)
    if not tmsl:
        raise SourceError(f"Model '{identity.display_name}' returned no definition.")
    return TMSLToOSIConverter().to_osi(
        {
            "tmsl": tmsl,
            "workspace_id": identity.workspace_id,
            "dataset_id": identity.model_id,
            "display_name": identity.display_name,
        }
    )


def read_fabric_models(
    source_config: Dict[str, Any],
    selected_sources: List[str],
    *,
    lister: Any = None,
    extractor_factory: Callable[[ModelIdentity, Optional[SourceCredentials]], Any] = _extractor,
    max_models: int = 10,
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """(OSI models, per-model errors) for the selected Fabric models.

    No default workspace or model: a missing workspace or an empty selection
    is a SourceConfigError naming the field.
    """
    from semabridge.sources.fabric.credentials import resolve_fabric_credentials
    from semabridge.sources.fabric.validation import validate_fabric_models

    workspace = str((source_config or {}).get("workspace_id") or "").strip()
    if not workspace:
        raise SourceConfigError("Select a Fabric workspace (source.workspace_id) to review its models.")
    refs = [str(s).strip() for s in (selected_sources or []) if str(s or "").strip()]
    if not refs:
        refs = [str(m).strip() for m in ((source_config or {}).get("models") or []) if str(m or "").strip()]
    if not refs:
        raise SourceConfigError(f"Select at least one Fabric model in workspace '{workspace}' to review its measures.")

    creds = resolve_fabric_credentials(identity_id=(source_config or {}).get("identity_id"))
    check = validate_fabric_models(refs, workspace, creds, max_models=max_models, lister=lister)
    models: List[Any] = []
    errors: List[Dict[str, str]] = []
    for identity in check.identities:
        try:
            models.append(_read_fabric(identity, creds, extractor_factory=extractor_factory))
        except Exception as exc:  # one model must not hide the others; reported with its reason
            logger.warning("Ontology review: could not read Fabric model %s: %s", identity.display_name, exc)
            errors.append({"source": identity.display_name, "error": str(exc)})
    return models, errors
