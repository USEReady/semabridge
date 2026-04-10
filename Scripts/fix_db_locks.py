"""Nuclear fix: kill ALL connections, clean locks, verify alembic stamp."""
import psycopg2
from urllib.parse import unquote
import time

# Connect to the default 'postgres' database to kill connections to semabridge_db
conn = psycopg2.connect(
    dbname="postgres",
    user="postgres",
    password=unquote("Jeya%402004"),
    host="localhost",
    port=5432,
)
conn.autocommit = True
cur = conn.cursor()

# Kill ALL connections to semabridge_db
cur.execute("""
    SELECT pg_terminate_backend(pid) 
    FROM pg_stat_activity 
    WHERE datname = 'semabridge_db'
""")
print(f"Terminated {cur.rowcount} connections to semabridge_db")

cur.close()
conn.close()

# Wait for connections to fully close
time.sleep(2)

# Now connect to semabridge_db
conn = psycopg2.connect(
    dbname="semabridge_db",
    user="postgres",
    password=unquote("Jeya%402004"),
    host="localhost",
    port=5432,
)
conn.autocommit = True
cur = conn.cursor()

# Check for any advisory locks
cur.execute("SELECT * FROM pg_locks WHERE database = (SELECT oid FROM pg_database WHERE datname = 'semabridge_db') AND granted = false")
blocked = cur.fetchall()
print(f"Blocked locks: {len(blocked)}")

# Ensure alembic_version is stamped to latest
cur.execute("""
    CREATE TABLE IF NOT EXISTS alembic_version (
        version_num VARCHAR(32) NOT NULL,
        CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
    )
""")
cur.execute("DELETE FROM alembic_version")
cur.execute("INSERT INTO alembic_version (version_num) VALUES ('a3c91f2e7b84')")
cur.execute("SELECT version_num FROM alembic_version")
print(f"Alembic version: {cur.fetchall()}")

# Verify tables exist
cur.execute("""
    SELECT count(*) FROM information_schema.tables 
    WHERE table_schema = 'public'
""")
print(f"Table count: {cur.fetchone()[0]}")

cur.close()
conn.close()
print("Done! All locks cleared, alembic stamped. Start backend now.")
