"""
Global Configuration Manager for Semabridge.

Manages the system-wide ``config.yaml`` which is **separate** from the
project-level ``semabridge.yaml``.

Responsibilities:
- Define a strict Pydantic schema for global configuration.
- Load / save ``config.yaml``.
- Resolve relative paths in the logging section against the config file location.
- Detect when the config file has been modified since it was last acknowledged
  (hash-based change detection stored in the DuckDB ``semabridge_meta`` table).
- Validate ``intermediate_format`` as OSI / SML only.
- Enforce the "no inline secrets" rule.
"""

from __future__ import annotations

import hashlib
import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from semabridge.core.exceptions import ValidationError as SBValidationError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GLOBAL_CONFIG_FILENAME = "config.yaml"
"""Default file name for the global configuration."""

SENSITIVE_PATTERNS = (
    "password", "secret", "credential", "token", "api_key", "private_key",
)
"""Substrings whose values must never appear as inline literals."""


# ---------------------------------------------------------------------------
# Pydantic models – strict, typed, validated
# ---------------------------------------------------------------------------

class IntermediateFormat(str, Enum):
    """Valid intermediate format choices."""
    OSI = "OSI"
    SML = "SML"


class CoreSection(BaseModel):
    """Core engine settings."""

    intermediate_format: IntermediateFormat = Field(
        default=IntermediateFormat.OSI,
        description="Preferred intermediate format (OSI or SML).",
    )
    repository_path: str = Field(
        default="~/.semabridge/semabridge.db",
        description="Path to the DuckDB version-control repository.",
    )
    backup_retention: int = Field(
        default=5,
        ge=0,
        description="Number of backup copies to retain.",
    )

    @field_validator("intermediate_format", mode="before")
    @classmethod
    def _normalise_format(cls, v: str) -> str:
        """Accept case-insensitive input and normalise to upper."""
        if isinstance(v, str):
            upper = v.upper()
            if upper not in ("OSI", "SML"):
                raise ValueError(
                    f"Invalid intermediate_format '{v}'. Must be 'OSI' or 'SML'."
                )
            return upper
        return v


class LLMSection(BaseModel):
    """LLM support settings for Tier 4 DAX→SQL conversion."""

    enabled: bool = Field(
        default=False,
        description="Enable / disable LLM support.",
    )
    model: str = Field(
        default="gpt-4o-mini",
        description="Preferred AI model identifier.",
    )
    timeout_seconds: int = Field(default=30, ge=1)
    max_retries: int = Field(default=3, ge=0)
    api_key_env: str = Field(
        default="OPENAI_API_KEY",
        description="Name of the environment variable holding the API key.",
    )


