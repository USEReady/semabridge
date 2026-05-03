from typing import List, Dict, Any, Optional
import hashlib

from semabridge.api.services.project_domain_service import (
    auto_map_compat,
    delete_mappings_compat,
    list_mappings_compat,
    update_mapping_compat,
    manual_deploy_compat,
)

class MappingService:
    async def auto_map_compat(self, source_config: Dict[str, Any], target_config: Dict[str, Any], selected_sources: List[str], dry_run: bool = False) -> Dict[str, Any]:
        payload = {
            "source_config": source_config,
            "target_config": target_config,
            "selected_model_names": selected_sources,
            "dry_run": dry_run,
        }
        return await auto_map_compat(payload)

    async def update_mapping_compat(self, mapping_id: str, target_name: str, target_data_type: Optional[str] = None, status: str = "manual") -> Dict[str, Any]:
        payload = {
            "target_name": target_name,
            "status": status,
        }
        if target_data_type is not None:
            payload["target_data_type"] = target_data_type
        return await update_mapping_compat(mapping_id, payload)

    async def manual_deploy_compat(self, project_id: str, field_mappings: List[Dict[str, Any]]) -> Dict[str, Any]:
        # For preview projects, we would normally create the project first.
        # But this is handled in the controller.
        from fastapi import BackgroundTasks
        return await manual_deploy_compat(project_id, snapshot_id="deploy", background_tasks=BackgroundTasks())

    async def create_project_from_mappings(self, field_mappings: List[Dict[str, Any]]):
        # Stub implementation. In reality, the user wizard handles project creation at Step 5.
        class StubProject:
            def __init__(self, id: str):
                self.id = id
        return StubProject("new_project_id")

    def add_collision_handling(self, mappings: List[Dict]) -> List[Dict]:
        """
        Detect collisions and add hash suffixes to conflicting target names.
        """
        # Group by target_name (case-insensitive)
        target_groups = {}
        for mapping in mappings:
            target_key = mapping.get("target_name", "").lower() if mapping.get("target_name") else ""
            if not target_key:
                continue
            if target_key not in target_groups:
                target_groups[target_key] = []
            target_groups[target_key].append(mapping)
        
        # Mark collisions
        for target_key, group in target_groups.items():
            if len(group) > 1:
                for mapping in group:
                    mapping["mapping_status"] = "collision"
                    # Generate hash suffix from source qualified path
                    source_path = mapping.get("source_path", mapping.get("source_name", ""))
                    hash_suffix = hashlib.md5(source_path.encode()).hexdigest()[:4]
                    mapping["suggested_target_name"] = f"{mapping.get('target_name', '')}_{hash_suffix}"
                    mapping["target_name"] = mapping["suggested_target_name"]
        
        return mappings

def get_mapping_service() -> MappingService:
    return MappingService()
