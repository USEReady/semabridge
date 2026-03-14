
import os
from pathlib import Path
from semabridge.core.settings import get_settings
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor

def test():
    try:
        settings = get_settings()
        print(f"Testing Snowflake account: {settings.snowflake.account}")
        extractor = SnowflakeExtractor(settings.snowflake)
        with extractor.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()")
            db, schema = cur.fetchone()
            print(f"Connected to DB: {db}, Schema: {schema}")
            
            cur.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s", (schema,))
            count = cur.fetchone()[0]
            print(f"Tables found in {schema}: {count}")
            
            if count == 0:
                print("Checking all schemas...")
                cur.execute("SELECT DISTINCT TABLE_SCHEMA FROM INFORMATION_SCHEMA.TABLES")
                schemas = [r[0] for r in cur.fetchall()]
                print(f"Available schemas: {schemas}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
