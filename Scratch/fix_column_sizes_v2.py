import os
import sys

# Add src to path
sys.path.insert(0, "src")

from dotenv import load_dotenv
load_dotenv(".env")

from sqlalchemy import create_engine, text

db_url = os.getenv("SEMABRIDGE_DATABASE_URL", "postgresql://postgres:postgres@localhost/semabridge")
print(f"Connecting to: {db_url}")

engine = create_engine(db_url)

commands = [
    # Projects
    "ALTER TABLE projects ALTER COLUMN project_id TYPE VARCHAR(255);",
    
    # Sub objects of projects usually have project_id
    "ALTER TABLE runs ALTER COLUMN project_id TYPE VARCHAR(255);",
    "ALTER TABLE snapshots ALTER COLUMN project_id TYPE VARCHAR(255);",
    "ALTER TABLE run_metrics ALTER COLUMN project_id TYPE VARCHAR(255);",
    "ALTER TABLE run_artifacts ALTER COLUMN project_id TYPE VARCHAR(255);",
    
    # Other IDs
    "ALTER TABLE runs ALTER COLUMN run_id TYPE VARCHAR(255);",
    "ALTER TABLE snapshots ALTER COLUMN snapshot_id TYPE VARCHAR(255);",
    "ALTER TABLE accounts ALTER COLUMN id TYPE VARCHAR(255);",
    "ALTER TABLE workspaces ALTER COLUMN id TYPE VARCHAR(255);",
]

for cmd in commands:
    try:
        with engine.begin() as conn:
            conn.execute(text(cmd))
        print(f"Success: {cmd}")
    except Exception as e:
        print(f"Failed or skipped: {cmd} - {e}")
