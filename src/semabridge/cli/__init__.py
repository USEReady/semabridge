"""
CLI module.

This module provides the command-line interface for Semabridge.
All business logic is delegated to core/, connectors/, converter/, etc.
The CLI layer only orchestrates flow.
"""

from semabridge.cli.main import app, main
from semabridge.cli.semantic_commands import semantic_app
from semabridge.cli.diff_commands import diff_app
from semabridge.cli.logs_commands import logs_app

__all__ = ["app", "main", "semantic_app", "diff_app", "logs_app"]
