import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.core.config import Config
from semabridge.core.project import ProjectConfig, ProjectOptions
from semabridge.core.engine.engine import ExecutionEngine
from semabridge.intermediate.models import OSIModel
from semabridge.repository.model_repository import ModelRepository
from unittest.mock import MagicMock, patch

def run_test():
    # Setup mock config with skip_sml_conversion = True
    config = Config(
        project=ProjectConfig(
            name="test_project",
            adapter="fabric",
            workspace_id="test_ws",
            options=ProjectOptions(skip_sml_conversion=True)
        ),
        target={"type": "snowflake"}
    )
    
    # Setup mock components
    mock_db = MagicMock(spec=ModelRepository)
    mock_db.commit_model.return_value = (True, "mock_snapshot_id")
    
    engine = ExecutionEngine(config=config, db_manager=mock_db)
    engine._step4_extract = MagicMock(return_value=MagicMock())
    engine._step5_validate_source = MagicMock()
    
    mock_osi = OSIModel(datasets=[], metrics=[], relationships=[])
    
    def mock_convert_to_sml(context, workspace_id, dataset_id, force=False):
        context.osi_model = mock_osi
        return None
        
    engine._step6_convert_to_sml = MagicMock(side_effect=mock_convert_to_sml)
    engine._apply_mapping_overrides_from_config = MagicMock()
    
    with patch('semabridge.core.engine.deployment.snowflake.SnowflakeEmitter') as MockEmitter:
        mock_emitter_instance = MockEmitter.return_value
        mock_emitter_instance.deploy_from_osi.return_value = True
        
        summary = engine.execute(source="fabric", target="snowflake")
        
        engine._step6_convert_to_sml.assert_called_once()
        engine._apply_mapping_overrides_from_config.assert_not_called()
        mock_db.commit_model.assert_called_once()
        assert mock_db.commit_model.call_args.kwargs.get("format_type") == "osi"
        mock_emitter_instance.deploy_from_osi.assert_called_once_with(mock_osi, sync_mode="copy")
        mock_emitter_instance.deploy.assert_not_called()
        
        print("All tests passed! SML skipped, OSI deployed, and database saved as format_type=osi.")

if __name__ == "__main__":
    run_test()
