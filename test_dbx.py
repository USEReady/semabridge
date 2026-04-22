import logging
logging.basicConfig(level=logging.DEBUG)
from semabridge.repository.orm.session_factory import db_manager
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.auth.credential_builder import build_databricks_config
from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.core.settings import get_settings

config = get_settings()
e = ExecutionEngine()
acc = e._resolve_identity('DATABRICKS', 'jhgfd', 'default')

with db_manager.get_session() as s:
    cfg = build_databricks_config(acc, s, config.databricks)

p = DatabricksPublisher(cfg)
print(f"Testing against Databricks API: {cfg.api_base_url}/api/2.0/sql/statements")
print(f"Warehouse ID: {cfg.warehouse_id}")

payload = {
    "statement": "SELECT 1",
    "warehouse_id": cfg.warehouse_id,
}

resp = p.session.post(f"{cfg.api_base_url}/api/2.0/sql/statements", headers=p._headers(), json=payload)
print(f"Status: {resp.status_code}")
print(f"Body: {resp.text}")

payload_schema = {
    "statement": "CREATE SCHEMA IF NOT EXISTS `semabridge`.`public`",
    "warehouse_id": cfg.warehouse_id,
    "catalog": "semabridge",
    "schema": "public"
}
resp2 = p.session.post(f"{cfg.api_base_url}/api/2.0/sql/statements", headers=p._headers(), json=payload_schema)
print(f"Status Schema: {resp2.status_code}")
print(f"Body Schema: {resp2.text}")
