#!/usr/bin/env python
import os
import sys
import json
import copy

# Ensure semabridge source path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from semabridge.api.services.project_mapping_engine import build_entity_mappings
from semabridge.api.services.mapping_service import _compat_serialize_auto_map_entity_mappings
from semabridge.api.services.mappings_service import get_mapping_service

# Representative SML model (same as analyze_collision_layers)
mock_model = {
    "unique_name": "FabricCollisionModel",
    "datasets": [
        {
            "unique_name": "Store",
            "columns": [
                {"name": "Revenue", "data_type": "decimal"},
                {"name": "Revenue Total", "data_type": "decimal"},
                {"name": "Revenue_Total", "data_type": "decimal"},
                {"name": "Name", "data_type": "string"},
            ]
        },
        {
            "unique_name": "Web",
            "columns": [
                {"name": "Revenue", "data_type": "decimal"},
                {"name": "Name", "data_type": "string"},
            ]
        },
        {
            "unique_name": "Customers",
            "columns": [
                {"name": "ID", "data_type": "int"}
            ]
        },
        {
            "unique_name": "Products",
            "columns": [
                {"name": "ID", "data_type": "int"}
            ]
        }
    ]
}

existing_mappings = {
    "datasets.Store.columns.Name": {
        "id": "mapping-store-name",
        "target_name": "UNIQUE_NAME",
        "is_user_edited": True
    },
    "datasets.Web.columns.Name": {
        "id": "mapping-web-name",
        "target_name": "UNIQUE_NAME",
        "is_user_edited": True
    }
}

def generate_dry_run_payload(bypass=False):
    # Layer 1: build_entity_mappings
    built = build_entity_mappings(
        project_id="test-collision-project",
        model=mock_model,
        existing_mappings=existing_mappings,
        session_key="test-session",
        target_connector="snowflake"
    )
    
    # Layer 2: _compat_serialize_auto_map_entity_mappings
    serialized = _compat_serialize_auto_map_entity_mappings(
        built["mappings"],
        target_connector="snowflake"
    )
    
    # Filter and construct filtered_mappings (replicating mappings_controller)
    filtered_mappings = []
    for m in serialized:
        kind = str(m.get("entity_kind") or "").lower()
        if kind not in {"field", "column", "measure", "metric"}:
            continue
        normalised_kind = "measure" if kind in ("metric", "measure") else kind
        filtered_mappings.append({
            "id": m.get("id"),
            "source_name": m.get("source_name"),
            "source_path": m.get("source_path"),
            "target_name": m.get("target_name"),
            "target_data_type": m.get("target_data_type"),
            "mapping_status": m.get("status", "auto"),
            "status": m.get("status", "auto"),
            "suggested_target_name": m.get("suggested_target_name"),
            "collision_detected": bool(m.get("collision_detected")),
            "validation_status": m.get("validation_status"),
            "validation_code": m.get("validation_code"),
            "validation_message": m.get("validation_message"),
        })
        
    if not bypass:
        service = get_mapping_service()
        filtered_mappings = service.add_collision_handling(filtered_mappings)
        
    return filtered_mappings

if __name__ == "__main__":
    with_handling = generate_dry_run_payload(bypass=False)
    bypassed = generate_dry_run_payload(bypass=True)
    
    # Map by source_path for easy lookup
    map_with = {row["source_path"]: row for row in with_handling}
    map_bypassed = {row["source_path"]: row for row in bypassed}
    
    all_paths = sorted(list(set(map_with.keys()) | set(map_bypassed.keys())))
    
    fields_to_compare = [
        "target_name",
        "suggested_target_name",
        "collision_detected",
        "status",
        "validation_status",
        "validation_code",
        "mapping_status"
    ]
    
    diff_report = []
    changed_rows = 0
    
    for path in all_paths:
        row_with = map_with.get(path, {})
        row_bypass = map_bypassed.get(path, {})
        
        row_diff = {}
        has_change = False
        
        for field in fields_to_compare:
            val_with = row_with.get(field)
            val_bypass = row_bypass.get(field)
            if val_with != val_bypass:
                row_diff[field] = {"enabled": val_with, "bypassed": val_bypass}
                has_change = True
                
        if has_change:
            changed_rows += 1
            diff_report.append({
                "source_path": path,
                "source_name": row_with.get("source_name"),
                "diff": row_diff
            })
            
    # Print results
    print(f"Total mapping rows: {len(all_paths)}")
    print(f"Changed mapping rows: {changed_rows}")
    print("\n--- DIFF REPORT ---")
    print(json.dumps(diff_report, indent=2))
