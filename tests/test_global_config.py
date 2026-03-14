"""
Tests for semabridge.core.global_settings — GlobalConfig and GlobalConfigManager.

Covers:
  - Default construction
  - intermediate_format validation (OSI/SML only)
  - Relative path resolution
  - Config file loading
  - Change detection
"""

import os
import textwrap
from pathlib import Path

import pytest

from semabridge.core.global_settings import (
    GlobalConfig,
    GlobalConfigManager,
    IntermediateFormat,
)


# ---------------------------------------------------------------------------
# Unit tests for GlobalConfig model
# ---------------------------------------------------------------------------

class TestGlobalConfigModel:
    """Validate the Pydantic model constraints."""

    def test_defaults(self) -> None:
        """Default construction should produce valid config with LLM disabled."""
        cfg = GlobalConfig()
        assert cfg.core.intermediate_format == IntermediateFormat.OSI
        assert cfg.llm.enabled is False
        assert cfg.llm.model == "gpt-4o-mini"
        assert cfg.concurrency.max_concurrent_models == 4
        assert cfg.concurrency.thread_pool_size == 8
        assert cfg.logging.level == "INFO"
        assert cfg.logging.format == "text"

    def test_intermediate_format_osi(self) -> None:
        cfg = GlobalConfig(core={"intermediate_format": "osi"})
        assert cfg.core.intermediate_format == IntermediateFormat.OSI

    def test_intermediate_format_sml(self) -> None:
        cfg = GlobalConfig(core={"intermediate_format": "sml"})
        assert cfg.core.intermediate_format == IntermediateFormat.SML

    def test_intermediate_format_invalid(self) -> None:
        """Any value other than OSI/SML must be rejected."""
        with pytest.raises(Exception, match="intermediate_format"):
            GlobalConfig(core={"intermediate_format": "INVALID"})

    def test_concurrency_positive_only(self) -> None:
        """Concurrency values must be >= 1."""
        with pytest.raises(Exception):
            GlobalConfig(concurrency={"max_concurrent_models": 0})
        with pytest.raises(Exception):
            GlobalConfig(concurrency={"thread_pool_size": -1})

    def test_logging_level_validation(self) -> None:
        """Invalid log level should be rejected."""
        with pytest.raises(Exception, match="level"):
            GlobalConfig(logging={"level": "TRACE"})

    def test_logging_format_validation(self) -> None:
        """Invalid log format should be rejected."""
        with pytest.raises(Exception, match="format"):
            GlobalConfig(logging={"format": "xml"})

    def test_config_path_field(self) -> None:
        """config_path should be a string, defaults to empty."""
        cfg = GlobalConfig()
        assert cfg.config_path == ""

        cfg2 = GlobalConfig(config_path="/some/path/config.yaml")
        assert cfg2.config_path == "/some/path/config.yaml"


# ---------------------------------------------------------------------------
# Integration tests for GlobalConfigManager
# ---------------------------------------------------------------------------

class TestGlobalConfigManager:
    """Test loading, validation, and change detection."""

    def test_load_missing_file(self, tmp_path: Path) -> None:
        """Loading from a non-existent path returns None."""
        mgr = GlobalConfigManager(config_path=tmp_path / "does_not_exist.yaml")
        result = mgr.load()
        assert result is None

    def test_load_valid_file(self, tmp_path: Path) -> None:
        """A minimal valid config.yaml loads successfully."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            core:
              intermediate_format: SML
              repository_path: ./semabridge.db
            llm:
              enabled: false
              model: gpt-4o-mini
            logging:
              level: DEBUG
              format: text
              dir: ./logs
            concurrency:
              max_concurrent_models: 2
              thread_pool_size: 4
        """), encoding="utf-8")

        mgr = GlobalConfigManager(config_path=config_file)
        cfg = mgr.load()

        assert cfg is not None
        assert cfg.core.intermediate_format == IntermediateFormat.SML
        assert cfg.llm.enabled is False
        assert cfg.concurrency.max_concurrent_models == 2

    def test_load_invalid_intermediate_format(self, tmp_path: Path) -> None:
        """An invalid intermediate_format should raise a validation error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            core:
              intermediate_format: FOOBAR
        """), encoding="utf-8")

        mgr = GlobalConfigManager(config_path=config_file)
        with pytest.raises(Exception, match="intermediate_format"):
            mgr.load()

    def test_relative_path_resolution(self, tmp_path: Path) -> None:
        """Relative logging.dir is resolved against the config file location."""
        config_file = tmp_path / "sub" / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(textwrap.dedent("""\
            logging:
              dir: ./my_logs
        """), encoding="utf-8")

        mgr = GlobalConfigManager(config_path=config_file)
        cfg = mgr.load()

        assert cfg is not None
        resolved = Path(cfg.logging.dir)
        assert resolved.is_absolute()
        assert "my_logs" in str(resolved)

    def test_validate_returns_errors_for_missing_file(self, tmp_path: Path) -> None:
        """validate() on a missing file should return an error list."""
        mgr = GlobalConfigManager(config_path=tmp_path / "missing.yaml")
        errors = mgr.validate()
        assert len(errors) > 0
        assert "not found" in errors[0].lower()
