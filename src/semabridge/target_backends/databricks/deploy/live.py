"""Read-only view of the live Databricks state a deploy depends on (Phase 3.8).

Everything here is a read: SQL through the guard in READ mode, plus Unity
Catalog REST GETs. The pre-checks use this interface only, so they can be tested
offline with a fake. 3.8b's live run confirms the exact response shapes
(view_definition for metric views, effective-permissions privilege names).
"""

from __future__ import annotations

import json
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote


class LiveError(RuntimeError):
    pass


class Live:
    """The interface the pre-checks use (implemented by DatabricksLive and test fakes)."""

    def version(self) -> Optional[str]: ...
    def identity(self) -> Optional[str]: ...
    def metastore(self) -> Optional[str]: ...
    def objects(self, catalog: str, schema: str) -> Dict[str, str]: ...          # lower name -> table_type
    def table_info(self, fqn: str) -> Optional[Dict[str, Any]]: ...               # id, owner, properties, view_definition, comment, columns
    def source_tables(self, schemas: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], Dict[str, Any]]: ...
    def can_select(self, fqn: str) -> Tuple[bool, Optional[str]]: ...
    def schema_privileges(self, catalog: str, schema: str, principal: str) -> Optional[FrozenSet[str]]: ...
    def show_create(self, fqn: str) -> Optional[str]: ...
    def has_rows(self, fqn: str) -> Optional[bool]: ...                            # None: could not check
    def run_read(self, sql: str) -> Any: ...                                         # a guarded READ statement
    def show_grants(self, fqn: str) -> Optional[List[Tuple[str, str]]]: ...        # [(principal, privilege)]
    def grants(self, securable: str, full_name: str) -> Optional[List[Dict[str, Any]]]: ...   # direct grants
    def owner(self, securable: str, full_name: str) -> Optional[str]: ...
    def statement_status(self, statement_id: str) -> Optional[str]: ...           # PENDING/RUNNING/SUCCEEDED/...
    def history_status(self, statement_sha256: str, *, since: Any, warehouse_id: Optional[str],
                       user: Optional[str]) -> Optional[str]: ...                 # positive evidence only


def _unquoted(fqn: str) -> str:
    from semabridge.target_backends.databricks.guard import _split_name_parts

    return ".".join(_split_name_parts(fqn))


def _quoted(fqn: str) -> str:
    from semabridge.target_backends.databricks.guard import _split_name_parts
    from semabridge.target_backends.databricks.writer import quote_identifier

    return ".".join(quote_identifier(p) for p in _split_name_parts(fqn))


