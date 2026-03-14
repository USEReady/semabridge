"""
Create a test version with a new metric for rollback testing.
"""
import sys
sys.path.insert(0, 'src')

from semabridge.repository.duckdb_manager import DuckDBManager
import copy

def create_test_version():
    db = DuckDBManager()
    
    # Get current HEAD
    head = db.get_head("Customer Profitability")
    if not head:
        print("No HEAD found for Customer Profitability")
        return
    
    print(f"Current HEAD: {head.version_tag} ({head.snapshot_id[:12]})")
    print(f"Metrics count: {len(head.sml_blob.get('metrics', []))}")
    
    # Make a deep copy and add a test metric
    new_sml = copy.deepcopy(head.sml_blob)
    
    test_metric = {
        "unique_name": "Test_Metric_For_Rollback",
        "object_type": "metric",
        "label": "Test Metric for Rollback",
        "description": "This is a test metric added to test rollback functionality",
        "is_hidden": False,
        "expression": "SUM([Sales Amount]) * 2",
        "format_string": "$#,##0",
        "aggregation": "sum",
        "sync_enabled": False,
        "complexity_tier": 1,
        "dataset": "Sales",
        "source_column": "Sales Amount"
    }
    
    new_sml.setdefault("metrics", []).append(test_metric)
    
    # Commit as new version
    committed, snapshot_id = db.commit_model(
        project_id="Customer Profitability",
        sml_json=new_sml,
        tag="v3.00",
        status="success",
        duration_ms=100
    )
    
    print(f"\n[OK] Created new version v3.00")
    print(f"  Snapshot ID: {snapshot_id[:12]}...")
    print(f"  Metrics count: {len(new_sml.get('metrics', []))}")
    print(f"\nNow you can test rollback from v3.00 to v2.00!")
    print(f"\nRun: python main.py rollback -d \"Customer Profitability\" --tag v2.00 --dry-run")

if __name__ == "__main__":
    create_test_version()
