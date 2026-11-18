"""Source fingerprinting for Fabric and PBIX models (features.skip_unchanged_sources).

Computes a deterministic fingerprint representing:
- Source model definition:
  * Fabric: `lastModifiedDateTime` attribute, falling back to SHA-256 of definition payload
  * PBIX: SHA-256 hash of the .pbix file contents
- Project configuration (mapping overrides, attached ontology domains and versions)
- SemaBridge version and commit hash
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


def get_semabridge_version_and_commit() -> str:
    """Return 'version@commit' or 'version'."""
    import semabridge
    ver = getattr(semabridge, "__version__", "1.0.0")
    commit = os.getenv("SEMABRIDGE_COMMIT") or os.getenv("GIT_COMMIT")
    if not commit:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            commit = ""
    return f"{ver}@{commit}" if commit else str(ver)


def get_fabric_fingerprint(model_payload: Any) -> str:
    """Compute fingerprint for a Fabric semantic model.

    Uses ``lastModifiedDateTime`` if present; otherwise computes SHA-256
    hash of the model definition.
    """
    if isinstance(model_payload, dict):
        # 1. Check for lastModifiedDateTime at root or in item metadata
        last_mod = (
            model_payload.get("lastModifiedDateTime")
            or model_payload.get("modifiedDateTime")
            or (model_payload.get("item") or {}).get("lastModifiedDateTime")
            or (model_payload.get("properties") or {}).get("lastModifiedDateTime")
        )
        if last_mod and str(last_mod).strip():
            return f"fabric-mod:{str(last_mod).strip()}"

        # 2. Fall back to SHA-256 of definition payload
        definition = model_payload.get("definition") or model_payload.get("model") or model_payload
        dumped = json.dumps(definition, sort_keys=True, default=str)
        digest = hashlib.sha256(dumped.encode("utf-8")).hexdigest()
        return f"fabric-sha256:{digest}"

    if isinstance(model_payload, (str, bytes)):
        raw = model_payload if isinstance(model_payload, bytes) else model_payload.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return f"fabric-sha256:{digest}"

    dumped = json.dumps(model_payload, sort_keys=True, default=str)
    return f"fabric-sha256:{hashlib.sha256(dumped.encode('utf-8')).hexdigest()}"


def get_pbix_fingerprint(pbix_path_or_bytes: Union[str, Path, bytes]) -> str:
    """Compute SHA-256 fingerprint of a PBIX file."""
    if isinstance(pbix_path_or_bytes, bytes):
        digest = hashlib.sha256(pbix_path_or_bytes).hexdigest()
        return f"pbix-sha256:{digest}"

    p = Path(pbix_path_or_bytes)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"PBIX file not found for fingerprinting: {p}")

    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return f"pbix-sha256:{hasher.hexdigest()}"


def compute_source_fingerprint(
    source_type: str,
    source_payload: Any,
) -> Optional[str]:
    """Unified fingerprint entry point for source model definition via SemanticSourceAdapter."""
    from semabridge.sources.base import get_source_adapter, SourceConfigError

    stype = str(source_type or "").strip().lower()
    if stype in ("local_pbix", "powerbi"):
        stype = "pbix"
    try:
        adapter = get_source_adapter(stype)
        return adapter.fingerprint(source_payload)
    except SourceConfigError:
        return None


def get_project_config_fingerprint(project_cfg: Optional[Dict[str, Any]]) -> str:
    """Compute a deterministic SHA-256 fingerprint of the project config.

    Includes:
    - mapping overrides (tables, columns, synonyms)
    - attached ontology domains and their versions
    - core configuration settings (excluding ephemeral timestamps/IDs)
    """
    if not project_cfg or not isinstance(project_cfg, dict):
        return "empty"

    mapping_overrides = (
        project_cfg.get("mapping_overrides")
        or project_cfg.get("mappings")
        or project_cfg.get("overrides")
        or {}
    )

    ontology_info: List[Dict[str, str]] = []
    ontology_sec = (
        project_cfg.get("ontology")
        or project_cfg.get("domains")
        or project_cfg.get("ontology_domains")
        or {}
    )
    if isinstance(ontology_sec, dict):
        domains = ontology_sec.get("domains") or ontology_sec.get("attached_domains") or []
        if isinstance(domains, list):
            for d in domains:
                if isinstance(d, dict):
                    ontology_info.append({
                        "domain": str(d.get("domain") or d.get("id") or d.get("name") or "").strip().lower(),
                        "version": str(d.get("version") or "1.0.0").strip(),
                    })
                elif isinstance(d, str):
                    ontology_info.append({"domain": d.strip().lower(), "version": "1.0.0"})
        elif isinstance(domains, dict):
            for d_name, d_val in sorted(domains.items()):
                ver = d_val.get("version", "1.0.0") if isinstance(d_val, dict) else str(d_val)
                ontology_info.append({"domain": str(d_name).strip().lower(), "version": str(ver).strip()})
    elif isinstance(ontology_sec, list):
        for d in ontology_sec:
            if isinstance(d, dict):
                ontology_info.append({
                    "domain": str(d.get("domain") or d.get("id") or d.get("name") or "").strip().lower(),
                    "version": str(d.get("version") or "1.0.0").strip(),
                })
            elif isinstance(d, str):
                ontology_info.append({"domain": d.strip().lower(), "version": "1.0.0"})

    ontology_info.sort(key=lambda x: (x.get("domain", ""), x.get("version", "")))

    try:
        from semabridge.ontology.store import DomainOntologyStore
        store = DomainOntologyStore()
        for item in ontology_info:
            d_name = item.get("domain")
            if d_name and item.get("version") == "1.0.0":
                ont = store.get(d_name)
                if ont and ont.version:
                    item["version"] = ont.version
    except Exception:
        pass

    ephemeral = {"run_id", "id", "created_at", "updated_at", "last_run", "status"}
    filtered_cfg = {k: v for k, v in project_cfg.items() if k not in ephemeral and not k.startswith("_")}

    payload = {
        "mapping_overrides": mapping_overrides,
        "ontology_domains": ontology_info,
        "config": filtered_cfg,
    }
    dumped = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def compute_combined_fingerprint(
    source_fingerprint: str,
    project_cfg: Optional[Dict[str, Any]] = None,
    semabridge_version: Optional[str] = None,
) -> str:
    """Unified fingerprint combining source, project config (overrides + domains), and version."""
    if not source_fingerprint:
        return ""
    ver = semabridge_version if semabridge_version is not None else get_semabridge_version_and_commit()
    cfg_hash = get_project_config_fingerprint(project_cfg)
    return f"{source_fingerprint}|cfg:{cfg_hash[:16]}|v:{ver}"
