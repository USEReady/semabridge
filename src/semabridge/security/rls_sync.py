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


def _parse_single_comparison(expr: str) -> tuple[bool, Optional[str], Optional[tuple[str, str, str]]]:
    """Parse a single DAX column comparison (no AND/OR).

    Returns: (can_translate, reason_if_not, (col_name, op, val_sql))
    """
    expr = expr.strip()
    if not expr:
        return False, "DAX filter expression is empty", None

    # Check for unsupported function calls
    fn_match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", expr)
    if fn_match:
        fn_name = fn_match.group(1).upper()
        return False, f"uses unsupported function {fn_name}()", None

    # Match simple column comparison
    m = re.match(
        r"^\s*(?:(?:'?[A-Za-z0-9_\s]+'?\s*)?\[)?\s*([A-Za-z0-9_]+)\s*\]?\s*(==?|<>|!=|>=|<=|>|<)\s*(.+)$",
        expr,
    )
    if not m:
        return False, f"not a simple column comparison: '{expr}'", None

    col, op, val = m.group(1).upper(), m.group(2), m.group(3).strip()
    if op == "==":
        op = "="
    if val.startswith('"') and val.endswith('"'):
        val_clean = "'" + val[1:-1].replace("'", "''") + "'"
    elif val.startswith("'") and val.endswith("'"):
        val_clean = val
    else:
        val_clean = val
    return True, None, (col, op, val_clean)


def can_translate_dax_filter(
    dax_expr: str,
) -> tuple[bool, Optional[str], Optional[tuple[str, str, str]], Optional[List[tuple[str, str, str, str]]]]:
    """Check if a DAX filter is translatable to SQL.

    Supports single comparisons and AND (``&&``) / OR (``||``) combinations of
    simple column comparisons.

    Returns
    -------
    (can_translate, reason_if_not, single_parsed, compound_parts)
        *single_parsed* is ``(col, op, val)`` when the expression is a single
        comparison (backward-compatible).  *compound_parts* is a list of
        ``(connector, col, op, val)`` tuples when the expression contains
        ``&&`` / ``||``; the first tuple's connector is always ``""``.
    """
    expr = (dax_expr or "").strip()
    if not expr:
        return False, "DAX filter expression is empty", None, None

    # Split on && / || (DAX logical operators).
    parts: List[tuple[str, str]] = []  # (connector, sub_expr)
    # Regex splits while preserving the operator token.
    tokens = re.split(r"\s*(&&|\|\|)\s*", expr)
    if len(tokens) == 1:
        # Single comparison — delegate to legacy parser.
        ok, reason, parsed = _parse_single_comparison(expr)
        return ok, reason, parsed, None

    # Compound expression
    connector = ""
    compound: List[tuple[str, str, str, str]] = []
    for token in tokens:
        if token == "&&":
            connector = "AND"
            continue
        elif token == "||":
            connector = "OR"
            continue
        ok, reason, parsed = _parse_single_comparison(token)
        if not ok:
            return False, reason, None, None
        col, op, val = parsed  # type: ignore[misc]
        compound.append((connector, col, op, val))
        connector = ""

    return True, None, None, compound


class SecurityPolicyTranslator:
    """Translates row-level security and masking policies bidirectionally."""

    @classmethod
    def translate_tmdl_role(
        cls,
        role: TMDLSecurityRole,
        target_database: str,
        target_schema: str = "ANALYTICS",
    ) -> Dict[str, Any]:
        """
        Translate a TMDL role. If any permission uses unsupported functions
        (USERPRINCIPALNAME, RELATED, etc.), returns status='not translated' with reason,
        never as a policy.
        """
        if not target_database or not target_database.strip():
            raise ValueError("target_database is required (no default database).")

        for perm in role.table_permissions:
            can_trans, reason, _, _ = can_translate_dax_filter(perm.dax_filter_expression)
            if not can_trans:
                return {
                    "status": "not translated",
                    "reason": f"Role '{role.name}' {reason}. Only simple column comparisons are supported.",
                    "ddl": "",
                }

        ddl = cls.tmdl_role_to_snowflake_ddl(role, target_database=target_database, target_schema=target_schema)
        return {
            "status": "translated",
            "reason": None,
            "ddl": ddl,
        }

    @staticmethod
    def tmdl_role_to_snowflake_ddl(role: TMDLSecurityRole, target_database: str, target_schema: str = "ANALYTICS") -> str:
        """Translate a TMDL DAX security role into Snowflake ROW ACCESS POLICY DDL."""
        if not target_database or not target_database.strip():
            raise ValueError("target_database is required (no default database).")

        clean_role = role.name.upper().replace(" ", "_")
        lines = [
            "-- =============================================================================",
            f"-- Snowflake Row Access Policy for Power BI Role: {role.name}",
            "-- =============================================================================",
        ]

        for perm in role.table_permissions:
            tbl = perm.table_name.upper()
            can_trans, reason, parsed, compound = can_translate_dax_filter(perm.dax_filter_expression)
            if not can_trans:
                raise ValueError(f"Role '{role.name}' {reason}; cannot translate to Snowflake policy.")

            policy_name = f"RAP_{tbl}_{clean_role}"

            if compound:
                # Compound AND/OR expression — multiple columns
                col_defs = []
                seen: set[str] = set()
                for _conn, col, _op, _val in compound:
                    if col not in seen:
                        col_defs.append(f"{col} VARCHAR")
                        seen.add(col)
                as_clause = ", ".join(col_defs)
                sql_parts = []
                for conn, col, op, val in compound:
                    clause = f"{col} {op} {val}"
                    if conn:
                        sql_parts.append(f" {conn} {clause}")
                    else:
                        sql_parts.append(clause)
                sql_expr = "".join(sql_parts)
                on_cols = ", ".join(seen)

                lines.append(f"""CREATE OR REPLACE ROW ACCESS POLICY {target_database}.{target_schema}.{policy_name}
AS ({as_clause}) RETURNS BOOLEAN ->
    CURRENT_ROLE() IN ('ACCOUNTADMIN', 'SECURITYADMIN')
    OR (CURRENT_ROLE() = '{clean_role}' AND {sql_expr});""")

                lines.append(f"""ALTER TABLE {target_database}.{target_schema}.{tbl}
    ADD ROW ACCESS POLICY {target_database}.{target_schema}.{policy_name} ON ({on_cols});""")
            else:
                if not parsed:
                    raise ValueError(f"Role '{role.name}' {reason}; cannot translate to Snowflake policy.")
                col_name, op, val_clean = parsed
                sql_expr = f"{col_name} {op} {val_clean}"

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
        role_name = policy.assigned_roles[0] if policy.assigned_roles else policy.policy_name
        col = policy.column_name

        # Extract literal filter from policy expression if simple
        match = re.search(r"AND\s+([A-Za-z0-9_]+)\s*=\s*'([^']+)'", policy.body_expression, re.IGNORECASE)
        if match:
            dax_filter = f"[{match.group(1)}] = \"{match.group(2)}\""
        else:
            dax_filter = f"[{col}] = USERNAME()"

        lines = [
            f"role '{role_name}'",
            "\tmodelPermission: read",
            "",
            f"\ttablePermission '{policy.target_table}' = {dax_filter}",
            "",
        ]

        if policy.assigned_roles:
            lines.append(f"\tmember '{policy.assigned_roles[0]}' = role")

        return "\n".join(lines)
