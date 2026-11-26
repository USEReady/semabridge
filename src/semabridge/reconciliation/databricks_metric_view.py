"""Query a deployed Databricks metric view by its real column names.

A SemaBridge project deploys Unity Catalog metric views. Those are queried with
``MEASURE(<measure>)`` and their dimensions, never with the Snowflake-style
column expression stored in the ontology. Names in the view can differ from the
Power BI names (identifier-safe measure names; dimensions prefixed with their
table when two tables share a column name), so they are resolved against the
view's own columns (``DESCRIBE TABLE``) when the warehouse is reachable.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name or "").strip().strip("`\"").lower()).strip("_")


def is_project_metric_view(project_id: Optional[str], view: str, deployed_views: Iterable[str] = ()) -> bool:
    """True when ``view`` is one of the project's deployed metric views."""
    last = _norm(str(view or "").split(".")[-1])
    if not last:
        return False
    if last.endswith("_metric_view") or last.startswith("mv_"):
        return True
    return any(_norm(str(v or "").split(".")[-1]) == last for v in deployed_views if v)


def project_deployed_views(project_id: Optional[str]) -> List[str]:
    """Views the project's syncs deployed (deployment info + last successful runs)."""
    pid = str(project_id or "").strip()
    if not pid:
        return []
    views: List[str] = []
    try:
        from semabridge.repository.model_repository import ModelRepository

        info = ModelRepository().get_project_deployment_info(pid) or {}
        views += [info.get("semantic_view") or "", info.get("view_name") or ""]
    except Exception:  # noqa: BLE001
        pass
    try:
        from semabridge.api.services.run_records import list_project_runs

        for run in (list_project_runs(pid, limit=10) or []):
            for res in run.get("results") or []:
                if isinstance(res, dict) and res.get("view_name"):
                    views.append(str(res["view_name"]))
    except Exception:  # noqa: BLE001
        pass
    return [v for v in views if v]


def describe_columns(project_id: Optional[str], view: str, owner_id: Optional[str] = None) -> List[str]:
    """Column names of ``view`` (dimensions and measures); [] when it cannot be read."""
    if not re.fullmatch(r"[`\w.\- ]+", str(view or "")):
        return []
    try:
        import requests

        from semabridge.ai.databricks_genie_service import normalize_databricks_host, resolve_databricks_token
        from semabridge.ai.project_connection import resolve_project_warehouse_config
        from semabridge.core.engine.deployment.databricks import _auto_discover_warehouse_id
        from semabridge.utils.tls import requests_verify

        cfg = resolve_project_warehouse_config(project_id, "DATABRICKS", owner_id=owner_id)
        host = normalize_databricks_host(getattr(cfg, "host", "")) if cfg else ""
        if not host:
            return []
        verify = requests_verify()
        token = resolve_databricks_token(cfg, host=host, verify=verify)
        warehouse_id = str(getattr(cfg, "warehouse_id", "") or "").strip() or str(_auto_discover_warehouse_id(cfg) or "").strip()
        if not token or not warehouse_id:
            return []
        quoted = ".".join(f"`{p.strip().strip('`')}`" for p in str(view).split("."))
        resp = requests.post(
            f"https://{host}/api/2.0/sql/statements",
            json={"statement": f"DESCRIBE TABLE {quoted}", "warehouse_id": warehouse_id, "wait_timeout": "15s"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=20,
            verify=verify,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if (data.get("status") or {}).get("state") != "SUCCEEDED":
            return []
        names = []
        for row in (data.get("result") or {}).get("data_array") or []:
            col = str((row or [""])[0] or "").strip()
            if not col or col.startswith("#"):
                break  # partition / detail sections follow the column list
            names.append(col)
        return names
    except Exception as exc:  # noqa: BLE001 -- name resolution is best-effort
        logger.info("Could not describe Databricks view %s: %s", view, exc)
        return []


def _pick(candidates: List[str], columns: List[str]) -> Optional[str]:
    by_norm: Dict[str, str] = {}
    for c in columns:
        by_norm.setdefault(_norm(c), c)
    for cand in candidates:
        hit = by_norm.get(_norm(cand))
        if hit:
            return hit
    return None


def resolve_measure(metric_name: str, columns: List[str]) -> str:
    """The view's measure column for a Power BI measure name."""
    safe = re.sub(r"[^0-9A-Za-z_]+", "_", str(metric_name or "")).strip("_")
    return _pick([metric_name, safe, str(metric_name).split(".")[-1]], columns) or safe


def resolve_dimension(dim: str, columns: List[str]) -> str:
    """The view's dimension column for ``Table.Column`` (prefixed or plain)."""
    parts = [p for p in str(dim or "").split(".") if p]
    col = parts[-1] if parts else str(dim or "")
    table = parts[-2] if len(parts) > 1 else ""
    cands = [f"{table}_{col}" if table else col, col, dim]
    return _pick(cands, columns) or col


def resolve_names(
    metric_name: str, dimensions: List[str], columns: List[str]
) -> Tuple[str, List[str]]:
    """(measure, dimensions) as named in the view; best guesses when columns are unknown."""
    return resolve_measure(metric_name, columns), [resolve_dimension(d, columns) for d in dimensions if d and d.strip()]


def metric_view_query_inputs(
    project_id: Optional[str],
    view: str,
    metric_name: str,
    dimensions: List[str],
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Inputs for ``GoldenQueryVerifier.generate_sql_query(target_kind="metric_view")``."""
    columns = describe_columns(project_id, view, owner_id=owner_id)
    measure, dims = resolve_names(metric_name, list(dimensions or []), columns)
    return {"metric_sql_expression": measure, "dimensions": dims, "resolved_from_view": bool(columns)}
