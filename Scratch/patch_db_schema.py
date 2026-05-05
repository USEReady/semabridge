"""One-time DDL patch script to add missing columns to the database."""
import psycopg2

DB_URL = "postgresql://postgres:1234@localhost:5432/mydb"

# Parse connection params from URL
# postgresql://user:password@host:port/dbname
import re
m = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', DB_URL)
user, password, host, port, dbname = m.groups()

conn = psycopg2.connect(host=host, port=int(port), dbname=dbname, user=user, password=password)
conn.autocommit = True
cur = conn.cursor()

statements = [
    # runs table
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS sync_mode VARCHAR(20) DEFAULT 'copy' NOT NULL",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS restored_from_snapshot_id VARCHAR(36)",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS before_src_snapshot_id VARCHAR(36)",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS before_target_snapshot_ids TEXT",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS after_target_snapshot_ids TEXT",
    # snapshots table
    "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS connector_id VARCHAR(36)",
    "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS trigger VARCHAR(50)",
    "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
]

for sql in statements:
    try:
        cur.execute(sql)
        print(f"OK: {sql[:60]}...")
    except Exception as e:
        print(f"SKIP/ERROR: {e}")

cur.close()
conn.close()
print("\nDone! Schema patch complete.")
