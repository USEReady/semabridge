"""Read-only live check of the Phase 3.3 Databricks client against your workspace.

Uses the DATABRICKS_* settings from .env (host, token or service principal,
optional warehouse ID, catalog, schema). Prints no secrets.

    uv run python Scripts/databricks_live_check.py [catalog] [schema] [test_schema]

Defaults: catalog/schema from .env, test schema "sb_tests".

Steps (nothing is created or changed):
  1. connection test  -- REST only, does not start the warehouse;
  2. permission audit -- REST only, prints GRANT statements if anything is missing;
  3. guard check      -- a DROP TABLE is refused before anything is sent;
  4. SQL              -- SELECT 1, SHOW TABLES IN <catalog>.<test_schema>, DESCRIBE the
                         first table. These run on the warehouse (it starts if stopped).
"""

from __future__ import annotations

import json
import sys

from semabridge.core.settings import DatabricksConfig
from semabridge.target_backends.databricks.auth import DatabricksConnectionInfo
from semabridge.target_backends.databricks.client import DatabricksSqlClient, DatabricksSqlError
from semabridge.target_backends.databricks.connection import audit_databricks_permissions, check_databricks_connection
from semabridge.target_backends.databricks.guard import DatabricksGuardError
from semabridge.target_backends.databricks.warehouses import resolve_warehouse


def _show(title: str, value) -> None:
    print(f"\n== {title}")
    print(json.dumps(value, indent=2, default=str) if not isinstance(value, str) else value)


def main() -> int:
    args = sys.argv[1:]
    try:
        config = DatabricksConfig()
        info = DatabricksConnectionInfo.from_config(config)
    except Exception:
        # warehouse_id is required by DatabricksConfig; fall back to the environment
        from dotenv import dotenv_values

        info = DatabricksConnectionInfo.from_env({k: v for k, v in dotenv_values(".env").items() if v})
    catalog = args[0] if len(args) > 0 else (info.catalog or "workspace")
    schema = args[1] if len(args) > 1 else (info.schema or "semabridge_dev")
    test_schema = args[2] if len(args) > 2 else "sb_tests"
    print(f"Host: {info.host}  auth: {info.auth_type}  catalog.schema: {catalog}.{schema}")

    _show("1. Connection test (REST only)", check_databricks_connection(info))
    _show("2. Permission audit (REST only)", audit_databricks_permissions(info, catalog, schema))

    client = DatabricksSqlClient(info, query_tags={"purpose": "live_check"})
    try:
        client.execute(f"DROP TABLE {catalog}.{test_schema}.sales")
        _show("3. Guard", "ERROR: the DROP was allowed")
        return 1
    except DatabricksGuardError as exc:
        _show("3. Guard (nothing sent)", f"refused as expected: {exc}")

    if not client.warehouse_id:
        choice = resolve_warehouse(client)
        if choice["warehouse"] is None:
            _show("4. SQL", f"skipped: {choice['message']}")
            return 1
        client.warehouse_id = choice["warehouse"].id
    try:
        _show("4a. SELECT 1", client.execute("SELECT 1 AS one").rows)
        tables = client.execute(f"SHOW TABLES IN `{catalog}`.`{test_schema}`").rows
        _show(f"4b. Tables in {catalog}.{test_schema}", [t.get("tableName") for t in tables])
        if tables:
            first = tables[0].get("tableName")
            cols = client.execute(f"DESCRIBE TABLE `{catalog}`.`{test_schema}`.`{first}`").rows
            _show(f"4c. Columns of {first}", [(c.get("col_name"), c.get("data_type")) for c in cols])
    except DatabricksSqlError as exc:
        _show("4. SQL failed", f"{exc} (statement {exc.statement_id}, code {exc.error_code}, sqlstate {exc.sql_state})")
        return 1
    print("\nDone: read-only, nothing created or changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
