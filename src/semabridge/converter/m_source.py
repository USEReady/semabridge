"""Power Query M source parsing and binding hints.

Extracts Snowflake database, schema, table, and native SQL from Power Query (M)
partition expressions.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _extract_expression_text(partitions: Any) -> str:
    """Flatten partition expressions into a single text block."""
    if not partitions:
        return ""

    parts: List[Any] = partitions if isinstance(partitions, list) else [partitions]
    lines: List[str] = []

    for p in parts:
        if isinstance(p, dict):
            source = p.get("source") or {}
            expr = None
            if isinstance(source, dict):
                expr = source.get("expression")
            if not expr:
                expr = p.get("expression")
            if expr:
                if isinstance(expr, list):
                    lines.extend(str(x) for x in expr)
                else:
                    lines.append(str(expr))
        elif isinstance(p, list):
            lines.extend(str(x) for x in p)
        elif isinstance(p, str):
            lines.append(p)

    return "\n".join(lines)


def _parse_native_sql_table(sql: str) -> Dict[str, Optional[str]]:
    """Extract candidate table, schema, and database from native SQL query."""
    result: Dict[str, Optional[str]] = {"database": None, "schema": None, "table": None}
    if not sql:
        return result

    m = re.search(r'\bFROM\s+([A-Za-z0-9_$"."]+)', sql, re.IGNORECASE)
    if m:
        raw_target = m.group(1).strip()
        parts = [p.strip('"') for p in raw_target.split(".") if p.strip('"')]
        if len(parts) >= 3:
            result["database"] = parts[0]
            result["schema"] = parts[1]
            result["table"] = parts[2]
        elif len(parts) == 2:
            result["schema"] = parts[0]
            result["table"] = parts[1]
        elif len(parts) == 1:
            result["table"] = parts[0]

    return result


def snowflake_binding_hint(partitions: Any) -> Optional[Dict[str, Any]]:
    """Parse Power Query source partitions to extract Snowflake binding hint.

    Extracts:
    - Snowflake.Databases navigation -> database/schema/table
    - Value.NativeQuery -> native SQL

    Returns None if no Snowflake source is present.
    """
    raw = _extract_expression_text(partitions)
    if not raw or ("Snowflake.Databases" not in raw and "Value.NativeQuery" not in raw):
        return None

    # Determine if it's a Snowflake source
    is_snowflake = "Snowflake.Databases" in raw

    db: Optional[str] = None
    schema: Optional[str] = None
    table: Optional[str] = None
    native_sql: Optional[str] = None

    # 1. Parse Snowflake.Databases navigation steps
    # Pattern: {[Name="...", Kind="Database"]} or {[Name="..."]}
    db_match = re.search(r'\{\s*\[\s*Name\s*=\s*"([^"]+)"\s*(?:,\s*Kind\s*=\s*"Database"\s*)?\]\s*\}', raw, re.IGNORECASE)
    if db_match:
        db = db_match.group(1).strip()

    schema_match = re.search(r'\{\s*\[\s*Name\s*=\s*"([^"]+)"\s*(?:,\s*Kind\s*=\s*"Schema"\s*)?\]\s*\}', raw, re.IGNORECASE)
    if schema_match:
        schema = schema_match.group(1).strip()

    table_match = re.search(r'\{\s*\[\s*Name\s*=\s*"([^"]+)"\s*(?:,\s*Kind\s*=\s*"(?:Table|View)"\s*)?\]\s*\}', raw, re.IGNORECASE)
    if table_match:
        table = table_match.group(1).strip()

    # If Kind was omitted and navigation chained:
    # Source{[Name="DB"]}[Data]{[Name="SCHEMA"]}[Data]{[Name="TABLE"]}[Data]
    if not db or not schema or not table:
        chain_matches = re.findall(r'\{\s*\[\s*Name\s*=\s*"([^"]+)"(?:\s*,\s*Kind\s*=\s*"([^"]+)")?\s*\]\s*\}', raw, re.IGNORECASE)
        for name_val, kind_val in chain_matches:
            kind_lower = (kind_val or "").lower()
            if kind_lower == "database" and not db:
                db = name_val.strip()
            elif kind_lower == "schema" and not schema:
                schema = name_val.strip()
            elif kind_lower in ("table", "view") and not table:
                table = name_val.strip()

        # If kinds weren't specified, use positional order (Database -> Schema -> Table)
        if not db and len(chain_matches) >= 1:
            db = chain_matches[0][0].strip()
        if not schema and len(chain_matches) >= 2:
            schema = chain_matches[1][0].strip()
        if not table and len(chain_matches) >= 3:
            table = chain_matches[2][0].strip()

    # 2. Parse Value.NativeQuery
    # Power Query string escaping uses "" for literal double quote
    nq_match = re.search(r'Value\.NativeQuery\s*\(\s*[^,]+,\s*"((?:""|[^"])+)"', raw, re.IGNORECASE)
    if nq_match:
        raw_sql = nq_match.group(1)
        # Unescape "" to "
        native_sql = raw_sql.replace('""', '"').strip()
        if is_snowflake or "snowflake" in raw.lower():
            sql_info = _parse_native_sql_table(native_sql)
            if not table and sql_info["table"]:
                table = sql_info["table"]
            if not schema and sql_info["schema"]:
                schema = sql_info["schema"]
            if not db and sql_info["database"]:
                db = sql_info["database"]

    if not is_snowflake and not native_sql:
        return None

    return {
        "database": db,
        "schema": schema,
        "table": table,
        "native_sql": native_sql,
    }
