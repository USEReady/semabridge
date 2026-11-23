"""Non-waking connection test and permission audit (CS-10).

Both use only REST metadata endpoints -- never a SQL statement -- so they
work without starting the SQL warehouse:

* check_databricks_connection: token -> current identity
  (GET /api/2.0/preview/scim/v2/Me) -> warehouse (configured, or
  auto-discovered). Reports the warehouse state; does not start it.
* audit_databricks_permissions: for the target catalog and schema, reads
  Unity Catalog effective permissions
  (GET /api/2.1/unity-catalog/effective-permissions/{type}/{name}, which
  includes inherited grants) and ownership (GET .../catalogs/{name},
  .../schemas/{name}); checks USE CATALOG, USE SCHEMA and CREATE TABLE
  (Unity Catalog's privilege for creating tables *and views*, including
  metric views -- there is no separate CREATE VIEW privilege), and prints
  remediation GRANT statements as text (never executed).

Grants to groups the identity belongs to count; group membership comes from
the identity's ``groups``. A privilege that cannot be confirmed is reported
as "unknown", not "missing".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

from semabridge.target_backends.databricks.auth import DatabricksAuthError, DatabricksConnectionInfo
from semabridge.target_backends.databricks.client import DatabricksSqlClient, DatabricksSqlError
from semabridge.target_backends.databricks.warehouses import resolve_warehouse

ME_PATH = "/api/2.0/preview/scim/v2/Me"


def _privilege(name: str) -> str:
    """USE CATALOG / use_catalog / USE-CATALOG -> USE_CATALOG."""
    return str(name or "").strip().upper().replace(" ", "_").replace("-", "_")


def _quote_ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def identity_of(me: Dict[str, Any]) -> Dict[str, Any]:
    principal = me.get("userName") or me.get("applicationId") or me.get("displayName") or ""
    groups = sorted({str(g.get("display")) for g in me.get("groups") or [] if g.get("display")})
    return {"principal": principal, "display_name": me.get("displayName") or principal, "groups": groups}


def check_databricks_connection(info: DatabricksConnectionInfo, *, client: Optional[DatabricksSqlClient] = None) -> Dict[str, Any]:
    """{status: success|failed, message, identity, warehouse, warehouse_source}."""
    try:
        client = client or DatabricksSqlClient(info)
        identity = identity_of(client.get(ME_PATH) or {})
        choice = resolve_warehouse(client, info.warehouse_id)
    except DatabricksAuthError as exc:
        return {"status": "failed", "message": f"Authentication: {exc}"}
    except DatabricksSqlError as exc:
        hint = " (check the token / service principal)" if exc.http_status in (401, 403) else ""
        return {"status": "failed", "message": f"{exc}{hint}"}
    warehouse = choice["warehouse"]
    result: Dict[str, Any] = {
        "identity": identity,
        "warehouse": warehouse.as_dict() if warehouse else None,
        "warehouse_source": choice["source"],
    }
    if warehouse is None:
        result.update(status="failed", message=f"Signed in as {identity['principal']}, but: {choice['message']}")
        return result
    starting = " It will start on the first query." if warehouse.needs_start else ""
    result.update(
        status="success",
        message=(f"Databricks connection successful: signed in as {identity['principal']}; "
                 f"{choice['message']}.{starting}"),
    )
    return result


def _effective(client: DatabricksSqlClient, securable: str, full_name: str) -> Dict[str, Set[str]]:
    """principal -> privileges (direct and inherited) on one securable."""
    out: Dict[str, Set[str]] = {}
    params: Dict[str, Any] = {}
    for _ in range(50):
        body = client.get(
            f"/api/2.1/unity-catalog/effective-permissions/{securable}/{quote(full_name, safe='.')}",
            params=params or None,
        ) or {}
        for assignment in body.get("privilege_assignments") or []:
            privs = out.setdefault(str(assignment.get("principal") or ""), set())
            for p in assignment.get("privileges") or []:
                privs.add(_privilege(p.get("privilege") if isinstance(p, dict) else p))
        token = body.get("next_page_token")
        if not token:
            break
        params = {"page_token": token}
    return out


def _owner(client: DatabricksSqlClient, securable: str, full_name: str) -> Optional[str]:
    path = "catalogs" if securable == "catalog" else "schemas"
    body = client.get(f"/api/2.1/unity-catalog/{path}/{quote(full_name, safe='.')}") or {}
    return body.get("owner")


def audit_databricks_permissions(info: DatabricksConnectionInfo, catalog: Optional[str] = None,
                                 schema: Optional[str] = None, *,
                                 client: Optional[DatabricksSqlClient] = None) -> Dict[str, Any]:
    """Can this identity create metric views in catalog.schema? Read-only."""
    catalog = catalog or info.catalog
    schema = schema or info.schema
    if not catalog or not schema:
        return {"status": "failed", "message": "Catalog and schema are required for the permission audit."}
    try:
        client = client or DatabricksSqlClient(info)
        identity = identity_of(client.get(ME_PATH) or {})
    except (DatabricksAuthError, DatabricksSqlError) as exc:
        return {"status": "failed", "message": f"Could not read the current identity: {exc}"}

    principal = identity["principal"]
    me: Set[str] = {principal, *identity["groups"]}
    schema_full = f"{catalog}.{schema}"
    checks: List[Dict[str, Any]] = []

    def held(grants: Dict[str, Set[str]], wanted: str) -> bool:
        return any(wanted in privs or "ALL_PRIVILEGES" in privs for who, privs in grants.items() if who in me)

    def check(securable: str, full_name: str, wanted: List[str]) -> None:
        try:
            owner = _owner(client, securable, full_name)
        except DatabricksSqlError as exc:
            exists = exc.http_status != 404
            for w in wanted:
                checks.append({"securable": securable, "name": full_name, "privilege": w.replace("_", " "),
                               "status": "missing" if not exists else "unknown",
                               "detail": "does not exist" if not exists else str(exc)})
            return
        owns = bool(owner) and owner in me
        try:
            grants = _effective(client, securable, full_name)
        except DatabricksSqlError as exc:
            grants, error = {}, str(exc)
        else:
            error = None
        for w in wanted:
            if owns:
                status, detail = "granted", f"owner ({owner})"
            elif held(grants, w):
                status, detail = "granted", "granted directly or inherited"
            elif error:
                status, detail = "unknown", error
            else:
                status, detail = "missing", "not granted to you or your groups"
            checks.append({"securable": securable, "name": full_name, "privilege": w.replace("_", " "),
                           "status": status, "detail": detail})

    check("catalog", catalog, ["USE_CATALOG"])
    check("schema", schema_full, ["USE_SCHEMA", "CREATE_TABLE"])

    grantee = _quote_ident(principal)
    remediation: List[str] = []
    for c in checks:
        if c["status"] != "missing":
            continue
        if c["detail"] == "does not exist" and c["securable"] == "schema":
            stmt = f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(catalog)}.{_quote_ident(schema)};"
        else:
            target = (f"CATALOG {_quote_ident(catalog)}" if c["securable"] == "catalog"
                      else f"SCHEMA {_quote_ident(catalog)}.{_quote_ident(schema)}")
            stmt = f"GRANT {c['privilege']} ON {target} TO {grantee};"
        if stmt not in remediation:
            remediation.append(stmt)

    missing = [c for c in checks if c["status"] == "missing"]
    unknown = [c for c in checks if c["status"] == "unknown"]
    status = "ok" if not missing and not unknown else ("missing" if missing else "unknown")
    message = {
        "ok": f"{principal} can create metric views in {catalog}.{schema}.",
        "missing": f"{principal} is missing {len(missing)} privilege(s) in {catalog}.{schema}; "
                   "an admin can run the remediation SQL.",
        "unknown": f"Some privileges for {principal} could not be confirmed; see details.",
    }[status]
    return {
        "status": status,
        "message": message,
        "identity": identity,
        "catalog": catalog,
        "schema": schema,
        "checks": checks,
        "remediation_sql": remediation,
        "note": "CREATE TABLE is Unity Catalog's privilege for creating views and metric views (there is no CREATE VIEW privilege).",
    }
