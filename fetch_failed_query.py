import snowflake.connector
import os
from dotenv import load_dotenv

load_dotenv()

conn = snowflake.connector.connect(
    user=os.getenv("SNOWFLAKE_USER"),
    password=os.getenv("SNOWFLAKE_PASSWORD"),
    account=os.getenv("SNOWFLAKE_ACCOUNT"),
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
    database=os.getenv("SNOWFLAKE_DATABASE"),
    schema=os.getenv("SNOWFLAKE_SCHEMA"),
    role=os.getenv("SNOWFLAKE_ROLE")
)

cur = conn.cursor()
cur.execute("SELECT QUERY_TEXT, ERROR_MESSAGE FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY()) WHERE EXECUTION_STATUS = 'FAIL' ORDER BY START_TIME DESC LIMIT 1")
row = cur.fetchone()
if row:
    with open("failed_query.sql", "w", encoding="utf-8") as f:
        f.write(row[0])
    print(f"Error: {row[1]}")
    print("Wrote failed_query.sql")
else:
    print("No failed queries found.")
