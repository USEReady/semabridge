"""
Migration to add retention_policies table and update snapshots schema.

This migration:
1. Creates the retention_policies table
2. Adds connector_id and trigger columns to snapshots table
3. Creates indexes for performance
"""

import sqlite3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate(db_path: str):
    """Apply migration to database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    
    try:
        # 1. Create retention_policies table
        logger.info("Creating retention_policies table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS retention_policies (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL UNIQUE REFERENCES projects(project_id) ON DELETE CASCADE,
                strategy TEXT NOT NULL CHECK (strategy IN ('count', 'days', 'unlimited')) DEFAULT 'unlimited',
                max_snapshots_per_connector INTEGER,
                max_age_days INTEGER,
                prune_manual_snapshots BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_retention_project ON retention_policies(project_id)")
        
        # 2. Add connector_id and trigger to snapshots if not exists
        logger.info("Updating snapshots table...")
        
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(snapshots)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'connector_id' not in columns:
            cursor.execute("ALTER TABLE snapshots ADD COLUMN connector_id TEXT")
            logger.info("Added connector_id column to snapshots")
        
        if 'trigger' not in columns:
            cursor.execute("ALTER TABLE snapshots ADD COLUMN trigger TEXT")
            logger.info("Added trigger column to snapshots")
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_snapshots_connector ON snapshots(connector_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_snapshots_trigger ON snapshots(trigger)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_snapshots_project_ts ON snapshots(project_id, timestamp)")
        
        conn.commit()
        logger.info("Migration completed successfully")
        
    except Exception as e:
        conn.rollback()
        logger.error("Migration failed: %s", e)
        raise
    finally:
        conn.close()


def rollback(db_path: str):
    """Rollback migration (if needed)."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Drop retention_policies table
        cursor.execute("DROP TABLE IF EXISTS retention_policies")
        
        # Note: SQLite doesn't support DROP COLUMN easily, would need table recreation
        # For now, we leave the extra columns
        
        conn.commit()
        logger.info("Rollback completed")
    except Exception as e:
        conn.rollback()
        logger.error("Rollback failed: %s", e)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    db_file = sys.argv[1] if len(sys.argv) > 1 else "semabridge.db"
    migrate(db_file)
