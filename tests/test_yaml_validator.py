"""
Unit Tests for YAML Validator.

Tests line-number accuracy, invalid enum values, missing env vars, and unknown keys.
"""

import os
import pytest
from pathlib import Path
from tempfile import NamedTemporaryFile

from semabridge.formats.yaml_validator import (
    YAMLValidator,
    ValidationError,
    ValidationWarning,
    validate_yaml_file,
    format_validation_errors,
)
from semabridge.core.config_loader import (
    load_and_validate_configs,
    ValidationErrors,
)
from semabridge.formats.schema import suggest_similar_key, ALL_KNOWN_KEYS


@pytest.fixture
def temp_yaml_file(tmp_path):
    """Create a temporary YAML file for testing."""
    def _create(content: str, name: str = "test.yaml"):
        file_path = tmp_path / name
        file_path.write_text(content, encoding="utf-8")
        return file_path
    return _create


class TestLineNumberAccuracy:
    """Tests for line number accuracy in error messages."""
    
    def test_enum_error_line_number(self, temp_yaml_file):
        """Test that enum errors include correct line number."""
        yaml_content = """# Comment line 1
# Comment line 2
source:
  type: snowfake  # Line 4 - typo
model_name: Test
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        # Find the source.type error
        type_errors = [e for e in errors if "source.type" in e.key_path]
        assert len(type_errors) == 1
        assert type_errors[0].line == 4
    
    def test_unknown_key_line_number(self, temp_yaml_file):
        """Test that unknown key errors include correct line number."""
        yaml_content = """source:
  type: snowflake
model_name: Test
unknown_key: value
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        unknown_errors = [e for e in errors if "unknown_key" in e.key_path]
        assert len(unknown_errors) == 1
        assert unknown_errors[0].line == 4
    
    def test_nested_error_line_number(self, temp_yaml_file):
        """Test line numbers for nested field errors."""
        yaml_content = """source:
  type: snowflake
target:
  type: fabric
  deploy: true
sync:
  direction: invalid_direction
model_name: Test
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        direction_errors = [e for e in errors if "direction" in e.key_path]
        assert len(direction_errors) == 1
        assert direction_errors[0].line == 7


class TestInvalidEnumValues:
    """Tests for invalid enum value detection."""
    
    def test_invalid_source_type(self, temp_yaml_file):
        """Test detection of invalid source.type."""
        yaml_content = """source:
  type: postgres
model_name: Test
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        assert any("source.type" in e.key_path for e in errors)
        error = [e for e in errors if "source.type" in e.key_path][0]
        assert "postgres" in str(error.found)
        assert "snowflake" in str(error.expected) or "fabric" in str(error.expected)
    
    def test_invalid_sync_direction(self, temp_yaml_file):
        """Test detection of invalid sync.direction."""
        yaml_content = """source:
  type: snowflake
model_name: Test
sync:
  direction: source_to_dest
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        assert any("direction" in e.key_path for e in errors)
    
    def test_invalid_logging_level(self, temp_yaml_file):
        """Test detection of invalid logging.level."""
        yaml_content = """source:
  type: snowflake
model_name: Test
logging:
  level: VERBOSE
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        assert any("logging.level" in e.key_path for e in errors)
    
    def test_invalid_logging_format(self, temp_yaml_file):
        """Test detection of invalid logging.format."""
        yaml_content = """source:
  type: snowflake
model_name: Test
logging:
  format: xml
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        assert any("logging.format" in e.key_path for e in errors)
    
    def test_suggestion_for_typo(self, temp_yaml_file):
        """Test that similar value suggestions are provided."""
        yaml_content = """source:
  type: snowfake
model_name: Test
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        type_errors = [e for e in errors if "source.type" in e.key_path]
        assert len(type_errors) == 1
        assert type_errors[0].suggestion is not None
        assert "snowflake" in type_errors[0].suggestion


class TestMissingEnvVars:
    """Tests for missing environment variable detection."""
    
    def test_missing_auth_env_var(self, temp_yaml_file, monkeypatch):
        """Test detection of missing environment variable in auth."""
        monkeypatch.delenv("NONEXISTENT_VAR_12345", raising=False)
        
        yaml_content = """source:
  type: snowflake
model_name: Test
auth:
  password_env: NONEXISTENT_VAR_12345
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=True)
        
        assert any("NONEXISTENT_VAR_12345" in e.message for e in errors)
    
    def test_set_env_var_passes(self, temp_yaml_file, monkeypatch):
        """Test that set env vars pass validation."""
        monkeypatch.setenv("TEST_PASSWORD_VAR", "secret123")
        
        yaml_content = """source:
  type: snowflake
model_name: Test
auth:
  password_env: TEST_PASSWORD_VAR
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=True)
        
        # Should not have env var errors
        env_errors = [e for e in errors if "TEST_PASSWORD_VAR" in e.message]
        assert len(env_errors) == 0


