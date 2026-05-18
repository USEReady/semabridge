#!/usr/bin/env python3
"""
Diagnostic: Check snapshot storage in database
"""
import duckdb
import json
import os
from pathlib import Path

# Find the DuckDB database
workspace_root = Path(__file__).parent.parent
db_path = workspace_root / "output" / "semabridge.duckdb"

print(f"📍 Looking for database at: {db_path}")
print(f"✓ Database exists: {db_path.exists()}\n")

if not db_path.exists():
    print("❌ Database not found. Make sure app is running and has created the DB.")
    exit(1)

# Connect and query
conn = duckdb.connect(str(db_path), read_only=True)

try:
    # 1. Check snapshots table exists
    tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
    print("📊 Available tables:")
    for (table_name,) in tables:
        print(f"  - {table_name}")
    print()
    
    # 2. Count total snapshots
    count_result = conn.execute("SELECT COUNT(*) as cnt FROM snapshots").fetchall()
    total_snapshots = count_result[0][0] if count_result else 0
    print(f"📈 Total snapshots in database: {total_snapshots}\n")
    
    if total_snapshots == 0:
        print("⚠️  No snapshots found in database!")
        exit(0)
    
    # 3. List all snapshots with model count
    print("📋 Snapshots with model count:")
    print("-" * 80)
    
    snapshots = conn.execute("""
        SELECT 
            snapshot_id,
            project_id,
            timestamp,
            status,
            sml_blob
        FROM snapshots
        ORDER BY timestamp DESC
        LIMIT 20
    """).fetchall()
    
    for snap_id, proj_id, ts, status, sml_blob in snapshots:
        try:
            if sml_blob:
                blob_data = json.loads(sml_blob)
                models = blob_data.get('models', []) if isinstance(blob_data, dict) else []
                model_count = len(models) if isinstance(models, list) else 0
            else:
                model_count = 0
                blob_data = None
            
            print(f"\n🔹 Snapshot: {snap_id}")
            print(f"   Project: {proj_id}")
            print(f"   Time: {ts}")
            print(f"   Status: {status}")
            print(f"   Models: {model_count}")
            
            if blob_data and model_count == 0:
                print(f"   ⚠️  ZERO MODELS - sml_blob keys: {list(blob_data.keys())}")
        except Exception as e:
            print(f"   ❌ Error parsing: {e}")
    
    print("\n" + "-" * 80)
    print("\n📊 Summary by project and model count:")
    summary = conn.execute("""
        SELECT 
            project_id,
            COUNT(*) as snapshot_count
        FROM snapshots
        GROUP BY project_id
        ORDER BY project_id
    """).fetchall()
    
    for proj_id, snap_count in summary:
        print(f"  {proj_id}: {snap_count} snapshots")
    
    # 4. Check for 0-model snapshots specifically
    print("\n🔍 Snapshots with 0 models:")
    print("-" * 80)
    
    zero_model_count = 0
    zero_models = conn.execute("SELECT snapshot_id, project_id, sml_blob FROM snapshots LIMIT 50").fetchall()
    
    for snap_id, proj_id, sml_blob in zero_models:
        try:
            if sml_blob:
                blob_data = json.loads(sml_blob)
                models = blob_data.get('models', []) if isinstance(blob_data, dict) else []
                model_count = len(models) if isinstance(models, list) else 0
                
                if model_count == 0:
                    zero_model_count += 1
                    print(f"  ✓ {snap_id} ({proj_id}) - sml_blob has {len(blob_data)} fields")
                    if model_count == 0 and blob_data:
                        print(f"    Fields in sml_blob: {', '.join(blob_data.keys())}")
        except Exception as e:
            print(f"  ✗ {snap_id}: {e}")
    
    if zero_model_count == 0:
        print("  ✓ No 0-model snapshots found in database")
    else:
        print(f"\n  Found {zero_model_count} snapshots with 0 models")

finally:
    conn.close()
    print("\n✅ Database check complete")
