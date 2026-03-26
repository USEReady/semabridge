"""
Validate .env connector configuration and optional live connectivity.

Checks:
- Fabric credentials + workspace access
- Snowflake credentials + basic SQL query
- Databricks PAT + SQL warehouse visibility

Usage:
  python scripts/validate_env_connections.py
  python scripts/validate_env_connections.py --no-live
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str


def _load_dotenv_map() -> dict[str, str]:
    env_map: dict[str, str] = {}
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return env_map

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_map[key.strip()] = value.strip().strip('"').strip("'")
    return env_map


ENV_FILE_MAP = _load_dotenv_map()


def _is_guid(value: str) -> bool:
    pattern = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    return bool(re.match(pattern, (value or "").strip()))


def _required_env(name: str) -> Optional[str]:
    value = (os.getenv(name, "") or ENV_FILE_MAP.get(name, "")).strip()
    return value or None


def validate_presence() -> list[CheckResult]:
    checks: list[CheckResult] = []

    required = {
        "fabric": ["FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET", "FABRIC_WORKSPACE_ID"],
        "snowflake": ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_DATABASE", "SNOWFLAKE_SCHEMA"],
        "databricks": ["DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID", "DATABRICKS_CATALOG", "DATABRICKS_SCHEMA"],
    }

    for provider, vars_list in required.items():
        missing = [name for name in vars_list if not _required_env(name)]
        if missing:
            checks.append(CheckResult(provider, False, f"Missing env vars: {', '.join(missing)}"))
        else:
            checks.append(CheckResult(provider, True, "All required env vars present"))

    # Basic format checks
    tenant = _required_env("FABRIC_TENANT_ID")
    ws = _required_env("FABRIC_WORKSPACE_ID")
    if tenant and not _is_guid(tenant):
        checks.append(CheckResult("fabric-format", False, "FABRIC_TENANT_ID is not a GUID"))
    if ws and not _is_guid(ws):
        checks.append(CheckResult("fabric-format", False, "FABRIC_WORKSPACE_ID is not a GUID"))

    dbx_host = _required_env("DATABRICKS_HOST")
    if dbx_host and (dbx_host.startswith("http://") or dbx_host.startswith("https://")):
        checks.append(CheckResult("databricks-format", False, "DATABRICKS_HOST should not include protocol"))

    return checks


def check_fabric_live(timeout: int) -> CheckResult:
    tenant_id = _required_env("FABRIC_TENANT_ID")
    client_id = _required_env("FABRIC_CLIENT_ID")
    client_secret = _required_env("FABRIC_CLIENT_SECRET")
    workspace_id = _required_env("FABRIC_WORKSPACE_ID")

    if not all([tenant_id, client_id, client_secret, workspace_id]):
        return CheckResult("fabric-live", False, "Skipped live check due to missing env vars")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    token_data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://analysis.windows.net/powerbi/api/.default",
    }

    try:
        token_resp = requests.post(token_url, data=token_data, timeout=timeout)
        if token_resp.status_code >= 400:
            return CheckResult("fabric-live", False, f"Token request failed: HTTP {token_resp.status_code}")

        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return CheckResult("fabric-live", False, "Token response missing access_token")

        ws_url = f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}"
        ws_resp = requests.get(
            ws_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=timeout,
        )
        if ws_resp.status_code == 200:
            return CheckResult("fabric-live", True, "Workspace access OK")
        return CheckResult("fabric-live", False, f"Workspace access failed: HTTP {ws_resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("fabric-live", False, f"Live check error: {exc}")


def check_snowflake_live(timeout: int) -> CheckResult:
    try:
        import snowflake.connector
        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
        from semabridge.core.settings import get_settings

        settings = get_settings()
        kwargs = get_snowflake_connect_kwargs(settings.snowflake)
        conn = snowflake.connector.connect(**kwargs)
        try:
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_ACCOUNT(), CURRENT_DATABASE(), CURRENT_SCHEMA()")
            row = cur.fetchone() or ("", "", "")
            account, db, schema = row[0], row[1], row[2]
            return CheckResult("snowflake-live", True, f"Connected: account={account}, db={db}, schema={schema}")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("snowflake-live", False, f"Live check error: {exc}")


def check_databricks_live(timeout: int) -> CheckResult:
    host = _required_env("DATABRICKS_HOST")
    token = _required_env("DATABRICKS_TOKEN")
    warehouse_id = _required_env("DATABRICKS_WAREHOUSE_ID")

    if not all([host, token, warehouse_id]):
        return CheckResult("databricks-live", False, "Skipped live check due to missing env vars")

    host = host.rstrip("/")
    if host.startswith("http://"):
        host = host[len("http://"):]
    if host.startswith("https://"):
        host = host[len("https://"):]

    url = f"https://{host}/api/2.0/sql/warehouses/{warehouse_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            name = resp.json().get("name", "")
            return CheckResult("databricks-live", True, f"Warehouse access OK ({name})")
        return CheckResult("databricks-live", False, f"Warehouse access failed: HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("databricks-live", False, f"Live check error: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate env config and live connections")
    parser.add_argument("--no-live", action="store_true", help="Only validate env presence/format")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds")
    args = parser.parse_args()

    results: list[CheckResult] = []
    results.extend(validate_presence())

    if not args.no_live:
        results.append(check_fabric_live(args.timeout))
        results.append(check_snowflake_live(args.timeout))
        results.append(check_databricks_live(args.timeout))

    any_fail = False
    print("\n=== Env + Connectivity Validation ===")
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        if not r.ok:
            any_fail = True
        print(f"[{status}] {r.name}: {r.message}")

    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
