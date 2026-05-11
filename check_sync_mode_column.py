#!/usr/bin/env python
"""
Diagnostic script to verify sync_mode column exists in PostgreSQL snapshots table.
"""

import sys
from sqlalchemy import inspect, create_engine

def check_database():
    """Check if sync_mode column exists in snapshots table."""
    
    # Get database URL from environment or config
    import os
    from semabridge.repository.orm.base import get_db_url
    
    db_url = get_db_url()
    if not db_url:
        db_url = os.environ.get('DATABASE_URL', 'postgresql://localhost/semabridge')
    
    print(f"📊 Checking database: {db_url[:50]}...")
    print()
    
    engine = create_engine(db_url)
    inspector = inspect(engine)
    
    # Check if snapshots table exists
    tables = inspector.get_table_names()
    print(f"📋 Tables in database: {', '.join(tables)}")
    print()
    
    if 'snapshots' not in tables:
        print("❌ snapshots table not found!")
        return False
    
    # Check columns in snapshots table
    columns = inspector.get_columns('snapshots')
    column_names = [c['name'] for c in columns]
    
    print(f"📋 Columns in snapshots table:")
    for col in columns:
        print(f"   - {col['name']}: {col['type']}")
    print()
    
    # Check if sync_mode exists
    if 'sync_mode' in column_names:
        print("✅ sync_mode column EXISTS in snapshots table")
        return True
    else:
        print("❌ sync_mode column MISSING in snapshots table")
        print()
        print("Action Required:")
        print("  1. Run: cd Config && ..\.venv\Scripts\python.exe -m alembic upgrade head")
        print("  2. This will apply the migration to add sync_mode column")
        return False

if __name__ == '__main__':
    try:
        success = check_database()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
