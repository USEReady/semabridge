import logging
import json
import requests
logging.basicConfig(level=logging.DEBUG)
from semabridge.repository.orm.session_factory import db_manager
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.auth.credential_builder import _load_credential_env_map

e = ExecutionEngine()
acc = e._resolve_identity('DATABRICKS', 'jhgfd', 'default')

with db_manager.get_session() as s:
    env_map = _load_credential_env_map(acc, s)

host = env_map.get("DATABRICKS_HOST", "")
token = env_map.get("DATABRICKS_TOKEN", "")
warehouse_id = env_map.get("DATABRICKS_WAREHOUSE_ID", "")

print(f"Host: {host}")
print(f"WH: {warehouse_id}")

endpoint = f"https://{host}/api/2.0/sql/statements"
headers = {"Authorization": f"Bearer {token}"}

payload1 = {
    "statement": "SELECT 1",
    "warehouse_id": warehouse_id
}
resp1 = requests.post(endpoint, headers=headers, json=payload1)
print(f"SELECT 1: {resp1.status_code}")
print(resp1.text)

payload2 = {
    "statement": "CREATE SCHEMA IF NOT EXISTS `semabridge`.`public`",
    "warehouse_id": warehouse_id,
    "catalog": "semabridge",
    "schema": "public"
}
resp2 = requests.post(endpoint, headers=headers, json=payload2)
print(f"CREATE SCHEMA: {resp2.status_code}")
print(resp2.text)
