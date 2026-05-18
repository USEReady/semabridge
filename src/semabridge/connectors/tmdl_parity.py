from __future__ import annotations

import json
import os
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


def _canonicalize(obj: Any) -> str:
    try:
        return json.dumps(obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj, sort_keys=True, separators=(",", ":"))
    except Exception:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def log_parity_result(*, baseline_sml: Any, tmdl_sml: Any, identifier: str, out_dir: str | None = None) -> Path:
    """Write a structured parity artifact for shadow-mode diagnostics.

    The artifact contains canonicalized SML JSON for baseline (TMSL) and TMDL,
    content hashes, and a simple equality flag. This is intentionally simple
    and designed for offline inspection by engineers.
    """
    out_dir = out_dir or os.getenv("SEMABRIDGE_TMDL_PARITY_DIR") or os.path.join("output", "debug", "tmdl_parity")
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)

    canonical_baseline = _canonicalize(baseline_sml)
    canonical_tmdl = _canonicalize(tmdl_sml)

    h_baseline = sha256(canonical_baseline.encode("utf-8")).hexdigest()
    h_tmdl = sha256(canonical_tmdl.encode("utf-8")).hexdigest()

    equal = h_baseline == h_tmdl

    artifact = {
        "identifier": identifier,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "baseline_hash": h_baseline,
        "tmdl_hash": h_tmdl,
        "equal": equal,
        "baseline_canonical": canonical_baseline,
        "tmdl_canonical": canonical_tmdl,
    }

    filename = f"parity_{identifier}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{h_baseline[:8]}_{h_tmdl[:8]}.json"
    out_path = p.joinpath(filename)
    out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return out_path