class TestUnknownKeys:
    """Tests for unknown key detection."""
    
    def test_unknown_top_level_key(self, temp_yaml_file):
        """Test detection of unknown top-level key."""
        yaml_content = """source:
  type: snowflake
model_name: Test
foobar: value
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        assert any("foobar" in e.key_path for e in errors)
    
    def test_unknown_nested_key(self, temp_yaml_file):
        """Test detection of unknown nested key."""
        yaml_content = """source:
  type: snowflake
  unknown_nested: value
model_name: Test
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        assert any("unknown_nested" in e.key_path for e in errors)
    
    def test_did_you_mean_suggestion(self, temp_yaml_file):
        """Test 'did you mean' suggestions for typos."""
        yaml_content = """source:
  type: snowflake
model_name: Test
loging:
  level: INFO
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        loging_errors = [e for e in errors if "loging" in e.key_path]
        assert len(loging_errors) == 1
        assert loging_errors[0].suggestion is not None
        assert "logging" in loging_errors[0].suggestion


class TestSchemaSuggestions:
    """Tests for schema key suggestions."""
    
    def test_suggest_similar_key(self):
        """Test suggest_similar_key function."""
        # Test typo corrections
        assert suggest_similar_key("loging") == "logging"
        assert suggest_similar_key("sorce") == "source"
        assert suggest_similar_key("targte") == "target"
    
    def test_known_keys_completeness(self):
        """Test that ALL_KNOWN_KEYS contains expected keys."""
        assert "source" in ALL_KNOWN_KEYS
        assert "target" in ALL_KNOWN_KEYS
        assert "logging" in ALL_KNOWN_KEYS
        assert "model_name" in ALL_KNOWN_KEYS
        assert "source.type" in ALL_KNOWN_KEYS
        assert "logging.level" in ALL_KNOWN_KEYS


class TestValidConfiguration:
    """Tests for valid configurations."""
    
    def test_valid_minimal_config(self, temp_yaml_file):
        """Test that minimal valid config passes."""
        yaml_content = """source:
  type: snowflake
model_name: TestModel
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        assert len(errors) == 0
        assert config is not None
    
    def test_valid_full_config(self, temp_yaml_file):
        """Test that full valid config passes."""
        yaml_content = """source:
  type: snowflake
  database: mydb
  schema_name: myschema
target:
  type: fabric
  deploy: true
model_name: TestModel
sync_direction: source_to_target
logging:
  level: INFO
  format: text
version_tag: v1.0.0
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        assert len(errors) == 0
        assert config is not None


class TestPlaintextCredentials:
    """Tests for plaintext credential detection."""
    
    def test_plaintext_password_detected(self, temp_yaml_file):
        """Test that plaintext passwords are flagged (either as unknown key or plaintext)."""
        yaml_content = """source:
  type: snowflake
model_name: Test
password: mysecretpassword
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        # The password key should be flagged - either as unknown key or plaintext
        # (since 'password' is not in schema, it gets caught by unknown key check)
        assert any(
            "password" in e.key_path or "Plain-text credential" in e.message 
            for e in errors
        )
    
    def test_env_var_password_ok(self, temp_yaml_file):
        """Test that ${VAR} password references are allowed (still flags unknown key)."""
        yaml_content = """source:
  type: snowflake
model_name: Test
password: ${MY_SECRET_VAR}
"""
        path = temp_yaml_file(yaml_content)
        config, errors, warnings = validate_yaml_file(path, check_env_vars=False)
        
        # Should not flag env var references as plaintext (but will flag as unknown key)
        plaintext_errors = [e for e in errors if "Plain-text credential" in e.message]
        assert len(plaintext_errors) == 0


class TestErrorFormatting:
    """Tests for error message formatting."""
    
    def test_error_format_includes_location(self):
        """Test that formatted errors include file, line, column."""
        error = ValidationError(
            file="test.yaml",
            line=10,
            column=5,
            message="Test error",
            key_path="source.type",
        )
        formatted = error.format()
        
        assert "test.yaml" in formatted
        assert "line 10" in formatted
        assert "column 5" in formatted
    
    def test_error_format_includes_suggestion(self):
        """Test that formatted errors include suggestions."""
        error = ValidationError(
            file="test.yaml",
            line=1,
            column=1,
            message="Invalid value",
            suggestion="Did you mean 'snowflake'?",
        )
        formatted = error.format()
        
        assert "Did you mean 'snowflake'?" in formatted
    
    def test_multiple_errors_formatting(self):
        """Test formatting of multiple errors."""
        errors = [
            ValidationError(file="test.yaml", line=1, column=1, message="Error 1"),
            ValidationError(file="test.yaml", line=5, column=1, message="Error 2"),
        ]
        formatted = format_validation_errors(errors)
        
        assert "Error 1" in formatted
        assert "Error 2" in formatted
