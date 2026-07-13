#!/usr/bin/env python
import os
import sys
import io
import json

# Ensure semabridge source path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from semabridge.api.services.project_mapping_engine import build_entity_mappings
from semabridge.api.services.mapping_service import _compat_serialize_auto_map_entity_mappings
from semabridge.api.services.mappings_service import get_mapping_service

# Define mock representative SML model
mock_model = {
    "unique_name": "FabricCollisionModel",
    "datasets": [
        {
            "unique_name": "Store",
            "columns": [
                {"name": "Revenue", "data_type": "decimal"},
                {"name": "Revenue Total", "data_type": "decimal"},
                {"name": "Revenue_Total", "data_type": "decimal"},
                {"name": "SELECT", "data_type": "string"},
                {"name": "FROM", "data_type": "string"},
                {"name": "ORDER", "data_type": "string"},
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

# Define existing mappings with user manual overrides to trigger manual override conflict
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

def run_pipeline():
    # Capture print outputs
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    try:
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
        
        # Filter to field level like controller does
        filtered_mappings = []
        for m in serialized:
            kind = str(m.get("entity_kind") or "").lower()
            if kind not in {"field", "column", "measure", "metric"}:
                continue
            normalised_kind = "measure" if kind in ("metric", "measure") else kind
            filtered_mappings.append({
                "id": m.get("id"),
                "project_id": "test-collision-project",
                "model_name": "FabricCollisionModel",
                "entity_kind": normalised_kind,
                "source_name": m.get("source_name"),
                "source_data_type": m.get("source_data_type"),
                "source_table": m.get("parent_source_path", "").split(".")[-1] if m.get("parent_source_path") else "",
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
                "measure_source_tables": [],
            })
            
        # Layer 3: add_collision_handling
        service = get_mapping_service()
        final_mappings = service.add_collision_handling(filtered_mappings)
        
    finally:
        sys.stdout = old_stdout

    # Parse logged JSONs
    log_output = buffer.getvalue()
    logs = []
    for line in log_output.strip().split("\n"):
        if not line.strip():
            continue
        try:
            logs.append(json.loads(line))
        except Exception as e:
            print("Non-JSON Log:", line, file=sys.stderr)

    return logs, built, serialized, final_mappings

if __name__ == "__main__":
    logs, built, serialized, final = run_pipeline()
    
    print("--- RAW INSTRUMENTATION COLLISION LOGS ---")
    for log in logs:
        print(f"Layer: {log['layer']:<45} | Source: {log['source_path']:<45} | Target: {log['target_name']:<30} | Reason: {log['collision_reason']}")
        
    print("\n--- COLLISION REPORT ANALYSIS ---")
    
    # Analyze by layer
    by_layer = {"build_entity_mappings": [], "_compat_serialize_auto_map_entity_mappings": [], "add_collision_handling": []}
    for log in logs:
        layer = log["layer"]
        if layer in by_layer:
            by_layer[layer].append(log)
            
    print(f"Detected by build_entity_mappings: {len(by_layer['build_entity_mappings'])}")
    print(f"Detected by serializer:            {len(by_layer['_compat_serialize_auto_map_entity_mappings'])}")
    print(f"Detected by add_collision_handling: {len(by_layer['add_collision_handling'])}")
    
    # Identify unique detections per layer
    # We group by source_path + target_name or similar logic
    def get_key(log):
        # normalize source path and original target
        return (log["source_path"], log["layer"])
        
    # We also group strictly by source_path to see what layers caught each source path
    src_to_layers = {}
    for log in logs:
        sp = log["source_path"]
        if sp not in src_to_layers:
            src_to_layers[sp] = {}
        src_to_layers[sp][log["layer"]] = log
        
    print("\n--- Detailed Detections per Source Object ---")
    for sp, layers in sorted(src_to_layers.items()):
        detected_in = list(layers.keys())
        print(f"\nSource Object: {sp}")
        print(f"  Detected in layers: {detected_in}")
        for lyr, details in layers.items():
            print(f"    - Layer: {lyr:<45} | Target Name: {details['target_name']:<35} | Reason: {details['collision_reason']:<30} | Validation Code: {details['validation_code']}")

    # Classify overlap categories
    only_engine = []
    only_serializer = []
    only_handling = []
    multiple_layers = []
    
    for sp, layers in src_to_layers.items():
        keys = list(layers.keys())
        if len(keys) == 1:
            if "build_entity_mappings" in keys:
                only_engine.append((sp, layers["build_entity_mappings"]))
            elif "_compat_serialize_auto_map_entity_mappings" in keys:
                only_serializer.append((sp, layers["_compat_serialize_auto_map_entity_mappings"]))
            elif "add_collision_handling" in keys:
                only_handling.append((sp, layers["add_collision_handling"]))
        else:
            multiple_layers.append((sp, keys))
            
    print("\n--- Collision Analysis Categories ---")
    print(f"\nDetected ONLY by build_entity_mappings: {len(only_engine)}")
    for sp, details in only_engine:
        print(f"  - {sp} | Target Name: {details['target_name']} | Reason: {details['collision_reason']}")
        
    print(f"\nDetected ONLY by serializer (_compat_serialize_auto_map_entity_mappings): {len(only_serializer)}")
    for sp, details in only_serializer:
        print(f"  - {sp} | Target Name: {details['target_name']} | Reason: {details['collision_reason']}")
        
    print(f"\nDetected ONLY by add_collision_handling: {len(only_handling)}")
    for sp, details in only_handling:
        print(f"  - {sp} | Target Name: {details['target_name']} | Reason: {details['collision_reason']}")
        
    print(f"\nDetected by MULTIPLE layers: {len(multiple_layers)}")
    for sp, layers_list in multiple_layers:
        print(f"  - {sp} | Layers: {layers_list}")