class DatabricksLive(Live):
    """Live adapter over a READ-mode DatabricksSqlClient."""

    def __init__(self, client: Any) -> None:
        from semabridge.target_backends.databricks.guard import READ

        if getattr(getattr(client, "guard", None), "mode", READ) != READ:
            raise LiveError("the live adapter must use a read-only client")
        self.client = client

    @property
    def warehouse_id(self) -> Optional[str]:
        return getattr(self.client, "warehouse_id", None)

    def _scalar(self, sql: str) -> Optional[str]:
        v = self.client.execute(sql).scalar()
        return None if v is None else str(v)

    def version(self) -> Optional[str]:
        from semabridge.target_backends.databricks.probes.executor import _dbsql_version

        return _dbsql_version(self.client)[0]

    def identity(self) -> Optional[str]:
        return self._scalar("SELECT current_user() AS u")

    def metastore(self) -> Optional[str]:
        return self._scalar("SELECT current_metastore() AS m")

    def objects(self, catalog: str, schema: str) -> Dict[str, str]:
        from semabridge.target_backends.databricks.writer import quote_identifier

        sch = str(schema).lower().replace("'", "''")
        rows = self.client.execute(
            f"SELECT table_name, table_type FROM {quote_identifier(catalog)}.information_schema.tables "
            f"WHERE lower(table_schema) = '{sch}'").rows
        return {str(r.get("table_name") or "").lower(): str(r.get("table_type") or "").upper() for r in rows
                if r.get("table_name")}

    def table_info(self, fqn: str) -> Optional[Dict[str, Any]]:
        name = _unquoted(fqn)
        try:
            data = self.client.get(f"/api/2.1/unity-catalog/tables/{quote(name, safe='')}")
        except Exception as exc:
            text = str(exc).upper()
            if "NOT_FOUND" in text or "DOES_NOT_EXIST" in text or "404" in text:
                return None
            raise LiveError(f"could not read {name}: {exc}") from None
        if not isinstance(data, dict):
            return None
        return {
            "id": data.get("table_id"), "owner": data.get("owner"), "table_type": data.get("table_type"),
            "properties": dict(data.get("properties") or {}), "comment": data.get("comment"),
            "view_definition": data.get("view_definition"),
            "columns": [{"name": c.get("name"), "type": c.get("type_text") or c.get("type_name")}
                        for c in data.get("columns") or []],
        }

    def source_tables(self, schemas: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        from semabridge.target_backends.databricks.tables import read_unity_catalog

        return read_unity_catalog(self.client, list(dict.fromkeys(schemas)))

    def can_select(self, fqn: str) -> Tuple[bool, Optional[str]]:
        try:
            self.client.execute(f"SELECT 1 FROM {_quoted(fqn)} LIMIT 0")
            return True, None
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def schema_privileges(self, catalog: str, schema: str, principal: str) -> Optional[FrozenSet[str]]:
        """Effective privileges of ``principal`` on the schema, including those
        inherited from the catalog (and ownership). None when they can't be read."""
        out: Set[str] = set()
        try:
            for kind, name in (("catalog", catalog), ("schema", f"{catalog}.{schema}")):
                data = self.client.get(f"/api/2.1/unity-catalog/effective-permissions/{kind}/{quote(name, safe='')}",
                                       params={"principal": principal})
                for pa in (data or {}).get("privilege_assignments") or []:
                    for p in pa.get("privileges") or []:
                        priv = p.get("privilege") if isinstance(p, dict) else p
                        if priv:
                            out.add(str(priv).upper())
            for kind, name in (("catalogs", catalog), ("schemas", f"{catalog}.{schema}")):
                data = self.client.get(f"/api/2.1/unity-catalog/{kind}/{quote(name, safe='')}")
                if isinstance(data, dict) and str(data.get("owner") or "").lower() == str(principal).lower():
                    out.add("OWNER")
        except Exception:
            return None
        return frozenset(out)

    def show_create(self, fqn: str) -> Optional[str]:
        try:
            res = self.client.execute(f"SHOW CREATE TABLE {_quoted(fqn)}")
        except Exception:
            return None
        return None if not res.rows else str(next(iter(res.rows[0].values())) or "") or None

    def run_read(self, sql: str) -> Any:
        """A read-only statement (staging validation); raises on failure."""
        return self.client.execute(sql)

    def show_grants(self, fqn: str) -> Optional[List[Tuple[str, str]]]:
        try:
            rows = self.client.execute(f"SHOW GRANTS ON TABLE {_quoted(fqn)}").rows
        except Exception:
            return None
        out = []
        for r in rows:
            low = {str(k).lower(): v for k, v in r.items()}
            principal = low.get("principal") or low.get("grantee")
            priv = low.get("actiontype") or low.get("action_type") or low.get("privilege")
            if principal and priv:
                out.append((str(principal), str(priv).upper()))
        return sorted(out)

    def view_text(self, fqn: str) -> Optional[str]:
        """The full stored definition ("View Text" row of DESCRIBE TABLE EXTENDED)."""
        try:
            rows = self.client.execute(f"DESCRIBE TABLE EXTENDED {_quoted(fqn)}").rows
        except Exception:
            return None
        for r in rows:
            vals = list(r.values())
            if vals and str(vals[0] or "").strip().lower() == "view text":
                return str(vals[1] or "") or None
        return None

    def has_rows(self, fqn: str) -> Optional[bool]:
        try:
            return bool(self.client.execute(f"SELECT 1 FROM {_quoted(fqn)} LIMIT 1").rows)
        except Exception:
            return None

    def grants(self, securable: str, full_name: str) -> Optional[List[Dict[str, Any]]]:
        """Direct grants on a catalog / schema: [{principal, privileges}] (UC permissions API)."""
        try:
            data = self.client.get(f"/api/2.1/unity-catalog/permissions/{securable}/{quote(full_name, safe='')}")
        except Exception:
            return None
        out = []
        for pa in (data or {}).get("privilege_assignments") or []:
            privs = [str(p.get("privilege") if isinstance(p, dict) else p).upper() for p in pa.get("privileges") or []]
            out.append({"principal": str(pa.get("principal") or ""), "privileges": privs})
        return out

    def owner(self, securable: str, full_name: str) -> Optional[str]:
        kind = {"catalog": "catalogs", "schema": "schemas"}[securable]
        try:
            data = self.client.get(f"/api/2.1/unity-catalog/{kind}/{quote(full_name, safe='')}")
        except Exception:
            return None
        return str(data.get("owner")) if isinstance(data, dict) and data.get("owner") else None

    def statement_status(self, statement_id: str) -> Optional[str]:
        try:
            data = self.client.get(f"/api/2.0/sql/statements/{quote(statement_id, safe='')}")
        except Exception:
            return None
        state = ((data or {}).get("status") or {}).get("state") if isinstance(data, dict) else None
        return str(state).upper() if state else None

    def history_status(self, statement_sha256: str, *, since: Any, warehouse_id: Optional[str],
                       user: Optional[str]) -> Optional[str]:
        """Positive evidence only: the status of a query-history entry whose exact text
        hashes to ``statement_sha256`` (run by ``user`` on ``warehouse_id`` after
        ``since``). None when not found or unreadable -- which proves nothing."""
        from semabridge.target_backends.databricks.deploy.statements import statement_hash

        try:
            start_ms = int(since.timestamp() * 1000)
            flt: Dict[str, Any] = {"query_start_time_range": {"start_time_ms": start_ms}}
            if warehouse_id:
                flt["warehouse_ids"] = [warehouse_id]
            data = self.client.get("/api/2.0/sql/history/queries",
                                   params={"filter_by": json.dumps(flt), "max_results": 1000})
        except Exception:
            return None
        for q in (data or {}).get("res") or []:
            if user and str(q.get("user_name") or "").lower() != str(user).lower():
                continue
            if statement_hash(str(q.get("query_text") or "")) == statement_sha256:
                return str(q.get("status") or "").upper() or None
        return None


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
