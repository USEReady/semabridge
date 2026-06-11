import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from semabridge.core.config import Config, ProjectConfig, ProjectOptions
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.engine import ExecutionEngine
from semabridge.intermediate.models import OSIModel
from semabridge.repository.model_repository import ModelRepository

def test_osi_only_mode_skips_sml_and_deploys_osi():
    # 1. Setup mock config with skip_sml_conversion = True
    config = Config(
        project=ProjectConfig(
            name="test_project",
            adapter="fabric",
            workspace_id="test_ws",
            options=ProjectOptions(skip_sml_conversion=True)
        ),
        target={"type": "snowflake"}
    )
    
    # 2. Setup mock components
    mock_db = MagicMock(spec=ModelRepository)
    mock_db.commit_model.return_value = (True, "mock_snapshot_id")
    
    engine = ExecutionEngine(config=config, db_manager=mock_db)
    
    # Mock steps
    engine._step4_extract = MagicMock(return_value=MagicMock())
    engine._step5_validate_source = MagicMock()
    
    # Mock fabric extraction to return a mock OSI model but no SML model
    mock_osi = OSIModel(datasets=[], metrics=[], relationships=[])
    
    def mock_convert_to_sml(context, workspace_id, dataset_id, force=False):
        context.osi_model = mock_osi
        # Engine conversion/fabric.py should return None when skip_sml_conversion=True
        return None
        
    engine._step6_convert_to_sml = MagicMock(side_effect=mock_convert_to_sml)
    
    # Mock _apply_mapping_overrides_from_config
    engine._apply_mapping_overrides_from_config = MagicMock()
    
    # Mock Snowflake deploy_from_osi
    with patch('semabridge.core.engine.deployment.snowflake.SnowflakeEmitter') as MockEmitter:
        mock_emitter_instance = MockEmitter.return_value
        mock_emitter_instance.deploy_from_osi.return_value = True
        
        # 3. Execute pipeline
        summary = engine.execute(source="fabric", target="snowflake")
        
        # 4. Assertions
        
        # Assert step6 was called
        engine._step6_convert_to_sml.assert_called_once()
        
        # Assert _apply_mapping_overrides was NOT called with None
        engine._apply_mapping_overrides_from_config.assert_not_called()
        
        # Assert that the DB committed with format_type="osi"
        mock_db.commit_model.assert_called_once()
        call_kwargs = mock_db.commit_model.call_args.kwargs
        assert call_kwargs.get("format_type") == "osi"
        
        # Assert Snowflake emitter called deploy_from_osi instead of deploy
        mock_emitter_instance.deploy.assert_not_called()
        mock_emitter_instance.deploy_from_osi.assert_called_once_with(
            mock_osi,
            sync_mode="copy"
        )
