import os
import sys
from semabridge.core.settings import get_settings
from semabridge.connectors.connection_manager import SnowflakeConnectionManager
from semabridge.core.behavior import ConnectorBehavior

def check_objects():
    settings = get_settings()
    config = settings.snowflake
    behavior = ConnectorBehavior()
    conn_mgr = SnowflakeConnectionManager(config, behavior)
    
    conn, owns = conn_mgr.get_connection()
    try:
        cur = conn.cursor()
        print("Executing: SHOW OBJECTS IN SCHEMA SEMABRIDGE_DB.PUBLIC")
        cur.execute("SHOW OBJECTS IN SCHEMA SEMABRIDGE_DB.PUBLIC")
        objects = cur.fetchall()
        for obj in objects:
            print(f"- {obj[1]} (Type: {obj[2]})")
            
        print("\nExecuting: SHOW OBJECTS LIKE 'SEMABRIDGE_PUBLIC_SALESFACT_ENRICHED'")
        cur.execute("SHOW OBJECTS LIKE 'SEMABRIDGE_PUBLIC_SALESFACT_ENRICHED'")
        objects = cur.fetchall()
        for obj in objects:
            print(f"- Match: {obj[1]} (Type: {obj[2]}, DB: {obj[3]}, Schema: {obj[4]})")
    finally:
        if owns:
            conn.close()

if __name__ == "__main__":
    check_objects()
