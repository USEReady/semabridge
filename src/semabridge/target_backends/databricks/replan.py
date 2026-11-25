"""Re-planning from a dry run's cached inputs (integration batch 2).

The dry run (Step 6b) stores what the planner read -- the model after the
Unity Catalog column rewrite, the table plan, the security-role state and the
planner defaults -- next to the plan files:

    output/databricks/<project>[/<model>]/databricks_planner_input.json

A re-plan reruns ONLY the planner on that cache with the current saved names
and planner options (approximate/strict, excluded measures). It never reads
Unity Catalog: the plan says which table check it is based on, and bindings
are checked against Unity Catalog again before an actual deploy (3.8).

Ordering: every plan carries a ``revision`` (milliseconds; the dry run's own
plan uses its run time). A plan is recorded only when its revision is newer
than the recorded one, so an older response can never overwrite a newer plan.
A failed re-plan marks the recorded plan STALE (it is then never presented as
valid) until a later plan succeeds.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CACHE_FILE = "databricks_planner_input.json"
STATE_FILE = "databricks_view_plan_state.json"
PLAN_JSON = "databricks_view_plan.json"
PLAN_MD = "databricks_view_plan.md"
CACHE_VERSION = 1

_lock = threading.Lock()


class ReplanError(RuntimeError):
    """The re-plan cannot run (no cached dry run, unreadable cache, bad options)."""


def _safe(name: Any) -> str:
    """Same rule as ExecutionEngine._safe_output_name (output folder names)."""
    raw = str(name or "model")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)
    safe = re.sub(r"_+", "_", safe).strip("._")
    return safe or "model"


def output_dir(project_id: Any, model_subdir: Optional[str] = None, root: Path = Path("output")) -> Path:
    path = root / "databricks" / _safe(project_id)
    if model_subdir:
        path /= _safe(model_subdir)
    return path


def now_revision() -> int:
    return int(time.time() * 1000)


# --- table plan (de)serialisation ---------------------------------------------------------

def table_plan_to_dict(plan: Any) -> Optional[Dict[str, Any]]:
    return None if plan is None else asdict(plan)


def table_plan_from_dict(data: Optional[Dict[str, Any]]) -> Any:
    from semabridge.target_backends.databricks.tables import TableBinding, TablePlan, UcTable

    if not data:
        return None
    bindings = []
    for b in data.get("bindings") or []:
        b = dict(b)
        t = b.get("table")
        if t:
            t = dict(t)
            t["columns"] = {k: tuple(v) for k, v in (t.get("columns") or {}).items()}
            b["table"] = UcTable(**t)
        b["stub_columns"] = [tuple(x) for x in b.get("stub_columns") or []]
        bindings.append(TableBinding(**b))
    return TablePlan(
        platform=data.get("platform", "databricks"),
        source_schemas=[tuple(x) for x in data.get("source_schemas") or []],
        on_missing_source=data.get("on_missing_source", "stub"),
        bindings=bindings,
        warnings=list(data.get("warnings") or []),
        error=data.get("error"),
    )


# --- cache and state ------------------------------------------------------------------------

def write_cache(out_dir: Path, *, sml_model: Any, table_plan: Any, security_roles: Optional[int],
                defaults: Dict[str, Any], project_planner: Dict[str, Any], cache_ref: Dict[str, Any],
                revision: int) -> Dict[str, Any]:
    """Store the planner's inputs; returns the plan's ``meta`` block."""
    out_dir.mkdir(parents=True, exist_ok=True)
    checked_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "version": CACHE_VERSION,
        "cache_ref": cache_ref,
        "table_check_at": checked_at,
        "table_check": "live" if table_plan is not None else "no_connection",
        "sml_model": sml_model.model_dump(mode="json"),
        "table_plan": table_plan_to_dict(table_plan),
        "security_roles": security_roles,
        "defaults": defaults,
        "project_planner": project_planner,
    }
    tmp = out_dir / (CACHE_FILE + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(out_dir / CACHE_FILE)
    return meta_for(payload, revision, source="dry_run")


def read_cache(out_dir: Path) -> Dict[str, Any]:
    path = out_dir / CACHE_FILE
    if not path.exists():
        raise ReplanError("no dry run has been recorded for this model yet: run a dry run first")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReplanError(f"the cached dry-run input is unreadable: {exc}") from None
    if data.get("version") != CACHE_VERSION:
        raise ReplanError("the cached dry-run input is from another version: run a dry run again")
    return data


def meta_for(cache: Dict[str, Any], revision: int, source: str) -> Dict[str, Any]:
    return {
        "revision": int(revision),
        "status": "valid",
        "source": source,                                   # dry_run | replan
        "cache_ref": cache.get("cache_ref") or {},
        "basis": {
            "table_check_at": cache.get("table_check_at"),
            "table_check": cache.get("table_check"),
            # 3.8 must check bindings against Unity Catalog again before deploying.
            "uc_recheck_required_before_deploy": True,
        },
    }


def read_state(out_dir: Path) -> Dict[str, Any]:
    try:
        return json.loads((out_dir / STATE_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(out_dir: Path, state: Dict[str, Any]) -> None:
    tmp = out_dir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_dir / STATE_FILE)


def record_plan(out_dir: Path, plan: Dict[str, Any], title: str, *, reset: bool = False) -> bool:
    """Record ``plan`` (with its meta) unless a newer plan is already recorded.
    ``reset``: a new dry run (a new basis) replaces whatever was recorded."""
    from semabridge.target_backends.databricks.planner.report import render

    revision = int(plan["meta"]["revision"])
    with _lock:
        out_dir.mkdir(parents=True, exist_ok=True)
        state = read_state(out_dir)
        if not reset and int(state.get("revision") or -1) >= revision:
            return False
        (out_dir / PLAN_JSON).write_text(json.dumps(plan, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (out_dir / PLAN_MD).write_text(render(plan, title), encoding="utf-8")
        _write_state(out_dir, {"revision": revision, "status": "valid", "error": None,
                               "source": plan["meta"]["source"]})
        return True


def mark_stale(out_dir: Path, revision: int, error: str) -> Dict[str, Any]:
    """A failed re-plan: the recorded plan is no longer valid for the current
    choices. Ignored when a newer plan is already recorded."""
    with _lock:
        state = read_state(out_dir)
        if int(state.get("revision") or -1) > int(revision):
            return state
        state = {"revision": int(revision), "status": "stale", "error": str(error),
                 "plan_revision": state.get("plan_revision", state.get("revision"))}
        if out_dir.exists():
            _write_state(out_dir, state)
        return state


def recorded_plan(out_dir: Path) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """(the recorded plan, its state). A stale state means: do not present it as valid."""
    state = read_state(out_dir)
    try:
        plan = json.loads((out_dir / PLAN_JSON).read_text(encoding="utf-8"))
    except Exception:
        plan = None
    if plan is not None and state.get("status") == "stale":
        plan.setdefault("meta", {})["status"] = "stale"
        plan["meta"]["error"] = state.get("error")
    return plan, state


# --- re-plan --------------------------------------------------------------------------------

def _settings(cache: Dict[str, Any], rows: List[Dict[str, Any]], planner_opts: Optional[Dict[str, Any]]):
    from semabridge.core.engine.config import collect_databricks_name_requests
    from semabridge.target_backends.databricks.planner import PlannerSettings

    values: Dict[str, Any] = {
        "calculate_mode": str(cache["defaults"].get("calculate_mode") or "approximate"),
        "view_mode": str(cache["defaults"].get("view_mode") or "separate_views"),
        "capabilities": frozenset(cache["defaults"].get("capabilities") or []),
    }
    merged = {**(cache.get("project_planner") or {}), **(planner_opts or {})}
    for key in ("calculate_mode", "view_mode", "primary_fact"):
        if merged.get(key):
            values[key] = str(merged[key]).strip().lower() if key != "primary_fact" else str(merged[key]).strip()
    for key in ("selected_tables", "excluded_measures"):
        if key in merged and merged[key] is not None:
            values[key] = tuple(str(x) for x in merged[key])
    requests = collect_databricks_name_requests(None, {"mappings_overrides": list(rows or [])})
    values["requested_names"] = tuple((r["source_path"], r["target_name"], r["origin"]) for r in requests)
    values["security_roles"] = cache.get("security_roles")
    return PlannerSettings(**values)


def replan(out_dir: Path, rows: List[Dict[str, Any]], planner_opts: Optional[Dict[str, Any]],
           revision: int, title: str) -> Dict[str, Any]:
    """Re-plan from the cache. Returns {status, plan, revision, superseded, stale, error}.
    Never raises for a planning failure: the recorded plan is marked stale instead."""
    from semabridge.sml.models import SMLModel
    from semabridge.target_backends.databricks.planner import plan_metric_views

    try:
        cache = read_cache(out_dir)
        settings = _settings(cache, rows, planner_opts)
        model = SMLModel.model_validate(cache["sml_model"])
        result = plan_metric_views(model, table_plan_from_dict(cache.get("table_plan")), settings).as_dict()
    except Exception as exc:  # planner self-check, bad options, unreadable cache
        state = mark_stale(out_dir, revision, f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "plan": None, "revision": revision, "superseded": False,
                "stale": state.get("status") == "stale", "error": str(exc)}
    result["meta"] = meta_for(cache, revision, source="replan")
    written = record_plan(out_dir, result, title)
    return {"status": "valid", "plan": result, "revision": revision, "superseded": not written,
            "stale": False, "error": None}
