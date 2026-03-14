"""
Tests for Configuration Loader.

Tests environment variable interpolation, config merging, and validation.
"""

import os
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from semabridge.core.config_loader import (
    interpolate_env_vars,
    resolve_env_ref_fields,
    deep_merge,
    mask_sensitive_value,
    load_and_merge_configs,
    validate_config_schema,
    ConfigLoadError,
    MissingEnvVarError,
)


class TestInterpolateEnvVars:
    """Tests for environment variable interpolation."""
    
    def test_simple_interpolation(self, monkeypatch):
        """Test basic ${VAR} interpolation."""
        monkeypatch.setenv("TEST_VAR", "hello")
        result = interpolate_env_vars("prefix_${TEST_VAR}_suffix")
        assert result == "prefix_hello_suffix"
    
    def test_multiple_vars(self, monkeypatch):
        """Test multiple variables in one string."""
        monkeypatch.setenv("VAR1", "a")
        monkeypatch.setenv("VAR2", "b")
        result = interpolate_env_vars("${VAR1} and ${VAR2}")
        assert result == "a and b"
    
    def test_default_value_used(self, monkeypatch):
        """Test that default value is used when var is not set."""
        monkeypatch.delenv("UNSET_VAR", raising=False)
        result = interpolate_env_vars("${UNSET_VAR:-default_value}")
        assert result == "default_value"
    
    def test_default_value_not_used(self, monkeypatch):
        """Test that env var takes precedence over default."""
        monkeypatch.setenv("SET_VAR", "actual")
        result = interpolate_env_vars("${SET_VAR:-default}")
        assert result == "actual"
    
    def test_empty_default_value(self, monkeypatch):
        """Test empty string as default value."""
        monkeypatch.delenv("UNSET_VAR", raising=False)
        result = interpolate_env_vars("${UNSET_VAR:-}")
        assert result == ""
    
    def test_missing_var_raises_error(self, monkeypatch):
        """Test that missing required variable raises error."""
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(MissingEnvVarError) as exc_info:
            interpolate_env_vars("${MISSING_VAR}")
        assert "MISSING_VAR" in str(exc_info.value)
    
    def test_missing_var_no_fail(self, monkeypatch):
        """Test that fail_on_missing=False returns original pattern."""
        monkeypatch.delenv("MISSING_VAR", raising=False)
        result = interpolate_env_vars("${MISSING_VAR}", fail_on_missing=False)
        assert result == "${MISSING_VAR}"


class TestResolveEnvRefFields:
    """Tests for _env field resolution."""
    
    def test_env_field_resolved(self, monkeypatch):
        """Test that password_env: VAR resolves to password: value."""
        monkeypatch.setenv("MY_SECRET", "secret123")
        data = {"password_env": "MY_SECRET"}
        result = resolve_env_ref_fields(data)
        assert "password" in result
        assert result["password"] == "secret123"
        assert "password_env" not in result
    
    def test_nested_env_field(self, monkeypatch):
        """Test nested _env field resolution."""
        monkeypatch.setenv("DB_PASS", "dbpass")
        data = {
            "database": {
                "connection_env": "DB_PASS"
            }
        }
        result = resolve_env_ref_fields(data)
        assert result["database"]["connection"] == "dbpass"
    
    def test_env_field_missing_raises(self, monkeypatch):
        """Test that missing env var raises error."""
        monkeypatch.delenv("MISSING_SECRET", raising=False)
        data = {"api_key_env": "MISSING_SECRET"}
        with pytest.raises(MissingEnvVarError):
            resolve_env_ref_fields(data)
    
    def test_string_interpolation_in_values(self, monkeypatch):
        """Test ${VAR} interpolation in regular string values."""
        monkeypatch.setenv("ENDPOINT", "https://api.example.com")
        data = {"url": "${ENDPOINT}/v1"}
        result = resolve_env_ref_fields(data)
        assert result["url"] == "https://api.example.com/v1"


class TestDeepMerge:
    """Tests for deep dictionary merging."""
    
    def test_simple_merge(self):
        """Test basic merge of two dicts."""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}
    
    def test_nested_merge(self):
        """Test nested dictionary merge."""
        base = {"level1": {"a": 1, "b": 2}}
        override = {"level1": {"b": 3, "c": 4}}
        result = deep_merge(base, override)
        assert result == {"level1": {"a": 1, "b": 3, "c": 4}}
    
    def test_list_override(self):
        """Test that lists are replaced, not merged."""
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = deep_merge(base, override)
        assert result == {"items": [4, 5]}
    
    def test_base_unchanged(self):
        """Test that base dictionary is not mutated."""
        base = {"a": 1}
        override = {"b": 2}
        deep_merge(base, override)
        assert base == {"a": 1}
    
    def test_deeply_nested(self):
        """Test deeply nested merge."""
        base = {"l1": {"l2": {"l3": {"a": 1, "b": 2}}}}
        override = {"l1": {"l2": {"l3": {"b": 3}}}}
        result = deep_merge(base, override)
        assert result["l1"]["l2"]["l3"] == {"a": 1, "b": 3}


