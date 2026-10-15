"""Read-only value lookup for a metric an UPSERT run added or modified.

The Runs page's "Show value" button: queries the deployed semantic view once
through SEMANTIC_VIEW(...) with the run's own Snowflake account. Only metrics
listed in that run's delta (with a deployed query target) can be queried, so
no caller-supplied SQL ever reaches Snowflake.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Optional

from semabridge.domain.exceptions import NotFoundError, ValidationError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _find_metric(run: Dict[str, Any], model: str, metric: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    from semabridge.core.upsert_delta import normalize_name

    for result in run.get("results") or []:
        if str(result.get("model") or "") != model:
            continue
        delta = result.get("upsert_delta") or (result.get("summary") or {}).get("upsert_delta") or {}
        bucket = (delta.get("kinds") or {}).get("metrics") or {}
        for item in (bucket.get("added") or []) + (bucket.get("modified") or []):
            if normalize_name(item.get("name")) == normalize_name(metric) and item.get("query"):
                return delta, item
    raise NotFoundError(f"Metric '{metric}' is not an added/modified, deployed metric of '{model}' in this run")


def _query_value(account_id: str, view: Dict[str, str], entity: str, metric: str) -> Any:
    import snowflake.connector

    from semabridge.auth.credential_builder import build_snowflake_config
    from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
    from semabridge.core.settings import get_settings
    from semabridge.repository.account_repository import AccountRepository
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        account = AccountRepository(session).get_account_by_id(account_id)
        if not account:
            raise NotFoundError("The run's Snowflake account no longer exists")
        cfg = build_snowflake_config(
            account, session, get_settings().snowflake,
            project_target={"database": view["database"], "schema_name": view["schema"]},
        )
    sql = (
        f'SELECT {metric} FROM SEMANTIC_VIEW("{view["database"]}"."{view["schema"]}"."{view["name"]}" '
        f"METRICS {entity}.{metric} AS {metric})"
    )
    conn = snowflake.connector.connect(**get_snowflake_connect_kwargs(cfg))
    try:
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def query_upsert_metric_value(project_id: str, run_id: str, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    from semabridge.api.services.run_service import get_project_runs_compat

    model = str((payload or {}).get("model") or "")
    metric = str((payload or {}).get("metric") or "")
    if not model or not metric:
        raise ValidationError("model and metric are required")
    run = next(
        (r for r in await get_project_runs_compat(project_id) if str(r.get("id") or r.get("run_id")) == run_id),
        None,
    )
    if run is None:
        raise NotFoundError("Run not found")
    delta, item = _find_metric(run, model, metric)
    view = delta.get("view") or {}
    query = item["query"]
    parts = [view.get("database"), view.get("schema"), view.get("name"), query.get("entity"), query.get("metric")]
    if not all(p and _IDENT.match(str(p)) for p in parts):
        raise ValidationError("The run did not record a queryable semantic view for this metric")
    if not run.get("account_id"):
        raise ValidationError("The run has no Snowflake account to query with")
    value = await asyncio.to_thread(_query_value, str(run["account_id"]), view, query["entity"], query["metric"])
    if value is not None and not isinstance(value, (int, float, str)):
        value = float(value)
    return {"model": model, "metric": item.get("name"), "value": value}
