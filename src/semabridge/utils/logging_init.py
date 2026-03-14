"""
Centralized Logging Initialization.

Implements strict logging level precedence:
1. CLI argument (--log-level) - highest priority
2. Project YAML config (logging.level)
3. Default Python logging level (INFO)

Also supports JSON format output for structured logging.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Literal, Optional

from rich.console import Console
from rich.logging import RichHandler

from semabridge.utils.logger import console as default_console

logger = logging.getLogger(__name__)

# Track if logging has been initialized for the current project
_logging_initialized = False


class JsonFormatter(logging.Formatter):
    """
    Format log records as JSON for structured logging.
    
    Output format:
    {
        "timestamp": "2024-01-24T12:00:00.000000Z",
        "level": "INFO",
        "logger": "semabridge.core.executor",
        "message": "Step 1: Validating configuration..."
    }
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_dict = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Include exception info if present
        if record.exc_info:
            log_dict["exception"] = self.formatException(record.exc_info)
        
        # Include extra fields if present
        if hasattr(record, "project_id"):
            log_dict["project_id"] = record.project_id
        if hasattr(record, "run_id"):
            log_dict["run_id"] = record.run_id
        if hasattr(record, "step_number"):
            log_dict["step_number"] = record.step_number
        
        return json.dumps(log_dict)


def setup_logging_with_config(
    level: str = "INFO",
    format_type: Literal["text", "json"] = "text",
    format_string: Optional[str] = None,
    rich_output: bool = True,
) -> None:
    """
    Set up logging with specified configuration.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_type: Output format ('text' or 'json')
        format_string: Optional format string for text output
        rich_output: Whether to use Rich for formatted text output
    """
    global _logging_initialized
    
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Clear existing handlers
    root = logging.getLogger()
    root.handlers.clear()
    
    if format_type == "json":
        # JSON output for structured logging
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
    elif rich_output and format_type == "text":
        # Rich formatted output
        console = Console()
        handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            rich_tracebacks=True,
            tracebacks_show_locals=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        # Standard text output
        handler = logging.StreamHandler(sys.stdout)
        fmt = format_string or "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
    
    handler.setLevel(log_level)
    root.addHandler(handler)
    root.setLevel(log_level)
    
    # Reduce noise from third-party libraries
    logging.getLogger("snowflake.connector").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)
    
    _logging_initialized = True
    logger.debug(f"Logging initialized: level={level}, format={format_type}")


def initialize_logging(
    cli_log_level: Optional[str] = None,
    yaml_log_level: Optional[str] = None,
    yaml_log_format: Optional[str] = None,
    verbose: bool = False,
) -> str:
    """
    Initialize logging with proper precedence.
    
    Precedence order (highest to lowest):
    1. CLI argument (--log-level)
    2. CLI verbose flag (--verbose) - sets DEBUG
    3. Project YAML config (logging.level)
    4. Default (INFO)
    
    Args:
        cli_log_level: Log level from CLI --log-level argument
        yaml_log_level: Log level from YAML logging.level
        yaml_log_format: Log format from YAML logging.format ('text' or 'json')
        verbose: CLI verbose flag
        
    Returns:
        The effective log level that was set
    """
    # Determine effective level using precedence
    if cli_log_level:
        effective_level = cli_log_level.upper()
        source = "CLI --log-level"
    elif verbose:
        effective_level = "DEBUG"
        source = "CLI --verbose"
    elif yaml_log_level:
        effective_level = yaml_log_level.upper()
        source = "YAML config"
    else:
        effective_level = "INFO"
        source = "default"
    
    # Validate level
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if effective_level not in valid_levels:
        logger.warning(
            f"Invalid log level '{effective_level}', using INFO. "
            f"Valid levels: {valid_levels}"
        )
        effective_level = "INFO"
    
    # Determine format
    format_type: Literal["text", "json"] = "text"
    if yaml_log_format and yaml_log_format.lower() == "json":
        format_type = "json"
    
    # Set up logging
    setup_logging_with_config(
        level=effective_level,
        format_type=format_type,
    )
    
    logger.info(f"Logging level set to {effective_level} (from {source})")
    
    return effective_level


def reset_logging() -> None:
    """Reset logging state for testing purposes."""
    global _logging_initialized
    _logging_initialized = False
    
    root = logging.getLogger()
    root.handlers.clear()


def is_logging_initialized() -> bool:
    """Check if logging has been initialized for current project."""
    return _logging_initialized
