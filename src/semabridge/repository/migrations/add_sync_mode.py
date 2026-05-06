"""
Migration to add sync_mode column to snapshots table.

This migration:
1. Adds sync_mode column to snapshots table for v4.3 rollback tracking
2. Sets default value to "copy" for existing snapshots
3. Ensures sync_mode persists through database for dynamic rollback
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
        logger.info("Adding sync_mode column to snapshots table...")
        
        # Check if column already exists
        cursor.execute("PRAGMA table_info(snapshots)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'sync_mode' not in columns:
            cursor.execute("""
                ALTER TABLE snapshots ADD COLUMN sync_mode TEXT NOT NULL DEFAULT 'copy'
            """)
            logger.info("Added sync_mode column to snapshots (default='copy')")
        else:
            logger.info("sync_mode column already exists in snapshots table")
        
        conn.commit()
        logger.info("Migration completed successfully")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        conn.close()


def rollback(db_path: str):
    """Rollback migration."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    
    try:
        logger.info("Removing sync_mode column from snapshots table...")
        
        # SQLite doesn't support DROP COLUMN directly before 3.35.0
        # We'll need to recreate the table without the column
        cursor.execute("PRAGMA table_info(snapshots)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'sync_mode' in columns:
            # This is complex in SQLite, so we'll just mark it as a no-op for safety
            logger.warning("Rollback not fully implemented - sync_mode column will remain in database")
            logger.warning("To manually rollback, drop and recreate snapshots table")
        else:
            logger.info("sync_mode column does not exist - nothing to rollback")
        
        conn.commit()
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Rollback failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
        migrate(db_path)
        print(f"Migration applied to {db_path}")
