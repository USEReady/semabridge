import os
import snowflake.connector
from dotenv import load_dotenv

def main():
    load_dotenv("c:/Users/MANOJ/dev-test/semabridge/.env")
    conn = snowflake.connector.connect(
        user=os.environ.get("SNOWFLAKE_USER"),
        password=os.environ.get("SNOWFLAKE_PASSWORD"),
        account=os.environ.get("SNOWFLAKE_ACCOUNT"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "SEMABRIDGE_DB"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC")
    )
    try:
        cur = conn.cursor()
        cur.execute("SELECT GET_DDL('VIEW', 'SEMABRIDGE_DB.PUBLIC.SALESFACT_ENRICHED')")
        print(cur.fetchone()[0])
    finally:
        conn.close()

if __name__ == "__main__":
    main()
