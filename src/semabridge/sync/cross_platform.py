"""Cross-Platform Deployment & Multi-Target Parity (Snowflake, Databricks, Fabric)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def preview_cross_platform_definitions(
    *,
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    targets: Optional[List[str]] = None,
    version_tag: str = "v1.0.0",
) -> Dict[str, Any]:
    """Generate platform-specific semantic definitions and flag gaps for Snowflake, Databricks, and Fabric."""
    from semabridge.ontology.store import ontology_store

    selected = [t.lower().strip() for t in (targets or ["snowflake", "databricks", "fabric"])]
    fed = ontology_store.get_federated_ontology()
    doms = [
        d for d in fed.domains.values()
        if (not domain or d.domain == domain)
        and (not project_id or getattr(d, "source_project_id", None) == project_id)
    ]

    metrics = [m for d in doms for m in d.metrics]
    previews: Dict[str, Dict[str, Any]] = {}

    if "snowflake" in selected:
        lines = [f"-- Version: {version_tag} ({datetime.now(timezone.utc).isoformat()})"]
        gaps: List[str] = []
        for m in metrics:
            expr = m.sql_expression or m.dax_expression or ""
            if not m.sql_expression:
                gaps.append(f"{m.name}: missing SQL expression (fallback to DAX)")
            lines.append(f"  {m.name} AS {expr or '0'} COMMENT = '{(m.description or '').replace(chr(39), '')}'")
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
            expr = m.sql_expression or ""
            if not expr:
                gaps.append(f"{m.name}: requires SQL expression for Databricks Metric View")
            lines.append(f"  - name: {m.name}\n    expr: {expr or 'NULL'}")
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
            expr = m.dax_expression or m.sql_expression or "0"
            if not m.dax_expression:
                gaps.append(f"{m.name}: missing DAX expression (using SQL fallback)")
            lines.append(f"measure '{m.name}' = {expr}")
        previews["fabric"] = {
            "version_tag": version_tag,
            "metric_count": len(metrics),
            "gaps": gaps,
            "definition": "\n".join(lines),
        }

    return {
        "project_id": project_id,
        "domain": domain,
        "version_tag": version_tag,
        "targets": selected,
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

    sf_sql = sql_snowflake.strip().rstrip(";")
    db_sql = (sql_databricks or sql_snowflake).strip().rstrip(";")

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
                    rows = [[v if v is None or isinstance(v, (int, float, bool, str)) else str(v) for v in r] for r in cur.fetchall()]
                    sf_data = {"success": True, "target": "SNOWFLAKE", "columns": cols, "rows": rows, "row_count": len(rows)}
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
                token = make_target_emitter("databricks", db_cfg)._resolve_token()
                resp = _requests.post(
                    f"https://{db_cfg.host}/api/2.0/sql/statements",
                    json={"statement": db_sql, "warehouse_id": db_cfg.warehouse_id, "wait_timeout": "15s"},
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    timeout=20,
                )
                body = resp.json() if resp.status_code == 200 else {}
                cols = [c.get("name") for c in (body.get("manifest") or {}).get("schema", {}).get("columns", [])]
                rows = (body.get("result") or {}).get("data_array", []) or []
                ok = resp.status_code == 200 and (body.get("status") or {}).get("state") == "SUCCEEDED"
                db_data = {"success": ok, "target": "DATABRICKS", "columns": cols, "rows": rows, "row_count": len(rows), "error": None if ok else resp.text[:200]}
            except Exception as exc:
                db_data = {"success": False, "target": "DATABRICKS", "error": str(exc), "rows": [], "row_count": 0}
        else:
            db_data = _execute_databricks(ExecuteQueryRequest(sql=db_sql, target="DATABRICKS", project_id=project_id, limit=100), db_sql, db_sql, None, owner_id=owner_id).model_dump()
    else:
        db_data = _execute_databricks(ExecuteQueryRequest(sql=db_sql, target="DATABRICKS", project_id=project_id, limit=100), db_sql, db_sql, None, owner_id=owner_id).model_dump()

    parity_ok = bool(
        sf_data.get("success")
        and db_data.get("success")
        and sf_data.get("row_count") == db_data.get("row_count")
        and sf_data.get("rows") == db_data.get("rows")
    )
    return {
        "parity_passed": parity_ok,
        "snowflake": sf_data,
        "databricks": db_data,
    }

