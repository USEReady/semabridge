"""Optional integration point for Microsoft's TOM/TmdlSerializer.

This module provides a thin wrapper that attempts to use a TOM-based
parser when available (pythonnet + .NET TOM libraries). In many runtime
environments TOM will not be available; this module falls back to None
and logs a warning so the caller can continue using the resilient
text-based TMDL parser.

This file intentionally keeps the implementation minimal to avoid
including heavy dependencies in the core codepath.
"""
from __future__ import annotations

from typing import Any, Optional
import requests
import json
import os

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def parse_tmdl_with_tom(parts: list[dict[str, Any]], sidecar_url: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Attempt to parse a TMDL package using TOM/TmdlSerializer.

    Returns a dict in the same shape as the existing fallback
    (i.e. {"model": {"name":..., "tables":..., "relationships":...}})
    or ``None`` if TOM is unavailable or parsing fails.
    """
    # 1) If a sidecar URL is provided, prefer it — it's OS-friendly and isolates .NET
    try:
        url = sidecar_url or os.getenv("FABRIC_TOM_SIDECAR_URL")
        if url:
            endpoint = url.rstrip("/") + "/parse"
            try:
                resp = requests.post(endpoint, json={"parts": parts}, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                logger.warning("TOM sidecar returned %s: %s", resp.status_code, resp.text[:300])
                return None
            except Exception as e:
                logger.warning("TOM sidecar request failed: %s", e)
                return None
    except Exception:
        # Non-fatal; fall through to optional pythonnet attempt
        pass

    # 2) Fallback to embedded pythonnet integration if available
    try:
        import clr  # type: ignore
    except Exception:
        logger.debug("pythonnet not available for TOM; skipping embedded TOM path")
        return None

    try:
        logger.info("pythonnet available but embedded TOM integration is not implemented")
        return None
    except Exception as e:
        logger.warning(f"TOM parser encountered an error: {e}; falling back to text parser")
        return None
