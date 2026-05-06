import sqlalchemy as sa
from semabridge.repository.orm.database import db_manager

def run_migration():
    print("Starting manual schema update...")
    engine = db_manager._engine()
    with engine.connect() as conn:
        # Check snapshots.deleted_at
        try:
            conn.execute(sa.text("ALTER TABLE snapshots ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE"))
            print("Added snapshots.deleted_at")
        except Exception as e:
            print(f"Skipped snapshots.deleted_at (probably exists): {e}")

        # Check runs.run_type
        try:
            conn.execute(sa.text("ALTER TABLE runs ADD COLUMN run_type VARCHAR(50)"))
            print("Added runs.run_type")
        except Exception as e:
            print(f"Skipped runs.run_type: {e}")

        # Check runs.before_src_snapshot_id
        try:
            conn.execute(sa.text("ALTER TABLE runs ADD COLUMN before_src_snapshot_id VARCHAR(36)"))
            print("Added runs.before_src_snapshot_id")
        except Exception as e:
            print(f"Skipped runs.before_src_snapshot_id: {e}")

        # Check runs.restore_snapshot_id
        try:
            conn.execute(sa.text("ALTER TABLE runs ADD COLUMN restore_snapshot_id VARCHAR(36)"))
            print("Added runs.restore_snapshot_id")
        except Exception as e:
            print(f"Skipped runs.restore_snapshot_id: {e}")

        # Check runs.before_target_snapshot_ids
        try:
            conn.execute(sa.text("ALTER TABLE runs ADD COLUMN before_target_snapshot_ids TEXT"))
            print("Added runs.before_target_snapshot_ids")
        except Exception as e:
            print(f"Skipped runs.before_target_snapshot_ids: {e}")

        # Check runs.after_target_snapshot_ids
        try:
            conn.execute(sa.text("ALTER TABLE runs ADD COLUMN after_target_snapshot_ids TEXT"))
            print("Added runs.after_target_snapshot_ids")
        except Exception as e:
            print(f"Skipped runs.after_target_snapshot_ids: {e}")

        conn.commit()
    print("Manual schema update complete.")

if __name__ == "__main__":
    run_migration()
