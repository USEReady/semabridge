"""
SemaBridge Plugin Base Classes.

This module provides the abstract base classes for creating SemaBridge plugins.
All plugins must inherit from SemaBridgePlugin and implement the required methods.

Usage:
    from semabridge.plugins.base import SemaBridgePlugin, PluginContext

    class MyPlugin(SemaBridgePlugin):
        @property
        def name(self) -> str:
            return "my-plugin"
        
        @property
        def version(self) -> str:
            return "1.0.0"
        
        def initialize(self, context: PluginContext) -> None:
            # Setup logic
            pass
        
        def execute(self, data: Any) -> Any:
            # Transformation/processing logic
            return processed_data
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from enum import Enum


class PluginType(Enum):
    """Types of plugins supported by SemaBridge."""
    CONNECTOR = "connector"      # Data source integrations
    TRANSFORMER = "transformer"  # Data transformation
    VALIDATOR = "validator"      # Validation rules
    EMITTER = "emitter"          # Output generation
    HOOK = "hook"                # Lifecycle hooks


@dataclass
class PluginContext:
    """
    Context passed to plugins during initialization.
    
    Provides access to configuration, logging, and shared resources.
    """
    config: Dict[str, Any] = field(default_factory=dict)
    workspace_path: Optional[str] = None
    log_level: str = "INFO"
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key."""
        return self.config.get(key, default)


@dataclass
class PluginResult:
    """
    Result returned from plugin execution.
    
    Attributes:
        success: Whether the execution succeeded
        data: Output data from the plugin
        errors: List of error messages if any
        metadata: Additional metadata about the execution
    """
    success: bool
    data: Any = None
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SemaBridgePlugin(ABC):
    """
    Abstract base class for SemaBridge plugins.
    
    All plugins must inherit from this class and implement:
    - name: Unique identifier for the plugin
    - version: Semantic version string
    - plugin_type: Type of plugin (connector, transformer, etc.)
    - initialize: Setup logic called once when plugin is loaded
    - execute: Main execution logic
    
    Example:
        class MyConnectorPlugin(SemaBridgePlugin):
            @property
            def name(self) -> str:
                return "my-connector"
            
            @property
            def version(self) -> str:
                return "1.0.0"
            
            @property
            def plugin_type(self) -> PluginType:
                return PluginType.CONNECTOR
            
            def initialize(self, context: PluginContext) -> None:
                self.config = context.config
            
            def execute(self, data: Any) -> PluginResult:
                # Process data
                return PluginResult(success=True, data=result)
    """
    
    _context: Optional[PluginContext] = None
    _initialized: bool = False
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this plugin (lowercase, kebab-case)."""
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version string (e.g., '1.0.0')."""
        pass
    
    @property
    @abstractmethod
    def plugin_type(self) -> PluginType:
        """Type of plugin."""
        pass
    
    @property
    def description(self) -> str:
        """Human-readable description of the plugin."""
        return f"{self.name} v{self.version}"
    
    @property
    def is_initialized(self) -> bool:
        """Whether the plugin has been initialized."""
        return self._initialized
    
    @abstractmethod
    def initialize(self, context: PluginContext) -> None:
        """
        Initialize the plugin with the given context.
        
        Called once when the plugin is loaded. Use this to set up
        any resources, connections, or configuration needed.
        
        Args:
            context: Plugin context with configuration and metadata
        """
        pass
    
    @abstractmethod
    def execute(self, data: Any) -> PluginResult:
        """
        Execute the plugin's main logic.
        
        Args:
            data: Input data to process
            
        Returns:
            PluginResult with success status and output data
        """
        pass
    
    def cleanup(self) -> None:
        """
        Clean up resources when plugin is unloaded.
        
        Override this method to release connections, close files, etc.
        """
        pass
    
    def validate_input(self, data: Any) -> bool:
        """
        Validate input data before execution.
        
        Override to add custom validation logic.
        
        Args:
            data: Input data to validate
            
        Returns:
            True if valid, False otherwise
        """
        return True
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name}, version={self.version})>"
