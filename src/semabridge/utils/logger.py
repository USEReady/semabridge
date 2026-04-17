"""
Logging utilities for Semabridge.

Provides consistent logging across the application with:
- Hierarchical log-level resolution: CLI > project YAML > global config > default (INFO)
- RotatingFileHandler (10 MB, 5 backups) co-located with the DuckDB repository
- Rich console output for interactive CLI sessions
- Thread identifier in every log line for tracing interleaved concurrent ops
- Credential redaction filter that scrubs secrets matching common patterns
- Identical output for both file and console handlers (unified telemetry)
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

# Console for rich output
# force_terminal=True ensures output works even in non-interactive contexts (e.g., async servers)
# legacy_windows=False ensures unicode symbols work on Windows 10+
console = Console(force_terminal=True, legacy_windows=False)

# Logger cache and state
_loggers: dict[str, logging.Logger] = {}
_logging_initialized = False
_last_rotation_warning_time = 0.0

# Unified structured log format — includes threadName for tracing
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | [%(threadName)s] | %(name)s | %(funcName)s | %(message)s"
)

VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Regex patterns for credential scrubbing
_CREDENTIAL_PATTERNS = [
    re.compile(r'(password|passwd|pwd|secret|token|api_key|apikey|auth|credential|bearer)\s*[=:]\s*\S+', re.IGNORECASE),
    re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*', re.IGNORECASE),
    re.compile(r'[A-Za-z0-9+/]{40,}={0,2}'),  # Base64 blobs ≥40 chars
]

# Repetitive warning throttling (message pattern -> max occurrences)
_THROTTLED_WARNING_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^Skipping metric '\S+", re.IGNORECASE), 6),
    (re.compile(r"^LLM translation low confidence", re.IGNORECASE), 3),
]


class WindowsRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """
    Windows-safe rotating file handler.
    On Windows, RotatingFileHandler periodically fails with PermissionError
    if another process (like Uvicorn reloader) has the log file open.
    This handler catches the error and skips the rotation for that cycle.
    """

    def doRollover(self) -> None:
        """
        Perform a rollover, catching OS-level lock errors on Windows.
        """
        try:
            super().doRollover()
        except (PermissionError, OSError) as e:
            # If the file is locked, we can't rotate it.
            # In a dev environment with a reloader, this is common.
            # Throttle the warning so it doesn't spam the console.
            global _last_rotation_warning_time
            now = time.time()
            if now - _last_rotation_warning_time > 60:  # Only warn once per minute
                sys.stderr.write(
                    f"\n[Logging Warning] Unable to rotate log file: {e}\n"
                    f"Continuing to log to current file until lock is released.\n"
                )
                _last_rotation_warning_time = now


class CredentialRedactionFilter(logging.Filter):
    """Scrub log messages matching known credential patterns.

    Prevents accidental leakage of passwords, tokens, and API keys
    into console output and persistent log files.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact sensitive values in the log message."""
        msg = record.getMessage()
        for pattern in _CREDENTIAL_PATTERNS:
            msg = pattern.sub("[REDACTED]", msg)
        record.msg = msg
        record.args = None  # Prevent re-formatting with original args
        return True


class WarningThrottleFilter(logging.Filter):
    """Suppress repetitive warning spam while preserving first occurrences."""

    def __init__(self) -> None:
        super().__init__()
        self._counts: dict[str, int] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING:
            return True

        msg = record.getMessage()
        for pattern, max_occurrences in _THROTTLED_WARNING_PATTERNS:
            if pattern.search(msg):
                key = pattern.pattern
                current = self._counts.get(key, 0)
                if current >= max_occurrences:
                    return False
                self._counts[key] = current + 1
                break
        return True


def resolve_log_level(cli_flag: Optional[str] = None) -> str:
    """
    Resolve the effective logging level using a strict precedence chain.

    Resolution order (highest to lowest):
      1. CLI flag (e.g. --log-level DEBUG)
      2. Project-level semabridge.yaml  -> logging.level
      3. Global config ~/.semabridge/config.yaml -> logging.level
      4. Default: DEBUG

    Args:
        cli_flag: Optional log-level value supplied via the CLI.

    Returns:
        Validated uppercase logging level string.
    """
    # 1. CLI flag
    if cli_flag and cli_flag.upper() in VALID_LEVELS:
        return cli_flag.upper()

    # 2. Project semabridge.yaml
    try:
        import yaml
        from semabridge.core.config_loader import get_project_file_path
        project_cfg_path = get_project_file_path("semabridge.yaml")
        if project_cfg_path.exists():
            cfg = yaml.safe_load(project_cfg_path.read_text(encoding="utf-8")) or {}
            val = cfg.get("logging", {}).get("level")
            if val and val.upper() in VALID_LEVELS:
                return val.upper()
    except Exception:
        pass

    # 3. Global config.yaml
    try:
        import yaml
        global_cfg_path = Path.home() / ".semabridge" / "config.yaml"
        if global_cfg_path.exists():
            cfg = yaml.safe_load(global_cfg_path.read_text(encoding="utf-8")) or {}
            val = cfg.get("logging", {}).get("level")
            if val and val.upper() in VALID_LEVELS:
                return val.upper()
    except Exception:
        pass

    # 4. Default
    return "INFO"


def resolve_log_file_path() -> Path:
    """
    Determine the log file path, co-located with the DuckDB repository.

    Resolution order:
      1. Global config.yaml -> logging.log_file
      2. Global config.yaml -> core.repository_path  (sibling file)
      3. Default: ~/.semabridge/semabridge.log
    """
    default = Path.home() / ".semabridge" / "semabridge.log"
    try:
        import yaml
        global_cfg_path = Path.home() / ".semabridge" / "config.yaml"
        if global_cfg_path.exists():
            cfg = yaml.safe_load(global_cfg_path.read_text(encoding="utf-8")) or {}

            # Explicit log_file key
            explicit = cfg.get("logging", {}).get("log_file")
            if explicit:
                return Path(explicit).expanduser().resolve()

            # Co-locate with repo
            repo_path_raw = cfg.get("core", {}).get("repository_path")
            if repo_path_raw:
                return Path(repo_path_raw).expanduser().resolve().parent / "semabridge.log"
    except Exception:
        pass

    return default


def setup_logging(
    level: Optional[str] = None,
    format_string: Optional[str] = None,
    rich_output: bool = True,
    log_file: Optional[str] = None,
) -> None:
    """
    Set up logging for the application.

    Args:
        level: Logging level. If None, resolved via hierarchy.
        format_string: Optional format string for log messages.
        rich_output: Whether to use Rich for formatted console output.
        log_file: Explicit log file path. If None, resolved dynamically.
    """
    effective_level = level or resolve_log_level()
    log_level = getattr(logging, effective_level.upper(), logging.DEBUG)

    global _logging_initialized
    if _logging_initialized:
        # Just update the level of the root logger and noisy loggers
        logging.getLogger().setLevel(log_level)
        _set_noisy_loggers_level(log_level)
        return

    # Clear existing handlers
    root = logging.getLogger()
    root.handlers.clear()

    # --- Global filters (applied to all handlers) ---
    redaction_filter = CredentialRedactionFilter()
    throttle_filter = WarningThrottleFilter()

    # --- Console handler ---
    if rich_output:
        handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            rich_tracebacks=True,
            tracebacks_show_locals=False,
            show_level=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        handler = logging.StreamHandler(sys.stdout)
        fmt = format_string or LOG_FORMAT
        handler.setFormatter(logging.Formatter(fmt))

    handler.setLevel(log_level)
    handler.addFilter(redaction_filter)
    handler.addFilter(throttle_filter)
    # Ensure output is flushed immediately, especially important for async contexts
    if hasattr(handler, 'stream'):
        handler.stream = sys.stdout
    root.addHandler(handler)

    # --- Rotating file handler (identical output to console) ---
    try:
        file_path = Path(log_file) if log_file else resolve_log_file_path()
        file_path.parent.mkdir(parents=True, exist_ok=True)

        handler_class = logging.handlers.RotatingFileHandler
        if sys.platform == "win32":
            handler_class = WindowsRotatingFileHandler

        file_handler = handler_class(
            str(file_path),
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        file_handler.setLevel(log_level)
        file_handler.addFilter(redaction_filter)
        file_handler.addFilter(throttle_filter)
        root.addHandler(file_handler)
    except Exception:
        # If file handler setup fails (permissions, etc.), proceed with console only
        pass

    root.setLevel(log_level)
    _set_noisy_loggers_level(log_level)
    _logging_initialized = True


def _set_noisy_loggers_level(log_level: int) -> None:
    """Helper to configure log levels for verbose third-party/internal modules."""
    # Reduce noise from third-party libraries and verbose modules
    noisy_loggers = [
        "snowflake.connector",
        "urllib3",
        "httpx",
        "msal",
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "asyncio",
        "starlette",
        "uvicorn",
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    # Keep converter internals concise in normal runs.
    if log_level > logging.DEBUG:
        semabridge_noisy_modules = [
            "semabridge.converter.dax_rule_translator",
            "semabridge.converter.dax_translator",
            "semabridge.converter.gemini_dax_translator",
            "semabridge.converter.gemini_api_service",
            "semabridge.converter.deterministic_translator",
            "semabridge.converter.dax_pipeline",
            "semabridge.converter.measure_dictionary",
            "semabridge.converter.dax_parser",
        ]
        for logger_name in semabridge_noisy_modules:
            logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    if name not in _loggers:
        logger = logging.getLogger(name)
        _loggers[name] = logger
    return _loggers[name]
