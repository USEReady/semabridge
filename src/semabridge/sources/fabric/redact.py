"""Redact connection details from a TMSL model before it is written anywhere.

Power Query (M) partition / shared expressions name servers, databases,
warehouses and file paths as string arguments of connector calls
(``Snowflake.Databases("acct.snowflakecomputing.com", "WH")``,
``Sql.Database("srv", "db")``, ``Value.NativeQuery(src, "SELECT ...")``).
Legacy data sources carry a ``connectionString``. All of those become
``"<redacted>"``; model structure (tables, columns, measures, navigation
steps) is kept so the dump is still useful for debugging.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict

REDACTED = "<redacted>"
_CONNECTOR_CALL = re.compile(r"\b[A-Z][A-Za-z0-9]*\.[A-Z][A-Za-z0-9]*\s*\(")
_M_STRING = re.compile(r'"(?:[^"]|"")*"')
_SECRET_KEYS = {"connectionstring", "password", "accesstoken", "account", "server", "url"}


def _redact_call_args(expr: str) -> str:
    """Replace string literals inside the argument list of every Connector.Function( ... ) call."""
    out, i = [], 0
    for m in _CONNECTOR_CALL.finditer(expr):
        if m.start() < i:
            continue
        out.append(expr[i:m.end()])
        depth, j, in_str = 1, m.end(), False
        while j < len(expr) and depth:
            ch = expr[j]
            if ch == '"':
                in_str = not in_str
            elif not in_str and ch == "(":
                depth += 1
            elif not in_str and ch == ")":
                depth -= 1
            j += 1
        args = expr[m.end():j - 1] if depth == 0 else expr[m.end():j]
        out.append(_M_STRING.sub(f'"{REDACTED}"', args))
        out.append(")" if depth == 0 else "")
        i = j
    out.append(expr[i:])
    return "".join(out)


def redact_m_expression(expr: Any) -> Any:
    if isinstance(expr, list):
        return [_redact_call_args(str(line)) for line in _join_and_split(expr)]
    if isinstance(expr, str):
        return _redact_call_args(expr)
    return expr


def _join_and_split(lines: list) -> list:
    # Calls can span lines; redact on the joined text, then split back.
    return _redact_call_args("\n".join(str(x) for x in lines)).split("\n") if lines else lines


def redact_tmsl(tmsl: Dict[str, Any]) -> Dict[str, Any]:
    """A deep copy of ``tmsl`` with M connector arguments and connection secrets redacted."""
    data = copy.deepcopy(tmsl)
    model = data.get("model", data) if isinstance(data, dict) else {}
    for table in model.get("tables") or []:
        for part in table.get("partitions") or []:
            src = part.get("source") or {}
            if "expression" in src:
                src["expression"] = (
                    _join_and_split(src["expression"]) if isinstance(src["expression"], list) else _redact_call_args(str(src["expression"]))
                )
            if "query" in src:
                src["query"] = REDACTED
    for shared in model.get("expressions") or []:
        if "expression" in shared:
            shared["expression"] = (
                _join_and_split(shared["expression"]) if isinstance(shared["expression"], list) else _redact_call_args(str(shared["expression"]))
            )
    for ds in model.get("dataSources") or []:
        for key in list(ds.keys()):
            if key.casefold() in _SECRET_KEYS:
                ds[key] = REDACTED
        conn = ds.get("connectionDetails")
        if isinstance(conn, dict):
            ds["connectionDetails"] = REDACTED
        cred = ds.get("credential")
        if cred is not None:
            ds["credential"] = REDACTED
    return data
