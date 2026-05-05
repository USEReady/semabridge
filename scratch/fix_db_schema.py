import os
import sys
from sqlalchemy import text

# Add src to path so we can import semabridge
sys.path.append(os.path.join(os.getcwd(), "src"))

from semabridge.repository.orm.session_factory import db_manager

def fix_db():
    engine = db_manager.get_engine()
    print(f"Connected to database dialect: {engine.dialect.name}")
    
    with engine.begin() as conn:
        # Check and add columns to snapshots
        print("Checking snapshots table...")
        try:
            conn.execute(text("ALTER TABLE snapshots ADD COLUMN connector_id VARCHAR(36)"))
            print("Added connector_id to snapshots")
        except Exception as e:
            print(f"Skipped connector_id: {e}")
            
        try:
            conn.execute(text("ALTER TABLE snapshots ADD COLUMN trigger VARCHAR(50)"))
            print("Added trigger to snapshots")
        except Exception as e:
            print(f"Skipped trigger: {e}")

        # Check and add columns to runs
        print("Checking runs table...")
        try:
            conn.execute(text("ALTER TABLE runs ADD COLUMN sync_mode VARCHAR(20) DEFAULT 'copy' NOT NULL"))
            print("Added sync_mode to runs")
        except Exception as e:
            print(f"Skipped sync_mode: {e}")
            
        try:
            conn.execute(text("ALTER TABLE runs ADD COLUMN restored_from_snapshot_id VARCHAR(36)"))
            print("Added restored_from_snapshot_id to runs")
        except Exception as e:
            print(f"Skipped restored_from_snapshot_id: {e}")
            
    print("Database fix complete.")

if __name__ == "__main__":
    fix_db()
