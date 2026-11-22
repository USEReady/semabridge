"""Cross-Platform Deployment & Multi-Target Parity (Snowflake, Databricks, Fabric)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def preview_cross_platform_definitions(
    *,
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    metric: Optional[str] = None,
    targets: Optional[List[str]] = None,
    version_tag: str = "v1.0.0",
) -> Dict[str, Any]:
    """Generate platform-specific semantic definitions and flag gaps for Snowflake, Databricks, and Fabric."""
    from semabridge.ontology.store import ontology_store, project_key

    selected = [t.lower().strip() for t in (targets or ["snowflake", "databricks", "fabric"])]
    fed = ontology_store.get_federated_ontology()

    pkey = project_key(project_id) if project_id else None
    doms = [
        d for d in fed.domains.values()
        if (not domain or d.domain == domain)
        and (not pkey or project_key(getattr(d, "source_project_id", None)) == pkey or getattr(d, "source_project_id", None) == project_id)
    ]
    if not doms and project_id:
        try:
            doms = ontology_store.domains_for(project_id) or []
        except Exception:
            doms = []

    all_metrics = [m for d in doms for m in d.metrics]
    if metric:
        target_name = metric.strip().casefold()
        filtered = [
            m for m in all_metrics
            if m.name.casefold() == target_name
            or (getattr(m, "logical_name", None) or "").casefold() == target_name
            or (getattr(m, "label", None) or "").casefold() == target_name
        ]
        metrics = filtered if filtered else all_metrics
    else:
        metrics = all_metrics

    previews: Dict[str, Dict[str, Any]] = {}

    if "snowflake" in selected:
        lines = [f"-- Version: {version_tag} ({datetime.now(timezone.utc).isoformat()})"]
        gaps: List[str] = []
        for m in metrics:
            if not m.sql_expression:
                gaps.append(f"{m.name}: missing SQL expression")
                continue
            lines.append(f"  {m.name} AS {m.sql_expression} COMMENT = '{(m.description or '').replace(chr(39), '')}'")
        previews["snowflake"] = {
            "version_tag": version_tag,
            "metric_count": len(metrics),
            "gaps": gaps,
            "definition": "\n".join(lines),
        }

    if "databricks" in selected:
        lines = [f"-- Databricks Unity Catalog Metric View ({version_tag})"]
        gaps = []
        for m in metrics:
            if not m.sql_expression:
                gaps.append(f"{m.name}: requires SQL expression for Databricks Metric View")
                continue
            lines.append(f"  - name: {m.name}\n    expr: {m.sql_expression}")
        previews["databricks"] = {
            "version_tag": version_tag,
            "metric_count": len(metrics),
            "gaps": gaps,
            "definition": "\n".join(lines),
        }

    if "fabric" in selected:
        lines = [f"// Power BI / Fabric TMDL ({version_tag})"]
        gaps = []
        for m in metrics:
            if not m.dax_expression:
                gaps.append(f"{m.name}: missing DAX expression")
                continue
            lines.append(f"measure '{m.name}' = {m.dax_expression}")
        previews["fabric"] = {
            "version_tag": version_tag,
            "metric_count": len(metrics),
            "gaps": gaps,
            "definition": "\n".join(lines),
        }

    return {
        "project_id": project_id,
        "domain": domain,
        "metric": metric,
        "version_tag": version_tag,
        "targets": selected,
        "metrics": [
            {
                "name": m.name,
                "label": getattr(m, "label", m.name) or m.name,
                "sql_expression": m.sql_expression,
                "dax_expression": getattr(m, "dax_expression", None),
                "concept": getattr(m, "concept", None),
            }
            for m in all_metrics
        ],
        "previews": previews,
    }


def check_cross_platform_parity(
    *,
    sql_snowflake: str,
    sql_databricks: Optional[str] = None,
    project_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    snowflake_account_id: Optional[str] = None,
    databricks_account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the query across configured Snowflake & Databricks targets and compare results side by side."""
    from semabridge.api.routers.ai_router import ExecuteQueryRequest, _execute_databricks, _execute_snowflake
    from semabridge.ai.project_connection import _build_from_account
    from semabridge.core.settings import reload_settings

    sf_sql = (sql_snowflake or "").strip().rstrip(";")
    db_sql = ((sql_databricks or sql_snowflake) or "").strip().rstrip(";")

    if not sf_sql:
        return {
            "parity_passed": False,
            "snowflake": {
                "success": False,
                "target": "SNOWFLAKE",
                "error": "No SQL statement provided for Snowflake. Please select a metric or enter a query.",
                "rows": [],
                "row_count": 0,
            },
            "databricks": {
                "success": False,
                "target": "DATABRICKS",
                "error": "No SQL statement provided for Databricks. Please select a metric or enter a query.",
                "rows": [],
                "row_count": 0,
            },
        }

    if snowflake_account_id:
        sf_cfg = _build_from_account(snowflake_account_id, "SNOWFLAKE", reload_settings().snowflake, {})
        if sf_cfg is not None:
            import snowflake.connector
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

            try:
                kw = get_snowflake_connect_kwargs(sf_cfg)
                conn = snowflake.connector.connect(**kw)
                try:
                    cur = conn.cursor()
                    cur.execute(sf_sql)
                    cols = [d[0] for d in cur.description] if cur.description else []
                    raw_rows = cur.fetchall() if cur.description else []
                    rows = [
                        [v if v is None or isinstance(v, (int, float, bool, str)) else str(v) for v in r]
                        for r in (raw_rows or [])
                    ]
                    sf_data = {
                        "success": True,
                        "target": "SNOWFLAKE",
                        "columns": cols,
                        "rows": rows,
                        "row_count": len(rows),
                    }
                finally:
                    conn.close()
            except Exception as exc:
                sf_data = {"success": False, "target": "SNOWFLAKE", "error": str(exc), "rows": [], "row_count": 0}
        else:
            sf_data = _execute_snowflake(ExecuteQueryRequest(sql=sf_sql, target="SNOWFLAKE", project_id=project_id, limit=100), sf_sql, sf_sql, None, owner_id=owner_id).model_dump()
    else:
        sf_data = _execute_snowflake(ExecuteQueryRequest(sql=sf_sql, target="SNOWFLAKE", project_id=project_id, limit=100), sf_sql, sf_sql, None, owner_id=owner_id).model_dump()

    if databricks_account_id:
        db_cfg = _build_from_account(databricks_account_id, "DATABRICKS", reload_settings().databricks, {})
        if db_cfg is not None and getattr(db_cfg, "host", None) and getattr(db_cfg, "warehouse_id", None):
            import requests as _requests
            from semabridge.connectors.factory import make_target_emitter

            try:
                try:
                    token = make_target_emitter("databricks", db_cfg)._resolve_token()
                except Exception as tok_err:
                    token = None
                    tok_msg = str(tok_err)

                if token and isinstance(token, str) and token.strip().startswith("{"):
                    try:
                        import json as _json
                        _b = _json.loads(token)
                        if isinstance(_b, dict):
                            token = _b.get("token") or _b.get("access_token") or token
                    except Exception:
                        pass

                if not token:
                    db_data = {
                        "success": False,
                        "target": "DATABRICKS",
                        "error": f"Databricks token unavailable: {locals().get('tok_msg', 'No valid token found in account credentials.')}",
                        "rows": [],
                        "row_count": 0,
                    }
                else:
                    clean_host = str(db_cfg.host).replace("https://", "").replace("http://", "").rstrip("/")
                    db_payload = {"statement": db_sql, "warehouse_id": db_cfg.warehouse_id, "wait_timeout": "15s"}
                    if getattr(db_cfg, "catalog", None):
                        db_payload["catalog"] = db_cfg.catalog
                    if getattr(db_cfg, "schema_name", None):
                        db_payload["schema"] = db_cfg.schema_name

                    resp = _requests.post(
                        f"https://{clean_host}/api/2.0/sql/statements",
                        json=db_payload,
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        timeout=20,
                    )
                    try:
                        body = resp.json()
                    except Exception:
                        body = {}
                    ok = resp.status_code == 200 and (body.get("status") or {}).get("state") == "SUCCEEDED"
                    cols = [c.get("name") for c in (body.get("manifest") or {}).get("schema", {}).get("columns", [])]
                    rows = (body.get("result") or {}).get("data_array", []) or []
                    err_msg = None
                    if not ok:
                        if resp.status_code == 401:
                            err_msg = "Databricks authentication failed (401): Credential missing or invalid for this account. Please update credentials in Connections."
                        else:
                            err_msg = body.get("message") or (body.get("status") or {}).get("error", {}).get("message") or resp.text[:200]
                    db_data = {
                        "success": ok,
                        "target": "DATABRICKS",
                        "columns": cols,
                        "rows": rows,
                        "row_count": len(rows),
                        "error": err_msg,
                    }
            except Exception as exc:
                db_data = {"success": False, "target": "DATABRICKS", "error": str(exc), "rows": [], "row_count": 0}
        else:
            db_data = _execute_databricks(ExecuteQueryRequest(sql=db_sql, target="DATABRICKS", project_id=project_id, limit=100), db_sql, db_sql, None, owner_id=owner_id).model_dump()
    else:
        db_data = _execute_databricks(ExecuteQueryRequest(sql=db_sql, target="DATABRICKS", project_id=project_id, limit=100), db_sql, db_sql, None, owner_id=owner_id).model_dump()

    def _val_eq(v1: Any, v2: Any) -> bool:
        if v1 == v2:
            return True
        try:
            if v1 is not None and v2 is not None:
                return float(v1) == float(v2)
        except (ValueError, TypeError):
            pass
        return str(v1 or "").strip().casefold() == str(v2 or "").strip().casefold()

    def _rows_eq(r1: List[List[Any]], r2: List[List[Any]]) -> bool:
        if len(r1) != len(r2):
            return False
        for row1, row2 in zip(r1, r2):
            if len(row1) != len(row2):
                return False
            if not all(_val_eq(a, b) for a, b in zip(row1, row2)):
                return False
        return True

    parity_ok = bool(
        sf_data.get("success")
        and db_data.get("success")
        and sf_data.get("row_count") == db_data.get("row_count")
        and _rows_eq(sf_data.get("rows") or [], db_data.get("rows") or [])
    )
    return {
        "parity_passed": parity_ok,
        "snowflake": sf_data,
        "databricks": db_data,
    }

