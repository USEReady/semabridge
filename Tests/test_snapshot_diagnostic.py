"""
Diagnostic test to inspect snapshot loading for 0-model case.

Run this after a sync that produces a 0-model snapshot to verify:
1. The snapshot is in the database
2. The API returns it with model_count=0
3. The API returns a valid graph response

Usage:
    python -m pytest Tests/test_snapshot_diagnostic.py -v -s
"""

import pytest
import asyncio
import json
from pathlib import Path


@pytest.mark.asyncio
async def test_snapshot_api_returns_0_model_snapshots():
    """
    Verify that snapshots with 0 models are returned by the API endpoints.
    
    This test queries the real backend API to verify that 0-model snapshots:
    1. Are included in graph_snapshots_compat response
    2. Have model_count field set to 0
    3. Have empty semantic_models array
    """
    import sys
    import os
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    
    from semabridge.api.services import project_projects_impl
    from semabridge.database.manager import DBManager
    
    # Get or create DB manager
    db_manager = DBManager()
    
    # Check all snapshots in database
    all_snapshots = db_manager.list_all_snapshots(limit=100)
    
    print("\n" + "="*60)
    print("ALL SNAPSHOTS IN DATABASE")
    print("="*60)
    
    zero_model_snapshots = []
    for snap in (all_snapshots or []):
        snap_id = snap.snapshot_id if hasattr(snap, 'snapshot_id') else snap.get('snapshot_id', '?')
        sml = snap.sml_blob if hasattr(snap, 'sml_blob') else snap.get('sml_blob', {})
        
        # Count models
        datasets = []
        if isinstance(sml, dict):
            datasets = sml.get('datasets', []) or []
        
        is_zero_model = len(datasets) == 0
        marker = "★ 0-MODELS ★" if is_zero_model else f"  {len(datasets)} models"
        
        print(f"{marker} | snapshot_id={snap_id} | project={snap.project_id if hasattr(snap, 'project_id') else snap.get('project_id', '?')}")
        
        if is_zero_model:
            zero_model_snapshots.append(snap)
    
    print(f"\nTotal: {len(all_snapshots or [])} snapshots, {len(zero_model_snapshots)} with 0 models")
    
    if zero_model_snapshots:
        print("\n" + "="*60)
        print("TESTING graph_snapshots_compat() RESPONSE")
        print("="*60)
        
        zero_snap = zero_model_snapshots[0]
        snap_id = zero_snap.snapshot_id if hasattr(zero_snap, 'snapshot_id') else zero_snap.get('snapshot_id')
        project_id = zero_snap.project_id if hasattr(zero_snap, 'project_id') else zero_snap.get('project_id')
        
        # Call the actual API function
        response = await project_projects_impl.graph_snapshots_compat(project_id)
        
        print(f"\nCalling graph_snapshots_compat('{project_id}')")
        print(f"Response count: {len(response)}")
        
        # Find our 0-model snapshot in the response
        matching_snap = None
        for row in response:
            if row.get('snapshot_id') == snap_id:
                matching_snap = row
                break
        
        if matching_snap:
            print(f"\n✓ Found snapshot in API response: {snap_id}")
            print(f"  model_count: {matching_snap.get('model_count')}")
            print(f"  semantic_models: {matching_snap.get('semantic_models')}")
            print(f"  model_label: {matching_snap.get('model_label')}")
            print(f"  project_id: {matching_snap.get('project_id')}")
            print("\nFull response object:")
            print(json.dumps(matching_snap, indent=2, default=str))
        else:
            print(f"\n✗ 0-model snapshot NOT found in API response!")
            print(f"Expected snapshot_id: {snap_id}")
            print(f"\nSnapshots in response:")
            for row in response[:5]:
                print(f"  - {row.get('snapshot_id')} (model_count={row.get('model_count')})")
        
        # Test graph_snapshot_compat
        print(f"\n" + "="*60)
        print("TESTING graph_snapshot_compat() RESPONSE")
        print("="*60)
        
        graph = await project_projects_impl.graph_snapshot_compat(project_id, snap_id, False, False)
        print(f"\nCalling graph_snapshot_compat('{project_id}', '{snap_id}')")
        print(f"Response nodes: {len(graph.get('nodes', []))}")
        print(f"Response edges: {len(graph.get('edges', []))}")
        
        if isinstance(graph.get('nodes'), list):
            print(f"✓ nodes is a list with {len(graph['nodes'])} items")
        else:
            print(f"✗ nodes is NOT a list: {type(graph.get('nodes'))}")
        
        print("\nGraph response:")
        print(json.dumps({
            'nodes_count': len(graph.get('nodes', [])),
            'edges_count': len(graph.get('edges', [])),
            'snapshot_id': graph.get('snapshot_id'),
            'model_id': graph.get('model_id'),
        }, indent=2))
    else:
        print("\n⚠ No 0-model snapshots found in database!")
        print("Please run a sync that results in 0 model changes first.")


if __name__ == '__main__':
    # Run without pytest if invoked directly
    asyncio.run(test_snapshot_api_returns_0_model_snapshots())
