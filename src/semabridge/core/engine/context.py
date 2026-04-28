from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional
from pydantic import Field
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import Settings
from semabridge.core.source_format import SourceFormat
from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel

@dataclass
class RunContext:
    """
    Execution context propagated through all steps.
    
    Generated at Step 2 and used throughout execution.
    """
    project_id: str
    run_id: str
    config: Settings
    start_time: float
    source_type: Literal["snowflake", "fabric", "pbix"]
    target_type: Optional[Literal["snowflake", "fabric", "databricks"]] = None
    behavior: ConnectorBehavior = Field(default_factory=ConnectorBehavior)
    account_id: Optional[str] = None  # Linked Account for multi-user credential scoping
    
    # Artifacts accumulated during execution
    source_format: Optional[SourceFormat] = None
    osi_model: Optional[OSIModel] = None  # OSI intermediate — populated after Step 6
    sml_model: Optional[SMLModel] = None
    source_artifact_id: Optional[str] = None
    sml_snapshot_id: Optional[str] = None
    target_artifact_path: Optional[str] = None
    routing_summary: Optional[dict[str, Any]] = None
