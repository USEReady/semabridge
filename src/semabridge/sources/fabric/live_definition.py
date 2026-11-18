"""Live drift for Fabric projects (features.fabric_live_drift).

Three-way comparison per model:

  live      = the model's CURRENT definition in Fabric (get_model_definition)
  baseline  = the last synced snapshot (SML: original DAX in ``expression``,
              translated SQL in ``sql_expression``, descriptions, synonyms)
  deployed  = the deployed semantic view (DESCRIBE SEMANTIC VIEW rows)

Reported (status "pending", like every drift event):

* a measure whose DAX changed in Fabric after the sync, unless the deployed
  view already carries the new definition;
* a measure added in Fabric (not in the snapshot, not deployed) or removed in
  Fabric (in the snapshot, still deployed);
* a column added / removed / retyped in Fabric since the sync;
* ontology drift: a measure description changed in Fabric, or the project's
  ontology description / synonyms differing from what the deployed view says.

Nothing is guessed: models are resolved with validate_fabric_models (GUID or
exact name), snapshots are matched by model key, and a model never synced is
a warning, not an event. PBIX projects keep drift_service's existing comparison.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from semabridge.sources.base import ModelIdentity, SourceConfigError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# ── project switch ───────────────────────────────────────────────────────────
def fabric_live_drift_enabled(project_id: Optional[str]) -> bool:
    if not project_id:
        return False
    from semabridge.sources.fabric.dax_target import load_project_config

    cfg = load_project_config(str(project_id)) or {}
    source = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
    features = ((cfg.get("behavior") or {}).get("features") or {}) if isinstance(cfg.get("behavior"), dict) else {}
    return str(source.get("type") or "").lower() == "fabric" and bool(features.get("fabric_live_drift"))


# ── normalisation ────────────────────────────────────────────────────────────
def _norm_expr(expr: Any) -> str:
    text = "\n".join(expr) if isinstance(expr, list) else str(expr or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _view_name(name: Any) -> str:
    tail = str(name or "").split(".")[-1]
    return re.sub(r"[^A-Za-z0-9]+", "_", tail).strip("_").upper()


def _synonyms(value: Any) -> List[str]:
    if isinstance(value, list):
        items = value
    else:
        text = str(value or "").strip()
        try:
            items = json.loads(text) if text.startswith("[") else [s for s in re.split(r"\s*,\s*", text.strip("()")) if s]
        except ValueError:
            items = [text]
    return sorted({str(s).strip().strip("'\"") for s in items if str(s).strip().strip("'\"")}, key=str.casefold)


# ── readers ──────────────────────────────────────────────────────────────────
def live_measures(tmsl: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for t in ((tmsl or {}).get("model") or {}).get("tables") or []:
        for m in t.get("measures") or []:
            if m.get("name"):
                out[str(m["name"]).casefold()] = {"name": m["name"], "table": t.get("name"),
                                                  "expression": m.get("expression"), "description": m.get("description") or ""}
    return out


def live_columns(tmsl: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for t in ((tmsl or {}).get("model") or {}).get("tables") or []:
        for c in t.get("columns") or []:
            if c.get("name") and c.get("type") != "rowNumber":
                out[(str(t.get("name")).casefold(), str(c["name"]).casefold())] = {
                    "table": t.get("name"), "name": c["name"], "data_type": str(c.get("dataType") or "").casefold()}
    return out


def snapshot_measures(blob: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for m in (blob or {}).get("metrics") or []:
        name = m.get("original_name") or m.get("label") or m.get("unique_name")
        if name:
            out[str(name).casefold()] = {"name": name, "unique_name": m.get("unique_name"), "expression": m.get("expression"),
                                         "sql_expression": m.get("sql_expression") or "", "description": m.get("description") or ""}
    return out


def snapshot_columns(blob: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ds in (blob or {}).get("datasets") or []:
        table = ds.get("original_name") or ds.get("label") or ds.get("unique_name")
        for c in ds.get("columns") or []:
            name = c.get("original_name") or c.get("label") or c.get("unique_name")
            if table and name:
                out[(str(table).casefold(), str(name).casefold())] = {
                    "table": table, "name": name, "data_type": str(c.get("source_type") or "").casefold()}
    return out


def deployed_entries(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """DESCRIBE SEMANTIC VIEW rows -> {VIEW_NAME: {kind, expression, comment, synonyms}}."""
    out: Dict[str, Dict[str, Any]] = {}
    for rec in rows or []:
        name = rec.get("object_name") or rec.get("name")
        if not name:
            continue
        entry = out.setdefault(_view_name(name), {"kind": str(rec.get("object_kind") or "").upper(),
                                                  "expression": "", "comment": "", "synonyms": []})
        prop = str(rec.get("property") or "").upper()
        val = rec.get("property_value")
        if prop == "EXPRESSION":
            entry["expression"] = str(val or "")
        elif prop == "COMMENT":
            entry["comment"] = str(val or "")
        elif prop == "SYNONYMS":
            entry["synonyms"] = _synonyms(val)
    return out


def _converted_metric_names(live_tmsl: Dict[str, Any]) -> set:
    """Metric names the sync's TMSL -> OSI conversion yields for the live definition."""
    import copy

    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter

    osi = TMSLToOSIConverter().to_osi({"tmsl": copy.deepcopy(live_tmsl), "workspace_id": "live-drift", "dataset_id": "live-drift"})
    names = set()
    for m in osi.metrics:
        names.update({str(m.unique_name or "").casefold(), str(getattr(m, "label", "") or "").casefold()})
    names.discard("")
    return names


