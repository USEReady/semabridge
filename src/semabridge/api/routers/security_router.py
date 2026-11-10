"""REST API Router for Bidirectional Security & RLS Policy Translation."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from semabridge.security.rls_sync import (
    SecurityPolicyTranslator,
    SnowflakeRowAccessPolicy,
    TMDLSecurityRole,
)

logger = logging.getLogger("semabridge.api.security_router")

router = APIRouter(prefix="/api/security/rls", tags=["security_rls"])


class TMDLToSnowflakeRequest(BaseModel):
    role: TMDLSecurityRole
    target_database: Optional[str] = None
    target_schema: str = "ANALYTICS"


class TMDLToSnowflakeResponse(BaseModel):
    status: str = "translated"
    reason: Optional[str] = None
    ddl: str = ""


class SnowflakeToTMDLRequest(BaseModel):
    policy: SnowflakeRowAccessPolicy


@router.post("/tmdl-to-snowflake", response_model=Dict[str, Any])
def convert_tmdl_role_to_snowflake(req: TMDLToSnowflakeRequest) -> Dict[str, Any]:
    """Convert Power BI / Fabric TMDL DAX role into Snowflake ROW ACCESS POLICY DDL."""
    target_db = req.target_database
    if not target_db:
        try:
            from semabridge.core.settings import reload_settings
            sf_cfg = reload_settings().snowflake
            target_db = sf_cfg.database
        except Exception:
            target_db = None

    if not target_db:
        raise HTTPException(status_code=400, detail="target_database is required; no default database configured.")

    res = SecurityPolicyTranslator.translate_tmdl_role(
        req.role,
        target_database=target_db,
        target_schema=req.target_schema,
    )
    return res


@router.post("/snowflake-to-tmdl", response_model=Dict[str, str])
def convert_snowflake_policy_to_tmdl(req: SnowflakeToTMDLRequest) -> Dict[str, str]:
    """Convert Snowflake ROW ACCESS POLICY into Microsoft Fabric TMDL role syntax."""
    translator = SecurityPolicyTranslator()
    tmdl = translator.snowflake_policy_to_tmdl(req.policy)
    return {"tmdl_content": tmdl}
