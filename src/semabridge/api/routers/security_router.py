"""REST API Router for Bidirectional Security & RLS Policy Translation."""

from __future__ import annotations

import logging
from typing import Dict
from fastapi import APIRouter
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
    target_database: str = "PROD_DWH"
    target_schema: str = "ANALYTICS"


class SnowflakeToTMDLRequest(BaseModel):
    policy: SnowflakeRowAccessPolicy


@router.post("/tmdl-to-snowflake", response_model=Dict[str, str])
def convert_tmdl_role_to_snowflake(req: TMDLToSnowflakeRequest) -> Dict[str, str]:
    """Convert Power BI / Fabric TMDL DAX role into Snowflake ROW ACCESS POLICY DDL."""
    translator = SecurityPolicyTranslator()
    ddl = translator.tmdl_role_to_snowflake_ddl(
        req.role,
        target_database=req.target_database,
        target_schema=req.target_schema,
    )
    return {"ddl": ddl}


@router.post("/snowflake-to-tmdl", response_model=Dict[str, str])
def convert_snowflake_policy_to_tmdl(req: SnowflakeToTMDLRequest) -> Dict[str, str]:
    """Convert Snowflake ROW ACCESS POLICY into Microsoft Fabric TMDL role syntax."""
    translator = SecurityPolicyTranslator()
    tmdl = translator.snowflake_policy_to_tmdl(req.policy)
    return {"tmdl_content": tmdl}