# ── comparison (pure) ────────────────────────────────────────────────────────
def compare(
    live_tmsl: Dict[str, Any],
    snapshot_blob: Dict[str, Any],
    deployed_rows: Optional[List[Dict[str, Any]]],
    *,
    domain: Any = None,
    propose: Optional[Callable[[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """Drift findings (dicts without ids / project fields) for one model."""
    if propose is None:
        from semabridge.governance.drift_service import propose_snowflake_sql as propose
    deployed = deployed_entries(deployed_rows or []) if deployed_rows is not None else None
    live_m, base_m = live_measures(live_tmsl), snapshot_measures(snapshot_blob)
    findings: List[Dict[str, Any]] = []

    def dep(name: str) -> Optional[Dict[str, Any]]:
        return None if deployed is None else deployed.get(_view_name(name))

    for key, live in live_m.items():
        base = base_m.get(key)
        if base is None:
            if deployed is None or dep(live["name"]) is None:
                findings.append({"metric_name": live["name"], "drift_type": "ADDED_IN_FABRIC",
                                 "bi_definition": live["expression"], "baseline_definition": "",
                                 "snowflake_definition": "(not present in semantic view)",
                                 "proposed_snowflake_definition": propose(live["name"], str(live["expression"] or ""))})
            continue
        if _norm_expr(live["expression"]) != _norm_expr(base["expression"]):
            d = dep(base["unique_name"] or live["name"])
            proposed = propose(live["name"], "\n".join(live["expression"]) if isinstance(live["expression"], list) else str(live["expression"] or ""))
            already = bool(d and proposed and _norm_expr(d["expression"]) == _norm_expr(proposed))
            if not already:
                findings.append({"metric_name": live["name"], "drift_type": "EXPRESSION_CHANGED_IN_FABRIC",
                                 "bi_definition": live["expression"], "baseline_definition": base["expression"],
                                 "snowflake_definition": (d or {}).get("expression") or base["sql_expression"],
                                 "proposed_snowflake_definition": proposed})
        if (live["description"] or "") != (base["description"] or ""):
            d = dep(base["unique_name"] or live["name"])
            if not (d and d.get("comment") == live["description"]):
                findings.append({"metric_name": live["name"], "drift_type": "ONTOLOGY_DESCRIPTION_CHANGED_IN_FABRIC",
                                 "bi_definition": live["description"], "baseline_definition": base["description"],
                                 "snowflake_definition": (d or {}).get("comment", ""), "proposed_snowflake_definition": ""})
    # "Removed" is judged against the metrics the SAME converter produces from
    # the live definition, so metrics the sync derives (e.g. implicit column
    # aggregations) are on both sides and never reported as removed.
    live_converted = _converted_metric_names(live_tmsl)
    for key, base in base_m.items():
        names = {key, str(base.get("unique_name") or "").casefold()}
        if key in live_m or names & live_converted:
            continue
        if deployed is None or dep(base["unique_name"] or base["name"]) is not None:
            findings.append({"metric_name": base["name"], "drift_type": "REMOVED_IN_FABRIC",
                             "bi_definition": "(removed in Fabric)", "baseline_definition": base["expression"],
                             "snowflake_definition": (dep(base["unique_name"] or base["name"]) or {}).get("expression") or base["sql_expression"],
                             "proposed_snowflake_definition": ""})

    live_c, base_c = live_columns(live_tmsl), snapshot_columns(snapshot_blob)
    for key, col in live_c.items():
        if key not in base_c:
            findings.append({"metric_name": f"{col['table']}.{col['name']}", "drift_type": "COLUMN_ADDED_IN_FABRIC",
                             "bi_definition": col["data_type"], "baseline_definition": "", "snowflake_definition": "",
                             "proposed_snowflake_definition": ""})
        elif base_c[key]["data_type"] and col["data_type"] and base_c[key]["data_type"] != col["data_type"]:
            findings.append({"metric_name": f"{col['table']}.{col['name']}", "drift_type": "COLUMN_TYPE_CHANGED_IN_FABRIC",
                             "bi_definition": col["data_type"], "baseline_definition": base_c[key]["data_type"],
                             "snowflake_definition": "", "proposed_snowflake_definition": ""})
    for key, col in base_c.items():
        if key not in live_c:
            findings.append({"metric_name": f"{col['table']}.{col['name']}", "drift_type": "COLUMN_REMOVED_IN_FABRIC",
                             "bi_definition": "(removed in Fabric)", "baseline_definition": col["data_type"],
                             "snowflake_definition": "", "proposed_snowflake_definition": ""})

    # ontology drift: the project's ontology vs what the deployed view says
    if domain is not None and deployed is not None:
        for m in getattr(domain, "metrics", None) or []:
            d = dep(m.name)
            if d is None:
                continue
            want_syn = _synonyms(list(getattr(m, "synonyms", None) or []))
            if want_syn and want_syn != d["synonyms"]:
                findings.append({"metric_name": m.name, "drift_type": "ONTOLOGY_SYNONYMS_NOT_DEPLOYED",
                                 "bi_definition": ", ".join(want_syn), "baseline_definition": "",
                                 "snowflake_definition": ", ".join(d["synonyms"]), "proposed_snowflake_definition": ""})
            want_desc = str(getattr(m, "description", "") or "").strip()
            if want_desc and want_desc != d["comment"].strip():
                findings.append({"metric_name": m.name, "drift_type": "ONTOLOGY_DESCRIPTION_NOT_DEPLOYED",
                                 "bi_definition": want_desc, "baseline_definition": "",
                                 "snowflake_definition": d["comment"], "proposed_snowflake_definition": ""})
    return findings


# ── orchestration ────────────────────────────────────────────────────────────
@dataclass
class LiveDriftResult:
    events: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked_models: int = 0


def _fetch_definition(identity: ModelIdentity, creds: Any) -> Dict[str, Any]:
    from semabridge.sources.base import get_source_adapter

    adapter = get_source_adapter("fabric")
    return adapter.live_definition(identity, creds)


def _project_models(project_id: str) -> List[Any]:
    from semabridge.ontology.project_models import project_models

    return project_models(project_id)


def live_fabric_drift(
    project_id: str,
    domains: List[Any],
    deployed_rows_by_view: Optional[Dict[str, List[Dict[str, Any]]]],
    *,
    lister: Any = None,
    fetch_definition: Callable[[ModelIdentity, Any], Dict[str, Any]] = None,
    project_models: Callable[[str], List[Any]] = None,
) -> LiveDriftResult:
    """Live drift events for every model of a Fabric project."""
    from semabridge.ontology.models import model_key
    from semabridge.sources.fabric.credentials import resolve_fabric_credentials
    from semabridge.sources.fabric.dax_target import load_project_config
    from semabridge.sources.fabric.validation import validate_fabric_models

    fetch_definition = fetch_definition or _fetch_definition
    project_models = project_models or _project_models
    cfg = load_project_config(project_id) or {}
    source = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
    workspace = str(source.get("workspace_id") or (cfg.get("fabric") or {}).get("workspace_id") or "").strip()
    if not workspace:
        raise SourceConfigError(f"Fabric project '{project_id}' has no source.workspace_id; live drift needs it.")
    refs = [str(source["dataset_id"])] if source.get("dataset_id") else [
        str(m.get("id") or m.get("name")) if isinstance(m, dict) else str(m) for m in (source.get("models") or [])]
    creds = resolve_fabric_credentials(identity_id=source.get("identity_id"))
    check = validate_fabric_models(refs, workspace, creds, max_models=50, lister=lister)
    snapshots = [pm for pm in project_models(project_id) if getattr(pm, "blob", None)]
    result = LiveDriftResult(warnings=list(check.warnings))
    now = datetime.now(timezone.utc).isoformat()

    for ref, identity in check.by_ref.items():
        keys = {model_key(ref), model_key(identity.display_name), model_key(identity.model_id)}
        pm = next((p for p in snapshots if ({p.model_id, *getattr(p, "aliases", [])} & keys)), None)
        if pm is None:
            result.warnings.append(f"Model '{identity.display_name}' has no synced snapshot yet; live drift has nothing to compare.")
            continue
        domain = next((d for d in domains if getattr(d, "source_model_id", None) in {pm.model_id, *getattr(pm, "aliases", [])}), None)
        if domain is None and len(domains) == 1:
            domain = domains[0]
        view = getattr(domain, "semantic_view_name", None) or getattr(pm, "semantic_view", None)
        rows = None
        if deployed_rows_by_view is not None and view:
            rows = deployed_rows_by_view.get(_view_name(view))
        live = fetch_definition(identity, creds)
        result.checked_models += 1
        for f in compare(live, pm.blob, rows, domain=domain):
            result.events.append({
                "id": f"drift-{uuid.uuid4().hex[:10]}",
                "project_id": project_id,
                "domain": getattr(domain, "domain", None),
                "domain_id": getattr(domain, "id", None),
                "model_id": pm.model_id,
                "semantic_view": view,
                "source": "fabric_live",
                "status": "pending",
                "resolution_reason": "",
                "detected_at": now,
                "affected_objects": [v for v in (view, f["metric_name"]) if v],
                **f,
            })
    return result
