# Core module for CLI Execution Engine
"""
Core execution engine components.

This module provides the central orchestration for CLI execution,
following the mandatory 10-step flow. Also exports exceptions and interfaces.
"""

from semabridge.core.executor import CLIExecutor, ExecutionError
from semabridge.core.execution_config import ExecutionConfig, SourceConfig, TargetConfig, LoggingConfig
from semabridge.core.source_format import SourceFormat, from_snowflake_metadata, from_fabric_tmsl
from semabridge.core.run_summary import (
    RunSummary, RunStatus, StepStatus, STEP_NAMES, create_run_summary
)
from semabridge.core.exceptions import (
    SemaBridgeError,
    ConnectorError,
    MissingCredentialError,
    ConversionError,
    ValidationError,
    RepositoryError,
    PluginError,
)
from semabridge.core.interfaces import (
    BaseConnector,
    BaseExtractor,
    BaseEmitter,
    BaseConverter,
)

__all__ = [
    # Executor
    "CLIExecutor",
    "ExecutionError",
    # Configuration
    "ExecutionConfig",
    "SourceConfig",
    "TargetConfig",
    "LoggingConfig",
    # Source Format
    "SourceFormat",
    "from_snowflake_metadata",
    "from_fabric_tmsl",
    # Run Summary
    "RunSummary",
    "RunStatus",
    "StepStatus",
    "STEP_NAMES",
    "create_run_summary",
    # Exceptions
    "SemaBridgeError",
    "ConnectorError",
    "MissingCredentialError",
    "ConversionError",
    "ValidationError",
    "RepositoryError",
    "PluginError",
    # Interfaces (ABCs)
    "BaseConnector",
    "BaseExtractor",
    "BaseEmitter",
    "BaseConverter",
]
