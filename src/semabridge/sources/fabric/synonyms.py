"""Report-layer synonyms for Fabric domain bootstrap (/from-osi).

Snapshots store the SML model, which keeps the report-caption synonyms the
Fabric report layer produced (features.fabric_report_layer). The SML -> OSI
step /from-osi uses does not carry synonyms, so a domain bootstrapped from a
Fabric snapshot would lose them. ``carry_sml_synonyms`` copies them back onto
the OSI model by exact element name -- metrics by name, columns by
(dataset, column) -- adding only synonyms the OSI element does not already have.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _key(text: Any) -> str:
    return str(text or "").strip().casefold()


def _sml_synonyms(sml_blob: Dict[str, Any]) -> Tuple[Dict[str, List[str]], Dict[Tuple[str, str], List[str]]]:
    metrics = {_key(m.get("unique_name")): list(m.get("synonyms") or [])
               for m in (sml_blob or {}).get("metrics") or [] if m.get("synonyms")}
    columns: Dict[Tuple[str, str], List[str]] = {}
    for ds in (sml_blob or {}).get("datasets") or []:
        for col in ds.get("columns") or []:
            if col.get("synonyms"):
                columns[(_key(ds.get("unique_name")), _key(col.get("unique_name")))] = list(col["synonyms"])
    return metrics, columns


def _merge(obj: Any, extra: List[str]) -> None:
    current = list(getattr(obj, "synonyms", None) or [])
    seen = {_key(s) for s in current}
    obj.synonyms = current + [s for s in extra if _key(s) not in seen]


def carry_sml_synonyms(osi: Any, sml_blob: Optional[Dict[str, Any]]) -> Any:
    """Return ``osi`` with the SML snapshot's synonyms merged in (in place)."""
    if osi is None or not isinstance(sml_blob, dict):
        return osi
    metrics, columns = _sml_synonyms(sml_blob)
    for m in getattr(osi, "metrics", None) or []:
        extra = metrics.get(_key(m.unique_name))
        if extra:
            _merge(m, extra)
    for ds in getattr(osi, "datasets", None) or []:
        for col in getattr(ds, "columns", None) or []:
            extra = columns.get((_key(ds.unique_name), _key(col.unique_name)))
            if extra:
                _merge(col, extra)
    return osi


def project_uses_fabric_report_layer(project_id: Optional[str]) -> bool:
    """True for a Fabric project whose config turns on features.fabric_report_layer."""
    if not project_id:
        return False
    from semabridge.sources.fabric.dax_target import load_project_config

    cfg = load_project_config(str(project_id)) or {}
    source = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
    features = ((cfg.get("behavior") or {}).get("features") or {}) if isinstance(cfg.get("behavior"), dict) else {}
    return str(source.get("type") or "").lower() == "fabric" and bool(features.get("fabric_report_layer"))
