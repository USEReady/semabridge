"""Pre-flight check of a Fabric project's models against its workspace.

``validate_fabric_models`` lists the workspace's semantic models with the
project's own credentials and resolves every configured entry, first by GUID,
then by exact display name (case-insensitive). Nothing is guessed:

* an entry matching no model            -> SourceNotFoundError
* a name shared by several models        -> SourceConfigError (lists the ids)
* more models than the cap               -> SourceConfigError
* the same model listed twice            -> warning, kept once

Every message names the workspace and the model. The result is one
``ModelIdentity`` per model (GUID + display name + workspace), in config order.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from semabridge.sources.base import ModelIdentity, SourceConfigError, SourceCredentials, SourceNotFoundError

logger = logging.getLogger(__name__)

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# A model lister returns the workspace's semantic models as Fabric/Power BI
# reports them: dicts with "id" and "displayName".
ModelLister = Callable[[str, Optional[SourceCredentials]], List[Dict[str, Any]]]


@dataclass
class FabricModelCheck:
    identities: List[ModelIdentity]
    warnings: List[str] = field(default_factory=list)
    # every configured ref -> its model (two refs naming one model share it)
    by_ref: Dict[str, ModelIdentity] = field(default_factory=dict)


def _entry_ref(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("id") or entry.get("dataset_id") or entry.get("name") or "").strip()
    return str(entry or "").strip()


def list_workspace_models(workspace_id: str, creds: Optional[SourceCredentials]) -> List[Dict[str, Any]]:
    """The workspace's semantic models via FabricExtractor (project credentials)."""
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.core.settings import FabricConfig
    from semabridge.sources.fabric.credentials import apply_to_extractor

    extractor = FabricExtractor(FabricConfig(workspace_id=workspace_id))
    if creds is not None:
        apply_to_extractor(extractor, creds)
    return list(extractor.list_semantic_models() or [])


def validate_fabric_models(
    models: Sequence[Any],
    workspace: str,
    creds: Optional[SourceCredentials],
    *,
    max_models: int,
    lister: Optional[ModelLister] = None,
) -> FabricModelCheck:
    ws = str(workspace or "").strip()
    if not ws:
        raise SourceConfigError("Fabric source has no workspace: set source.workspace_id in the project settings.")
    refs = [r for r in (_entry_ref(m) for m in models) if r]
    if not refs:
        raise SourceConfigError(f"No Fabric models configured for workspace '{ws}': set source.models.")

    catalog = (lister or list_workspace_models)(ws, creds)
    by_id = {str(m.get("id") or "").lower(): m for m in catalog if m.get("id")}
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for m in catalog:
        by_name.setdefault(str(m.get("displayName") or "").casefold(), []).append(m)

    identities: List[ModelIdentity] = []
    warnings: List[str] = []
    seen: Dict[str, str] = {}
    by_ref: Dict[str, ModelIdentity] = {}
    for ref in refs:
        hit = by_id.get(ref.lower()) if _GUID_RE.match(ref) else None
        if hit is None:
            named = by_name.get(ref.casefold(), [])
            if len(named) > 1:
                ids = ", ".join(str(m.get("id")) for m in named)
                raise SourceConfigError(
                    f"Model '{ref}' is ambiguous in workspace '{ws}': {len(named)} semantic models have that name "
                    f"({ids}). Use the dataset id in source.models."
                )
            hit = named[0] if named else None
        if hit is None:
            raise SourceNotFoundError(
                f"Model '{ref}' was not found in workspace '{ws}' (or this project's credentials cannot see it)."
            )
        guid = str(hit["id"])
        identity = next((i for i in identities if i.model_id.lower() == guid.lower()), None) or ModelIdentity(
            "fabric", guid, str(hit.get("displayName") or guid), ws
        )
        by_ref[ref] = identity
        if guid.lower() in seen:
            msg = f"Model '{ref}' is listed more than once for workspace '{ws}' (same model as '{seen[guid.lower()]}'); it is synced once."
            logger.warning(msg)
            warnings.append(msg)
            continue
        seen[guid.lower()] = ref
        identities.append(identity)

    if len(identities) > max_models:
        raise SourceConfigError(
            f"{len(identities)} Fabric models configured for workspace '{ws}'; at most {max_models} are supported per project."
        )
    return FabricModelCheck(identities, warnings, by_ref)
