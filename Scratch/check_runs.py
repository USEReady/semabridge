import sys
import os
from sqlalchemy import select

# Add src directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from semabridge.repository.orm.session_factory import db_manager
from semabridge.repository.orm.models import Run, SyncJob, Project

def check_runs():
    try:
        with db_manager.get_session() as session:
            # Query the last 10 runs
            stmt = select(Run).order_by(Run.started_at.desc()).limit(10)
            runs = session.execute(stmt).scalars().all()
            
            print(f"Found {len(runs)} recent runs in database:")
            for idx, r in enumerate(runs):
                print(f"{idx+1}. Run ID: {r.run_id}")
                print(f"   Project: {r.project_id}")
                print(f"   Status: {r.status}")
                print(f"   Started: {r.started_at}")
                duration = r.duration_ms / 1000.0 if r.duration_ms is not None else None
                print(f"   Duration: {duration} seconds")
                print(f"   Message: {r.error_message}")
                print(f"   Sync Mode: {r.sync_mode}")
                print("-" * 50)
                
    except Exception as e:
        print("Error connecting/querying DB:", e)

if __name__ == "__main__":
    check_runs()
