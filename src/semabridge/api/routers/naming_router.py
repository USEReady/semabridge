"""Naming preview: the name a field deploys under, from the one naming rule.

``GET /api/naming/preview?name=Date&kind=column&target=snowflake`` returns
``{"name": "DATE"}``. Read-only; authenticated by the global AuthMiddleware
like the other read endpoints.
"""

from __future__ import annotations

from typing import Dict, Literal

from fastapi import APIRouter, Query

from semabridge.utils.identifiers import databricks_target_name, snowflake_target_name

router = APIRouter(prefix="/api/naming", tags=["naming"])


@router.get("/preview")
def preview_name(
    name: str = Query(..., min_length=1, max_length=1024),
    kind: Literal["column", "metric"] = Query("column"),
    target: Literal["snowflake", "databricks"] = Query("snowflake"),
) -> Dict[str, str]:
    """Deployed target name for *name* using the single shared naming rule.

    - ``target="snowflake"``: columns (dimensions / facts) use the semantic-alias
      rule ('Date' -> DATE), metrics use to_alias ('Date' -> COL_DATE).
    - ``target="databricks"``: matches DatabricksPublisher's identifier rules
      ('Sales %' -> 'Sales' for columns, 'Sales_Pct' for metrics).
    """
    if target == "databricks":
        return {"name": databricks_target_name(name, entity_kind=kind)}
    return {"name": snowflake_target_name(name, entity_kind=kind)}
