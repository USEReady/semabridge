from pydantic import BaseModel, Field


class WorkspaceSelectionPayload(BaseModel):
    workspace_id: str = Field(..., min_length=1)
    workspace_name: str = ''


class AuthFlowPayload(BaseModel):
    flow_id: str = ''
    tenant_id: str | None = None