class TestMaskSensitiveValue:
    """Tests for sensitive value masking."""
    
    def test_password_masked(self):
        """Test that password fields are masked."""
        result = mask_sensitive_value("password", "secret123")
        assert result == "********"
    
    def test_secret_masked(self):
        """Test that secret fields are masked."""
        result = mask_sensitive_value("client_secret", "abcdef")
        assert result == "********"
    
    def test_api_key_masked(self):
        """Test that api_key fields are masked."""
        result = mask_sensitive_value("api_key", "key123")
        assert result == "********"
    
    def test_normal_value_not_masked(self):
        """Test that normal values are not masked."""
        result = mask_sensitive_value("name", "John")
        assert result == "John"
    
    def test_nested_sensitive_value(self):
        """Test masking in nested dictionaries."""
        data = {"user": "admin", "credentials": {"password": "secret"}}
        result = mask_sensitive_value("config", data)
        assert result["user"] == "admin"
        assert result["credentials"]["password"] == "********"


class TestLoadAndMergeConfigs:
    """Tests for loading and merging multiple config files."""
    
    def test_single_file_load(self, tmp_path):
        """Test loading a single config file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("model_name: test\nsource:\n  type: snowflake")
        
        merged, paths = load_and_merge_configs([config_file], resolve_env=False)
        assert merged["model_name"] == "test"
        assert merged["source"]["type"] == "snowflake"
        assert len(paths) == 1
    
    def test_multiple_file_merge(self, tmp_path):
        """Test merging multiple config files."""
        base_file = tmp_path / "base.yaml"
        base_file.write_text("model_name: base\nsource:\n  type: snowflake")
        
        override_file = tmp_path / "override.yaml"
        override_file.write_text("model_name: override")
        
        merged, paths = load_and_merge_configs([base_file, override_file], resolve_env=False)
        assert merged["model_name"] == "override"
        assert merged["source"]["type"] == "snowflake"  # From base
        assert len(paths) == 2
    
    def test_file_not_found(self, tmp_path):
        """Test error when file not found."""
        missing_file = tmp_path / "missing.yaml"
        with pytest.raises(ConfigLoadError) as exc_info:
            load_and_merge_configs([missing_file])
        assert "not found" in str(exc_info.value)
    
    def test_invalid_yaml(self, tmp_path):
        """Test error on invalid YAML."""
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("not: valid: yaml: {{{}}")
        
        with pytest.raises(ConfigLoadError) as exc_info:
            load_and_merge_configs([bad_file])
        assert "Invalid YAML" in str(exc_info.value)


class TestValidateConfigSchema:
    """Tests for config schema validation."""
    
    def test_valid_config(self):
        """Test that valid config passes validation."""
        config = {
            "source": {"type": "snowflake"},
            "model_name": "test",
        }
        errors = validate_config_schema(config)
        assert len(errors) == 0
    
    def test_missing_source(self):
        """Test error when source is missing."""
        config = {"model_name": "test"}
        errors = validate_config_schema(config)
        assert any("source" in e for e in errors)
    
    def test_missing_model_name(self):
        """Test error when model_name is missing."""
        config = {"source": {"type": "snowflake"}}
        errors = validate_config_schema(config)
        assert any("model_name" in e for e in errors)
    
    def test_invalid_source_type(self):
        """Test error on invalid source type."""
        config = {
            "source": {"type": "invalid"},
            "model_name": "test",
        }
        errors = validate_config_schema(config)
        assert any("source.type" in e for e in errors)
    
    def test_invalid_sync_direction(self):
        """Test error on invalid sync direction."""
        config = {
            "source": {"type": "snowflake"},
            "model_name": "test",
            "sync_direction": "invalid",
        }
        errors = validate_config_schema(config)
        assert any("sync_direction" in e for e in errors)
    
    def test_valid_logging_config(self):
        """Test valid logging configuration."""
        config = {
            "source": {"type": "snowflake"},
            "model_name": "test",
            "logging": {"level": "DEBUG", "format": "json"},
        }
        errors = validate_config_schema(config)
        assert len(errors) == 0
    
    def test_invalid_logging_level(self):
        """Test error on invalid logging level."""
        config = {
            "source": {"type": "snowflake"},
            "model_name": "test",
            "logging": {"level": "VERBOSE"},
        }
        errors = validate_config_schema(config)
        assert any("logging.level" in e for e in errors)
    
    def test_invalid_logging_format(self):
        """Test error on invalid logging format."""
        config = {
            "source": {"type": "snowflake"},
            "model_name": "test",
            "logging": {"format": "xml"},
        }
        errors = validate_config_schema(config)
        assert any("logging.format" in e for e in errors)
