"""
LLM Configuration Manager.

Loads and validates LLM-specific project configuration from config.yaml,
including model selection, timeout, retry, and API key settings.

This is separate from the UI ConfigurationManager (config_manager.py)
which handles semabridge.yaml for the sync engine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from semabridge.core.config_loader import ConfigLoadError, MissingEnvVarError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LLMConfig:
    """LLM configuration settings for Tier 4 DAX-to-SQL translation."""

    enabled: bool = False
    model: str = "gemini-1.5-pro"
    timeout_seconds: int = 30
    max_retries: int = 3
    api_key_env: str = "GOOGLE_API_KEY"

    @property
    def api_key(self) -> Optional[str]:
        """Resolve API key from environment variable. Never logged or stored."""
        return os.environ.get(self.api_key_env)

    def validate(self) -> None:
        """
        Validate config when LLM is enabled.

        Raises:
            ConfigLoadError: If model name is empty or timeout/retries are invalid.
            MissingEnvVarError: If required API key env var is not set.
        """
        if not self.enabled:
            return

        if not self.model or not self.model.strip():
            raise ConfigLoadError(
                "LLM is enabled but 'llm.model' is empty. "
                "Specify a model name (e.g., gpt-4o-mini, claude-3-sonnet)."
            )
        if not self.api_key:
            raise MissingEnvVarError(
                self.api_key_env, yaml_path="llm.api_key_env"
            )
        if self.timeout_seconds < 1:
            raise ConfigLoadError("llm.timeout_seconds must be >= 1")
        if self.max_retries < 0:
            raise ConfigLoadError("llm.max_retries must be >= 0")


@dataclass
class PathsConfig:
    """Project paths configuration."""

    duckdb_repo: str = "./data/duckdb"
    logs: str = "./logs"


@dataclass
class LLMProjectConfig:
    """Top-level project config including LLM and paths."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)


class LLMConfigManager:
    """
    Manages LLM-specific project configuration from config.yaml.

    Usage:
        config = LLMConfigManager.load_config()
        if config.llm.enabled:
            print(f"Using LLM model: {config.llm.model}")
    """

    DEFAULT_CONFIG_PATH = "config/config.yaml"

    @staticmethod
    def load_config(path: str = None) -> LLMProjectConfig:
        """
        Load and validate config.yaml for LLM settings.

        Args:
            path: Path to config file. Defaults to config.yaml in project root.

        Returns:
            Validated LLMProjectConfig.

        Raises:
            ConfigLoadError: If config is invalid.
            MissingEnvVarError: If required env var is missing when LLM is enabled.
        """
        config_path = Path(path or LLMConfigManager.DEFAULT_CONFIG_PATH)

        if not config_path.exists():
            logger.info(
                f"Config file '{config_path}' not found, using defaults (LLM disabled)"
            )
            return LLMConfigManager.get_default_config()

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigLoadError(f"Failed to parse '{config_path}': {e}")

        config = LLMConfigManager._parse_config(raw)

        if config.llm.enabled:
            config.llm.validate()

        logger.info(
            f"Loaded LLM config from '{config_path}' "
            f"(LLM={'enabled, model=' + config.llm.model if config.llm.enabled else 'disabled'})"
        )
        return config

    @staticmethod
    def get_default_config() -> LLMProjectConfig:
        """Get default configuration with LLM disabled."""
        return LLMProjectConfig()

    @staticmethod
    def _parse_config(raw: Dict[str, Any]) -> LLMProjectConfig:
        """Parse raw YAML dict into LLMProjectConfig."""
        llm_raw = raw.get("llm", {}) or {}
        paths_raw = raw.get("paths", {}) or {}

        llm = LLMConfig(
            enabled=bool(llm_raw.get("enabled", False)),
            model=str(llm_raw.get("model", "gemini-1.5-pro")),
            timeout_seconds=int(llm_raw.get("timeout_seconds", 30)),
            max_retries=int(llm_raw.get("max_retries", 3)),
            api_key_env=str(llm_raw.get("api_key_env", "GOOGLE_API_KEY")),
        )

        paths = PathsConfig(
            duckdb_repo=str(paths_raw.get("duckdb_repo", "./data/duckdb")),
            logs=str(paths_raw.get("logs", "./logs")),
        )

        return LLMProjectConfig(llm=llm, paths=paths)
