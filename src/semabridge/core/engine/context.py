from __future__ import annotations

from dataclasses import dataclass, field as _dataclass_field
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import Settings
from semabridge.core.source_format import SourceFormat
from semabridge.core.drop_ledger import DropLedger
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
    behavior: ConnectorBehavior = _dataclass_field(default_factory=ConnectorBehavior)
    account_id: Optional[str] = None  # Linked Account for multi-user credential scoping

    # Optional override for the Snowflake semantic view name.
    # Set from model_name / project_name in the project config YAML.
    # Kept separate from config.model.name to avoid mutating the shared
    # lru_cache Settings singleton.
    semantic_view_name_override: Optional[str] = None

    # Artifacts accumulated during execution
    source_format: Optional[SourceFormat] = None
    osi_model: Optional[OSIModel] = None  # OSI intermediate — populated after Step 6
    sml_model: Optional[SMLModel] = None
    source_artifact_id: Optional[str] = None
    sml_snapshot_id: Optional[str] = None
    target_artifact_path: Optional[str] = None
    routing_summary: Optional[dict[str, Any]] = None
    sync_mode: str = "copy"

    # Threaded through so Step 6's OSI→SML conversion can apply
    # metric-name mapping overrides to the OSI model's DAX bracket
    # references BEFORE translation runs, not just to the final SML
    # model afterward. See _apply_mapping_overrides_from_config.
    config_path: Optional[Path] = None
    config_payload: Optional[Dict[str, Any]] = None

    # Uniform "what got dropped and why" collector for this run — shared by
    # every DDL-building/extraction component so all drop reasons (DAX
    # translation, schema mismatch, DDL-emission skips, DDL-deployment
    # rejections) land in one place regardless of stage or cause.
    drop_ledger: DropLedger = _dataclass_field(default_factory=DropLedger)
