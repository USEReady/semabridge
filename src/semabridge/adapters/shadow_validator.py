"""Shadow-mode validator: run TOM (optional) and fallback parser and report differences.

This utility is intentionally small and safe: TOM integration is optional and
any exceptions from the TOM path are caught and surfaced in the report rather
than raised. The function returns a dict describing parity and differences so
callers (sync jobs, diagnostics endpoints) can log or act on the results.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from semabridge.utils.logger import get_logger
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.core.settings import FabricConfig

logger = get_logger(__name__)


def _norm_rels_for_compare(rels: List[Dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    out: set[tuple[str, str, str, str]] = set()
    for r in rels or []:
        ft = str(r.get('fromTable') or r.get('from_table') or r.get('from_dataset') or '').strip().casefold()
        fc = str(r.get('fromColumn') or r.get('from_column') or (r.get('from_columns') or [None])[0] or '').strip().casefold()
        tt = str(r.get('toTable') or r.get('to_table') or r.get('to_dataset') or '').strip().casefold()
        tc = str(r.get('toColumn') or r.get('to_column') or (r.get('to_columns') or [None])[0] or '').strip().casefold()
        if ft and fc and tt and tc:
            out.add((ft, fc, tt, tc))
    return out


def compare_tom_and_fallback(parts: List[Dict[str, Any]], cfg: Optional[FabricConfig] = None) -> Dict[str, Any]:
    """Run TOM (if available) and the resilient fallback parser and return a comparison.

    Args:
        parts: list of part dicts from Fabric definition payload
        cfg: optional FabricConfig to instantiate a FabricExtractor for fallback parsing

    Returns:
        dict with keys: 'tom_available', 'tom_error', 'tom_rels', 'fallback_rels', 'only_in_tom', 'only_in_fallback', 'parity'
    """
    tom_available = False
    tom_error: Optional[str] = None
    tom_model: Optional[Dict[str, Any]] = None
    fallback_model: Optional[Dict[str, Any]] = None

    # Attempt TOM first (optional integration)
    try:
        from semabridge.adapters.tom_integration import parse_tmdl_with_tom

        try:
            sidecar = cfg.tom_sidecar_url if cfg is not None else None
            tom_model = parse_tmdl_with_tom(parts, sidecar_url=sidecar)
            tom_available = tom_model is not None
        except Exception as e:  # Defensive: do not let TOM crash the validator
            tom_error = str(e)
            logger.debug("TOM parse raised: %s", e)
    except Exception:
        # TOM integration module missing or import error — not available
        tom_available = False

    # Run fallback parser via FabricExtractor (uses only local parsing of parts)
    try:
        if cfg is None:
            cfg = FabricConfig(tenant_id="", client_id="", workspace_id="")
        extractor = FabricExtractor(cfg, access_token=None)
        # Use the internal TMDL package parser directly
        fallback_model = extractor._parse_tmdl_package_parts(parts)
    except Exception as e:
        logger.warning("Fallback parser failed in shadow validator: %s", e)
        fallback_model = None

    tom_rels = (tom_model or {}).get('model', {}).get('relationships', []) if tom_model else []
    fallback_rels = (fallback_model or {}).get('model', {}).get('relationships', []) if fallback_model else []

    tom_set = _norm_rels_for_compare(tom_rels)
    fallback_set = _norm_rels_for_compare(fallback_rels)

    only_in_tom = sorted(list(tom_set - fallback_set))
    only_in_fallback = sorted(list(fallback_set - tom_set))

    result = {
        'tom_available': tom_available,
        'tom_error': tom_error,
        'tom_count': len(tom_set),
        'fallback_count': len(fallback_set),
        'only_in_tom': only_in_tom,
        'only_in_fallback': only_in_fallback,
        'parity': len(only_in_tom) == 0 and len(only_in_fallback) == 0,
    }

    # Log a concise warning when parity fails so operators see it in logs.
    if not result['parity']:
        logger.warning(
            "Shadow-mode parser parity mismatch: tom=%s fallback=%s only_in_tom=%s only_in_fallback=%s",
            result['tom_count'], result['fallback_count'], result['only_in_tom'], result['only_in_fallback'],
        )

    return result
