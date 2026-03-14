"""
UV Build System - Command Registry.

Centralizes all project commands for discovery and consistent execution.
"""

from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Union

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class CommandCategory(str, Enum):
    CORE = "core"
    SEMANTIC = "semantic"
    INFRA = "infra"
    DIAGNOSTIC = "diagnostic"

@dataclass
class CommandContext:
    """Context for command execution."""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    initiated_by: str = "cli"
    options: Dict[str, Any] = field(default_factory=dict)

class SemaCommand(Protocol):
    """Protocol for a SemaBridge command."""
    name: str
    description: str
    category: CommandCategory
    
    def execute(self, ctx: CommandContext) -> Any:
        ...

class CommandRegistry:
    """
    Central registry for all SemaBridge commands.
    """
    
    def __init__(self):
        self._commands: Dict[str, SemaCommand] = {}
        self._categories: Dict[CommandCategory, List[str]] = {cat: [] for cat in CommandCategory}

    def register(self, command: SemaCommand):
        """Register a new command."""
        self._commands[command.name] = command
        self._categories[command.category].append(command.name)
        logger.debug(f"Registered command: {command.name}")

    def get_command(self, name: str) -> Optional[SemaCommand]:
        """Get a command by name."""
        return self._commands.get(name)

    def list_commands(self) -> List[SemaCommand]:
        """List all registered commands."""
        return list(self._commands.values())

    def get_categorical_help(self) -> Dict[str, List[Dict[str, str]]]:
        """Get help information grouped by category."""
        help_data = {}
        for cat, cmd_names in self._categories.items():
            help_data[cat.value] = [
                {"name": name, "description": self._commands[name].description}
                for name in cmd_names
            ]
        return help_data

# Global instance
registry = CommandRegistry()

def register_command(category: CommandCategory):
    """Decorator to register a command."""
    def decorator(cls):
        cmd_instance = cls()
        cmd_instance.category = category
        registry.register(cmd_instance)
        return cls
    return decorator