class LoggingSection(BaseModel):
    """Logging configuration."""

    level: str = Field(default="INFO")
    format: str = Field(default="text")
    dir: str = Field(
        default="~/.semabridge/logs",
        description="Directory where log files are written.",
    )
    redact_secrets: bool = Field(default=True)

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        allowed = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        if v.upper() not in allowed:
            raise ValueError(
                f"Invalid logging level '{v}'. Allowed: {', '.join(allowed)}"
            )
        return v.upper()

    @field_validator("format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        allowed = ("text", "json")
        if v.lower() not in allowed:
            raise ValueError(
                f"Invalid logging format '{v}'. Allowed: {', '.join(allowed)}"
            )
        return v.lower()


class ConcurrencySection(BaseModel):
    """Concurrency / thread-pool limits."""

    max_concurrent_models: int = Field(
        default=4,
        ge=1,
        description="Maximum number of models processed concurrently.",
    )
    thread_pool_size: int = Field(
        default=8,
        ge=1,
        description="Size of the worker thread pool.",
    )


class OptionsSection(BaseModel):
    """Miscellaneous options."""

    global_exclude_models: List[str] = Field(default_factory=list)


class GlobalConfig(BaseModel):
    """
    Top-level Pydantic model for *config.yaml* (global configuration).

    This is intentionally **separate** from ``semabridge.yaml`` which stores
    per-project pipeline settings.
    """

    core: CoreSection = Field(default_factory=CoreSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    concurrency: ConcurrencySection = Field(default_factory=ConcurrencySection)
    options: OptionsSection = Field(default_factory=OptionsSection)

    # Self-referential: the path to this very config file.
    config_path: str = Field(
        default="",
        description="Absolute path to this config.yaml file (auto-populated).",
    )

    # ---- cross-field / whole-model validation ----------------------------

    @model_validator(mode="after")
    def _reject_inline_secrets(self) -> "GlobalConfig":
        """Scan all string values for inline secrets and reject them."""
        flat = _flatten_dict(self.model_dump())
        for key, value in flat.items():
            if not isinstance(value, str):
                continue
            key_lower = key.lower()
            for pattern in SENSITIVE_PATTERNS:
                # Only flag fields whose *name* looks credential-like AND
                # whose value does NOT look like an env-var *name*.
                if pattern in key_lower and not value.startswith("${"):
                    # Allow env var *names* (UPPER_CASE identifiers)
                    if value != value.upper() or " " in value:
                        raise ValueError(
                            f"Inline secret detected in '{key}'. "
                            f"Secrets must be stored as environment variables, "
                            f"not in config files."
                        )
        return self


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten_dict(
    d: Dict, parent_key: str = "", sep: str = ".",
) -> Dict[str, object]:
    """Recursively flatten a nested dict for secret scanning."""
    items: List = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# GlobalConfigManager – load, validate, detect changes
# ---------------------------------------------------------------------------

class GlobalConfigManager:
    """
    Manages the lifecycle of the global ``config.yaml``.

    Usage::

        mgr = GlobalConfigManager()
        cfg = mgr.load()            # GlobalConfig | None
        if mgr.has_changed():
            ...  # prompt user
        mgr.acknowledge()            # persist current hash
    """

    META_KEY_HASH = "global_config_hash"

    def __init__(self, config_path: Optional[Path] = None) -> None:
        """
        Initialise with an explicit path or fall back to the default location.

        Args:
            config_path: Absolute or expandable path to ``config.yaml``.
        """
        if config_path is not None:
            self._path = Path(config_path).expanduser().resolve()
        else:
            # Check for local config override first (Requirement for clean root)
            local_config = Path.cwd() / "config" / GLOBAL_CONFIG_FILENAME
            if local_config.exists():
                self._path = local_config
            else:
                self._path = Path.home() / ".semabridge" / GLOBAL_CONFIG_FILENAME

        self._config: Optional[GlobalConfig] = None
        self._current_hash: Optional[str] = None

    @property
    def path(self) -> Path:
        """Return the resolved config file path."""
        return self._path

    @property
    def config(self) -> Optional[GlobalConfig]:
        """Return the most recently loaded config (``None`` before ``load``)."""
        return self._config

    # ---- loading ---------------------------------------------------------

    def load(self) -> Optional[GlobalConfig]:
        """
        Load and validate ``config.yaml``.

        Returns:
            Validated ``GlobalConfig`` or ``None`` if the file does not exist.

        Raises:
            SBValidationError: If the file is malformed or fails validation.
        """
        if not self._path.exists():
            logger.debug(f"Global config not found at {self._path}")
            return None

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            raise SBValidationError(
                f"Failed to parse global config at {self._path}: {exc}",
                field="config.yaml",
            )

        if not isinstance(raw, dict):
            raise SBValidationError(
                "Global config.yaml must be a YAML mapping (dictionary).",
                field="config.yaml",
            )

        # Inject self-referential path if missing
        if "config_path" not in raw or not raw["config_path"]:
            raw["config_path"] = str(self._path)

        try:
            self._config = GlobalConfig(**raw)
        except Exception as exc:
            raise SBValidationError(
                f"Global config validation failed: {exc}",
                field="config.yaml",
                errors=[str(exc)],
            )

        # Resolve relative logging dir against config file location
        self._resolve_relative_paths()

        self._current_hash = _file_sha256(self._path)
        logger.info(f"Loaded global config from {self._path}")
        return self._config

    # ---- relative-path resolution ----------------------------------------

    def _resolve_relative_paths(self) -> None:
        """Resolve relative paths in the logging section relative to the
        config file's directory."""
        if self._config is None:
            return

        config_dir = self._path.parent

        log_dir = self._config.logging.dir
        log_path = Path(log_dir)
        if not log_path.is_absolute():
            # Tilde-expand first, then resolve against config dir
            expanded = Path(os.path.expanduser(log_dir))
            if not expanded.is_absolute():
                resolved = (config_dir / expanded).resolve()
                self._config.logging.dir = str(resolved)
                logger.debug(f"Resolved logging.dir '{log_dir}' → '{resolved}'")

        repo_path = self._config.core.repository_path
        repo = Path(repo_path)
        if not repo.is_absolute():
            expanded = Path(os.path.expanduser(repo_path))
            if not expanded.is_absolute():
                resolved = (config_dir / expanded).resolve()
                self._config.core.repository_path = str(resolved)
                logger.debug(
                    f"Resolved core.repository_path '{repo_path}' → '{resolved}'"
                )

    # ---- change detection ------------------------------------------------

    def has_changed(self) -> bool:
        """
        Check whether ``config.yaml`` has been modified since last acknowledged.

        Compares the current file hash against the hash stored in the DuckDB
        ``semabridge_meta`` table.

        Returns:
            ``True`` if the file has changed or has never been acknowledged.
        """
        if not self._path.exists():
            return False

        current_hash = _file_sha256(self._path)
        stored_hash = self._read_stored_hash()

        if stored_hash is None:
            # First time – treat as changed so the user is prompted
            return True

        return current_hash != stored_hash

    def acknowledge(self) -> None:
        """
        Persist the current file hash, marking the config as "accepted".

        Should be called after the user confirms they want to apply changes.
        """
        if not self._path.exists():
            return

        current_hash = _file_sha256(self._path)
        self._write_stored_hash(current_hash)
        logger.debug("Global config acknowledged (hash stored)")

    # ---- DuckDB meta helpers ---------------------------------------------

    def _get_repo_path(self) -> Optional[Path]:
        """Resolve the DuckDB repository path from the config or default."""
        if self._config:
            return Path(os.path.expanduser(self._config.core.repository_path))
        # Fallback to default
        return Path.home() / ".semabridge" / "semabridge.db"

    def _read_stored_hash(self) -> Optional[str]:
        """Read the stored config hash from DuckDB."""
        repo = self._get_repo_path()
        if repo is None or not repo.exists():
            return None

        try:
            import duckdb
            conn = duckdb.connect(str(repo), read_only=True)
            try:
                row = conn.execute(
                    "SELECT value FROM semabridge_meta WHERE key = ?",
                    [self.META_KEY_HASH],
                ).fetchone()
                return row[0] if row else None
            finally:
                conn.close()
        except Exception:
            return None

    def _write_stored_hash(self, hash_value: str) -> None:
        """Write the config hash to DuckDB."""
        repo = self._get_repo_path()
        if repo is None or not repo.exists():
            logger.warning(
                "Cannot persist config hash: DuckDB repository not found."
            )
            return

        try:
            import duckdb
            conn = duckdb.connect(str(repo))
            try:
                conn.execute(
                    """
                    INSERT INTO semabridge_meta (key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    [self.META_KEY_HASH, hash_value],
                )
                conn.execute("CHECKPOINT")
            finally:
                conn.close()
        except Exception as exc:
            logger.warning(f"Failed to persist config hash: {exc}")

    # ---- validation API (called by ``semabridge validate``) ---------------

    def validate(self) -> List[str]:
        """
        Run full validation on the loaded config.

        Returns:
            List of human-readable error strings (empty = valid).
        """
        errors: List[str] = []

        if not self._path.exists():
            errors.append(f"Global config not found at {self._path}")
            return errors

        try:
            self.load()
        except SBValidationError as exc:
            errors.append(str(exc))
            return errors

        if self._config is None:
            errors.append("Config loaded but resulted in None.")
            return errors

        # LLM: if enabled the api_key_env must resolve
        if self._config.llm.enabled:
            key_env = self._config.llm.api_key_env
            if not os.environ.get(key_env):
                errors.append(
                    f"LLM is enabled but the environment variable "
                    f"'{key_env}' is not set."
                )

        return errors
