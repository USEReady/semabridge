from semabridge.core.settings import get_settings
from semabridge.core.behavior import ConnectorBehavior
from semabridge.connectors.connection_manager import SnowflakeConnectionManager

def run_workaround():
    config = get_settings().snowflake
    behavior = ConnectorBehavior()
    
    conn_manager = SnowflakeConnectionManager(config=config, behavior=behavior)
    conn, owns_conn = conn_manager.get_connection()
    try:
        cur = conn.cursor()
        sql = """
        CREATE OR REPLACE VIEW "SEMABRIDGE_DB"."PUBLIC"."SEMABRIDGE_PUBLIC_SALESFACT_ENRICHED" AS
        SELECT * FROM "SEMABRIDGE_DB"."PUBLIC"."SALESFACT_ENRICHED";
        """
        cur.execute(sql)
        print("Workaround view created successfully!")
    except Exception as e:
        print(f"Failed to create workaround view: {e}")
    finally:
        if owns_conn:
            conn.close()

if __name__ == "__main__":
    run_workaround()
