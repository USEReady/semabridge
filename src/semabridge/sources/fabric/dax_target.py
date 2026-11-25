"""Server-side DAX target for Fabric projects (certification / parity).

Without this, /certify and /run-dax only get a DAX backend if the caller sends
one; with none, ``execute_dax`` auto-picks any open Power BI Desktop file, so a
Fabric project could be certified against an unrelated local model.

Rule: for a Fabric project the DAX side is ALWAYS the project's own Fabric
model (its workspace, its dataset, its identity). Nothing is guessed: a
missing workspace, an unknown source type, or a multi-model project without an
explicit dataset raises SourceConfigError with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml

from semabridge.sources.base import SourceConfigError, SourceCredentials


@dataclass(frozen=True)
class FabricDaxTarget:
    project_id: str
    workspace_id: str
    dataset_id: str
    identity_id: Optional[str] = None


def load_project_config(project_id: str) -> Optional[Dict[str, Any]]:
    """The project's live config, or None when the project has no saved config."""
    from semabridge.api.services.project_config_store import get_project_config_text

    text = get_project_config_text(project_id)
    if not str(text or "").strip():
        return None
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SourceConfigError(f"Project '{project_id}' config is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SourceConfigError(f"Project '{project_id}' config is not a mapping.")
    return parsed


def _configured_models(source: Dict[str, Any]) -> List[str]:
    from semabridge.utils.project_models import model_entry_key

    refs: List[str] = []
    explicit = str(source.get("dataset_id") or "").strip()
    if explicit:
        refs.append(explicit)
    for entry in source.get("models") or []:
        key = model_entry_key(entry)
        if isinstance(entry, dict):
            key = str(entry.get("id") or entry.get("dataset_id") or key).strip()
        if key and key.casefold() not in {r.casefold() for r in refs}:
            refs.append(key)
    return refs


def resolve_fabric_dax_target(
    project_id: Optional[str],
    *,
    dataset_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[FabricDaxTarget]:
    """The Fabric DAX target for ``project_id``, or None if it is not a Fabric project.

    None (not a Fabric project, or no project / no saved config) means the
    caller keeps its existing behaviour unchanged.
    """
    pid = str(project_id or "").strip()
    if not pid:
        return None
    cfg = config if config is not None else load_project_config(pid)
    if cfg is None:
        return None
    source = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
    stype = str(source.get("type") or "").strip().lower()
    if not stype:
        raise SourceConfigError(f"Project '{pid}' has no source.type; set it in the project settings.")
    if stype != "fabric":
        return None

    workspace_id = str(source.get("workspace_id") or (cfg.get("fabric") or {}).get("workspace_id") or "").strip()
    if not workspace_id:
        raise SourceConfigError(
            f"Fabric project '{pid}' has no workspace_id; the DAX side cannot be run "
            f"(no default workspace is assumed)."
        )

    ds = str(dataset_id or "").strip()
    if not ds:
        models = _configured_models(source)
        if not models:
            raise SourceConfigError(f"Fabric project '{pid}' has no model configured (source.models / source.dataset_id).")
        if len(models) > 1:
            raise SourceConfigError(
                f"Fabric project '{pid}' has {len(models)} models ({', '.join(models)}); "
                f"pass dax_dataset_id to choose which one to run the DAX query on."
            )
        ds = models[0]

    identity_id = str(source.get("identity_id") or "").strip() or None
    return FabricDaxTarget(project_id=pid, workspace_id=workspace_id, dataset_id=ds, identity_id=identity_id)


def run_fabric_dax(target: FabricDaxTarget, query: str, *, creds: Optional[SourceCredentials] = None) -> Dict[str, Any]:
    """Run ``query`` on the target model with the project's credentials; raise on any failure.

    Returns columns/rows normalized exactly like the rest_api backend
    (``normalize_dax_column_names``) so certification compares the same shapes.
    """
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.core.settings import FabricConfig
    from semabridge.reconciliation.dax_executor import normalize_dax_column_names
    from semabridge.sources.fabric.credentials import apply_to_extractor, resolve_fabric_credentials

    if creds is None:
        creds = resolve_fabric_credentials(identity_id=target.identity_id)
    extractor = apply_to_extractor(FabricExtractor(FabricConfig(workspace_id=target.workspace_id)), creds)
    raw_rows = extractor.execute_dax_query_strict(target.dataset_id, query)
    raw_cols = list(raw_rows[0].keys()) if raw_rows else []
    cols = normalize_dax_column_names(raw_cols)
    rows = [{cols[i]: r.get(raw_cols[i]) for i in range(len(raw_cols))} for r in raw_rows]
    return {
        "backend": "fabric_project",
        "columns": cols,
        "rows": rows,
        "metadata": {
            "workspace_id": target.workspace_id,
            "dataset_id": target.dataset_id,
            "credentials_origin": creds.origin,
        },
    }
