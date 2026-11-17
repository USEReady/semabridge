"""Source fingerprinting for Fabric and PBIX models (features.skip_unchanged_sources).

Computes a deterministic fingerprint representing the model definition:
- Fabric: `lastModifiedDateTime` attribute, falling back to SHA-256 of definition payload
- PBIX: SHA-256 hash of the .pbix file contents
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union


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
    """Unified fingerprint entry point for any supported source type."""
    stype = str(source_type or "").strip().lower()
    if stype == "fabric":
        return get_fabric_fingerprint(source_payload)
    if stype in ("pbix", "local_pbix", "powerbi"):
        return get_pbix_fingerprint(source_payload)
    return None

