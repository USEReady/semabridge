"""Naming preview: the name a field deploys under, from the one naming rule.

``GET /api/naming/preview?name=Date&kind=column&target=snowflake`` returns
``{"name": "DATE"}``. Read-only; authenticated by the global AuthMiddleware
like the other read endpoints.
"""

from __future__ import annotations

from typing import Dict, Literal

from fastapi import APIRouter, Query

from semabridge.utils.identifiers import snowflake_target_name

router = APIRouter(prefix="/api/naming", tags=["naming"])


@router.get("/preview")
def preview_name(
    name: str = Query(..., min_length=1, max_length=1024),
    kind: Literal["column", "metric"] = Query("column"),
    target: Literal["snowflake"] = Query("snowflake"),
) -> Dict[str, str]:
    """Deployed Snowflake name for *name*: columns (dimensions / facts) use the
    semantic-alias rule ('Date' -> DATE), metrics use to_alias ('Date' -> COL_DATE)."""
    return {"name": snowflake_target_name(name, entity_kind=kind)}
