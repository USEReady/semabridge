import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Request, Depends, HTTPException

from semabridge.api.services.mappings_service import (
    MappingService,
    get_mapping_service,
    list_mappings_compat,
    delete_mappings_compat,
)

# --- Request Models ---

class DryRunRequest(BaseModel):
    source_config: Dict[str, Any]
    target_config: Dict[str, Any]
    selected_sources: List[str]

class UpdateMappingRequest(BaseModel):
    target_name: str
    status: Optional[str] = "manual"

class AutoMapRequest(BaseModel):
    source_config: Dict[str, Any]
    target_config: Dict[str, Any]
    selected_sources: List[str]

class DeployRequest(BaseModel):
    field_mappings: List[Dict[str, Any]]

# --- Router ---

router = APIRouter()

router.get('/api/mappings')(list_mappings_compat)
router.delete('/api/mappings')(delete_mappings_compat)

from fastapi.responses import JSONResponse

@router.post("/api/projects/{project_id}/dry-run")
async def dry_run_mapping(
    project_id: str, 
    request: DryRunRequest,
    service: MappingService = Depends(get_mapping_service)
):
    """
    Execute dry run to get field-level mappings.
    Uses project_id = "preview" for wizard flows.
    Returns ONLY entity_kind = "field" mappings.
    """
    try:
        # Call existing auto_map_compat
        result = await service.auto_map_compat(
            source_config=request.source_config,
            target_config=request.target_config,
            selected_sources=request.selected_sources,
            dry_run=True
        )
        
        # SAFELY extract entity_mappings
        entity_mappings = result.get("entity_mappings", [])
        
        # Ensure it's a list
        if not isinstance(entity_mappings, list):
            entity_mappings = []
        
        # Filter to field-level only
        filtered_mappings = []
        for m in entity_mappings:
            if not isinstance(m, dict):
                continue
            kind = m.get("entity_kind")
            if kind == "field" or kind == "column" or kind == "measure":
                # Ensure required fields exist
                safe_mapping = {
                    "id": m.get("id", f"field_{len(filtered_mappings)}"),
                    "entity_kind": "field",
                    "source_name": m.get("source_name", "unknown"),
                    "source_data_type": m.get("source_data_type", "unknown"),
                    "source_table": m.get("source_table", m.get("source_entity", "unknown")),
                    "source_path": m.get("source_path", ""),
                    "source_qualified_path": m.get("source_qualified_path", ""),
                    "target_name": m.get("target_name", ""),
                    "target_data_type": m.get("target_data_type", ""),
                    "mapping_status": m.get("mapping_status", m.get("status", "unmapped")),
                    "status": m.get("status", m.get("mapping_status", "unmapped"))
                }
                filtered_mappings.append(safe_mapping)
        
        # Add collision handling
        filtered_mappings = service.add_collision_handling(filtered_mappings)
        
        return {
            "success": True,
            "entity_mappings": filtered_mappings,
            "summary": {
                "total_fields": len(filtered_mappings),
                "auto_mapped": sum(1 for m in filtered_mappings if m.get("mapping_status") == "auto" or m.get("status") == "auto"),
                "unmapped": sum(1 for m in filtered_mappings if m.get("mapping_status") == "unmapped" or m.get("status") == "unmapped"),
                "collisions": sum(1 for m in filtered_mappings if m.get("mapping_status") == "collision" or m.get("status") == "collision")
            }
        }
        
    except Exception as e:
        print(f"[Dry Run Error] {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Return proper error response (not crash)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "entity_mappings": [],
                "summary": {"total_fields": 0, "auto_mapped": 0, "unmapped": 0, "collisions": 0}
            }
        )

@router.put("/api/projects/{project_id}/mappings/{mapping_id}")
async def update_mapping(
    project_id: str,
    mapping_id: str,
    request: UpdateMappingRequest,
    service: MappingService = Depends(get_mapping_service)
):
    """
    Update a single field mapping (used when user edits target field).
    Sets status to "manual" automatically.
    """
    result = await service.update_mapping_compat(
        mapping_id=mapping_id,
        target_name=request.target_name,
        status="manual"
    )
    return {"success": True, "mapping": result}

@router.post("/api/projects/{project_id}/auto-map")
async def rerun_auto_map(
    project_id: str,
    request: AutoMapRequest,
    service: MappingService = Depends(get_mapping_service)
):
    """
    Re-run auto-mapping algorithm on demand.
    """
    result = await service.auto_map_compat(
        source_config=request.source_config,
        target_config=request.target_config,
        selected_sources=request.selected_sources,
        dry_run=False
    )
    
    filtered_mappings = [
        m for m in result.get("entity_mappings", [])
        if m.get("entity_kind") == "field" or m.get("entity_kind") == "column" or m.get("entity_kind") == "measure"
    ]
    
    filtered_mappings = service.add_collision_handling(filtered_mappings)
    
    return {"entity_mappings": filtered_mappings}

@router.post("/api/projects/{project_id}/deploy")
async def deploy_mappings(
    project_id: str,
    request: DeployRequest,
    service: MappingService = Depends(get_mapping_service)
):
    """
    Deploy final mappings to target system.
    For "preview" projects, this creates the actual project first.
    """
    # If project_id is "preview", create the project first
    actual_project_id = project_id
    if project_id == "preview":
        project = await service.create_project_from_mappings(request.field_mappings)
        actual_project_id = project.id
    
    result = await service.manual_deploy_compat(
        project_id=actual_project_id,
        field_mappings=request.field_mappings
    )
    
    return {
        "success": True,
        "project_id": actual_project_id,
        "deployed_count": result.get("deployed_count", 0),
        "errors": result.get("errors", [])
    }

# Keep old endpoint for backwards compatibility for now
@router.post('/api/mappings/auto')
async def auto_map_with_user_context(request: Request, payload: Dict[str, Any]):
    from semabridge.api.services.project_domain_service import auto_map_compat
    body = dict(payload or {})
    if os.environ.get("AUTH_ENABLED", "").lower() == "true":
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            body["user_id"] = user_id
    return await auto_map_compat(body)
