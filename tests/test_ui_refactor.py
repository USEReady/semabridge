"""
Tests for UI Refactoring (ConfigurationManager & VersionManager).
"""

import pytest
import shutil
from pathlib import Path
from semabridge.core.config_manager import ConfigurationManager
from semabridge.core.version_manager import VersionManager


@pytest.fixture
def clean_env(tmp_path):
    """Setup clean environment for testing."""
    config_path = tmp_path / "semabridge.yaml"
    repo_path = tmp_path / ".semabridge"
    
    # Create valid initial config
    initial_config = """
source:
  type: fabric
  workspace_id: "test-workspace"
  model: "*"
targets:
  - type: snowflake_semantic_view
    database: "TEST_DB"
    schema: "TEST_SCHEMA"
"""
    with open(config_path, "w") as f:
        f.write(initial_config)
        
    return config_path, repo_path

def test_config_manager_load(clean_env):
    """Test loading configuration."""
    config_path, _ = clean_env
    manager = ConfigurationManager(config_path)
    
    loaded = manager.load()
    if not loaded:
        print(f"Validation Errors: {manager._validation_errors}")
    assert loaded is True
    assert "test-workspace" in manager._current_text
    
    if not manager.is_valid():
        print(f"Validation Errors: {manager._validation_errors}")
    assert manager.is_valid() is True
    assert manager.get_config().source.workspace_id == "test-workspace"

def test_config_manager_validation_syntax_error(clean_env):
    """Test syntax validation."""
    config_path, _ = clean_env
    manager = ConfigurationManager(config_path)
    manager.load()
    
    # Introduce syntax error
    invalid_yaml = """
source:
  type: fabric
  workspace_id: "test-workspace"
  model: *  # Invalid scalar
targets:
  - type: snowflake
"""
    manager.update_text(invalid_yaml, source="test")
    
    assert manager.is_valid() is False
    assert len(manager._validation_errors) > 0
    assert "Syntax Error" in manager._validation_errors[0]["message"]

def test_config_manager_validation_schema_error(clean_env):
    """Test schema validation (missing required field)."""
    config_path, _ = clean_env
    manager = ConfigurationManager(config_path)
    manager.load()
    
    # Missing source
    invalid_schema = """
targets:
  - type: snowflake_semantic_view
    database: "TEST_DB"
    schema: "Schema"
"""
    manager.update_text(invalid_schema, source="test")
    
    assert manager.is_valid() is False
    assert any("Field required" in e["message"] for e in manager._validation_errors)

def test_version_manager_create_and_list(clean_env):
    """Test version creation and listing."""
    _, repo_path = clean_env
    manager = VersionManager(repo_path)
    
    v1_id = manager.create_version("content: v1", "Initial")
    v2_id = manager.create_version("content: v2", "Update")
    
    versions = manager.list_versions()
    assert len(versions) == 2
    assert versions[0]["version_id"] == v2_id  # Newest first
    assert versions[1]["version_id"] == v1_id

def test_version_manager_rollback(clean_env):
    """Test rollback mechanism via managers."""
    config_path, repo_path = clean_env
    
    # Setup
    v_mgr = VersionManager(repo_path)
    c_mgr = ConfigurationManager(config_path)
    c_mgr.load()
    
    # Save V1
    v1_content = c_mgr._current_text
    v1_id = v_mgr.create_version(v1_content, "V1")
    
    # Change to V2
    v2_content = v1_content.replace("test-workspace", "prod-workspace")
    c_mgr.update_text(v2_content, source="ui")
    c_mgr.save()
    v2_id = v_mgr.create_version(v2_content, "V2")
    
    assert "prod-workspace" in c_mgr._current_text
    
    # Rollback to V1 logic (simulated from UI)
    rollback_data = v_mgr.get_version(v1_id)
    c_mgr.update_text(rollback_data["content"], source="rollback")
    c_mgr.save()
    
    # Verify
    with open(config_path, "r") as f:
        current_disk = f.read()
        
    assert "test-workspace" in current_disk
    assert "prod-workspace" not in current_disk

def test_config_manager_validation_semantic_env_error(clean_env):
    """Test semantic validation (missing env var)."""
    config_path, _ = clean_env
    manager = ConfigurationManager(config_path)
    manager.load()
    
    # Use referencing a non-existent env var
    invalid_env = """
source:
  type: fabric
  workspace_id: "${NON_EXISTENT_VAR}"
  model: "*"
targets:
  - type: snowflake_semantic_view
    database: "TEST_DB"
    schema: "TEST_SCHEMA"
"""
    manager.update_text(invalid_env, source="test")
    
    # Should be invalid due to missing env var
    assert manager.is_valid() is False
    assert any("Missing environment variable" in e["message"] for e in manager._validation_errors)
