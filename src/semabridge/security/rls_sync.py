"""Bidirectional Row-Level Security (RLS) & Column Masking Policy Translator.

Synchronizes security governance between:
- Snowflake / Databricks Data Layer (ROW ACCESS POLICIES, Tag-Based MASKING)
- BI Layer (Power BI / Fabric TMDL Roles and DAX Table Permissions)

Ensures security policies are declared once and governed everywhere.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("semabridge.security.rls_sync")


class TMDLTablePermission(BaseModel):
    table_name: str
    dax_filter_expression: str


class TMDLSecurityRole(BaseModel):
    name: str
    model_name: Optional[str] = None
    description: Optional[str] = None
    table_permissions: List[TMDLTablePermission] = Field(default_factory=list)
    members: List[str] = Field(default_factory=list)


class SnowflakeRowAccessPolicy(BaseModel):
    policy_name: str
    target_table: str
    column_name: str
    column_type: str = "VARCHAR"
    body_expression: str
    assigned_roles: List[str] = Field(default_factory=list)


class SecurityPolicyTranslator:
    """Translates row-level security and masking policies bidirectionally."""

    @staticmethod
    def tmdl_role_to_snowflake_ddl(role: TMDLSecurityRole, target_database: str = "PROD_DWH", target_schema: str = "ANALYTICS") -> str:
        """Translate a TMDL DAX security role into Snowflake ROW ACCESS POLICY DDL."""
        clean_role = role.name.upper().replace(" ", "_")
        lines = [
            f"-- =============================================================================",
            f"-- Snowflake Row Access Policy for Power BI Role: {role.name}",
            f"-- =============================================================================",
        ]

        for perm in role.table_permissions:
            tbl = perm.table_name.upper()
            dax = perm.dax_filter_expression.strip()

            # Translate DAX equality '[Column] = "Value"' -> 'CURRENT_ROLE() IN (...) AND Column = 'Value''
            sql_expr = dax.replace("[", "").replace("]", "")
            # Replace double quotes with single quotes for SQL
            sql_expr = sql_expr.replace('"', "'")

            policy_name = f"RAP_{tbl}_{clean_role}"
            col_match = re.search(r"^(\w+)\s*=", sql_expr)
            col_name = col_match.group(1).upper() if col_match else "SECURITY_KEY"
            if col_match:
                sql_expr = re.sub(r"^\s*" + re.escape(col_match.group(1)), col_name, sql_expr)

            lines.append(f"""CREATE OR REPLACE ROW ACCESS POLICY {target_database}.{target_schema}.{policy_name}
AS ({col_name} VARCHAR) RETURNS BOOLEAN ->
    CURRENT_ROLE() IN ('ACCOUNTADMIN', 'SECURITYADMIN')
    OR (CURRENT_ROLE() = '{clean_role}' AND {sql_expr});""")

            lines.append(f"""ALTER TABLE {target_database}.{target_schema}.{tbl}
    ADD ROW ACCESS POLICY {target_database}.{target_schema}.{policy_name} ON ({col_name});""")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def snowflake_policy_to_tmdl(policy: SnowflakeRowAccessPolicy) -> str:
        """Translate a Snowflake Row Access Policy into Microsoft Fabric TMDL role syntax."""
        role_name = policy.assigned_roles[0] if policy.assigned_roles else "RestrictedRole"
        col = policy.column_name

        # Extract literal filter from policy expression if simple
        match = re.search(r"AND\s+([A-Za-z0-9_]+)\s*=\s*'([^']+)'", policy.body_expression, re.IGNORECASE)
        if match:
            dax_filter = f"[{match.group(1)}] = \"{match.group(2)}\""
        else:
            dax_filter = f"[{col}] = USERNAME()"

        lines = [
            f"role '{role_name}'",
            f"\tmodelPermission: read",
            "",
            f"\ttablePermission '{policy.target_table}' = {dax_filter}",
            "",
        ]

        if policy.assigned_roles:
            lines.append(f"\tmember '{policy.assigned_roles[0]}' = role")

        return "\n".join(lines)
