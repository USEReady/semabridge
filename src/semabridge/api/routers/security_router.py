"""REST API Router for Bidirectional Security & RLS Policy Translation."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
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
    role: Optional[TMDLSecurityRole] = None
    roles: Optional[List[TMDLSecurityRole]] = None
    target_database: str = "PROD_DWH"
    target_schema: str = "ANALYTICS"


class SnowflakeToTMDLRequest(BaseModel):
    policy: SnowflakeRowAccessPolicy


@router.post("/tmdl-to-snowflake", response_model=Dict[str, Any])
def convert_tmdl_role_to_snowflake(req: TMDLToSnowflakeRequest) -> Dict[str, Any]:
    """Convert Power BI / Fabric TMDL DAX role(s) into Snowflake ROW ACCESS POLICY DDL."""
    roles_list: List[TMDLSecurityRole] = []
    if req.roles:
        roles_list.extend(req.roles)
    if req.role is not None:
        roles_list.append(req.role)

    if not roles_list:
        raise HTTPException(status_code=400, detail="Either 'role' or non-empty 'roles' is required.")  # hardcoding-ok: API field name
    if not (req.target_database or "").strip() or not (req.target_schema or "").strip():
        raise HTTPException(status_code=400, detail="target_database and target_schema must not be blank.")

    for r in roles_list:
        if not (r.name or "").strip():
            raise HTTPException(status_code=400, detail="Role name must not be blank.")  # hardcoding-ok: RBAC term
        if not r.table_permissions:
            raise HTTPException(
                status_code=400,
                detail=f"Role '{r.name}' must define at least one table_permission.",  # hardcoding-ok: RBAC term
            )

    translator = SecurityPolicyTranslator()
    return translator.translate_tmdl_roles_to_snowflake(
        roles_list,
        target_database=req.target_database,
        target_schema=req.target_schema,
    )


@router.post("/snowflake-to-tmdl", response_model=Dict[str, Any])
def convert_snowflake_policy_to_tmdl(req: SnowflakeToTMDLRequest) -> Dict[str, Any]:
    """Convert Snowflake ROW ACCESS POLICY into Microsoft Fabric TMDL role syntax."""
    if not (req.policy.target_table or "").strip():
        raise HTTPException(status_code=400, detail="policy.target_table must not be blank.")
    translator = SecurityPolicyTranslator()
    return translator.translate_snowflake_policy_to_tmdl(req.policy)
