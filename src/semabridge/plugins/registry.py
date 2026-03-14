"""
SemaBridge Plugin Registry.

Manages plugin discovery, registration, and lifecycle.
Supports the Model Context Protocol (MCP) for agent-friendly tool discovery.

Usage:
    from semabridge.plugins.registry import PluginRegistry
    
    registry = PluginRegistry()
    registry.register(MyPlugin())
    
    # Discover and execute
    plugin = registry.get("my-plugin")
    result = plugin.execute(data)
"""

import logging
from typing import Dict, List, Optional, Any, Type
from dataclasses import dataclass, field

from .base import SemaBridgePlugin, PluginContext, PluginType, PluginResult


logger = logging.getLogger(__name__)


@dataclass
class PluginMetadata:
    """Metadata about a registered plugin for MCP compatibility."""
    name: str
    version: str
    plugin_type: str
    description: str
    initialized: bool = False
    
    def to_mcp_tool(self) -> Dict[str, Any]:
        """
        Convert to Model Context Protocol tool format.
        
        Returns a dictionary compatible with MCP tool discovery.
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "type": self.plugin_type,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }


class PluginRegistry:
    """
    Central registry for SemaBridge plugins.
    
    Provides:
    - Plugin registration and deregistration
    - Plugin discovery by name or type
    - MCP-compatible tool listing
    - Lifecycle management (initialize, cleanup)
    
    Example:
        registry = PluginRegistry()
        
        # Register a plugin
        registry.register(MyConnectorPlugin())
        
        # Get by name
        plugin = registry.get("my-connector")
        
        # Get by type
        connectors = registry.get_by_type(PluginType.CONNECTOR)
        
        # List as MCP tools
        tools = registry.list_mcp_tools()
    """
    
    _instance: Optional["PluginRegistry"] = None
    
    def __new__(cls) -> "PluginRegistry":
        """Singleton pattern for global registry access."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._plugins = {}
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, "_plugins"):
            self._plugins: Dict[str, SemaBridgePlugin] = {}
            self._initialized = False
    
    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        cls._instance = None
    
    def register(self, plugin: SemaBridgePlugin) -> bool:
        """
        Register a plugin with the registry.
        
        Args:
            plugin: Plugin instance to register
            
        Returns:
            True if registered successfully, False if name conflict
        """
        if plugin.name in self._plugins:
            logger.warning(f"Plugin '{plugin.name}' already registered, skipping")
            return False
        
        self._plugins[plugin.name] = plugin
        logger.info(f"Registered plugin: {plugin.name} v{plugin.version}")
        return True
    
    def deregister(self, name: str) -> bool:
        """
        Remove a plugin from the registry.
        
        Calls cleanup on the plugin before removal.
        
        Args:
            name: Plugin name to remove
            
        Returns:
            True if removed, False if not found
        """
        if name not in self._plugins:
            return False
        
        plugin = self._plugins[name]
        try:
            plugin.cleanup()
        except Exception as e:
            logger.error(f"Error during cleanup of plugin '{name}': {e}")
        
        del self._plugins[name]
        logger.info(f"Deregistered plugin: {name}")
        return True
    
    def get(self, name: str) -> Optional[SemaBridgePlugin]:
        """
        Get a plugin by name.
        
        Args:
            name: Plugin name
            
        Returns:
            Plugin instance or None if not found
        """
        return self._plugins.get(name)
    
    def get_by_type(self, plugin_type: PluginType) -> List[SemaBridgePlugin]:
        """
        Get all plugins of a specific type.
        
        Args:
            plugin_type: Type of plugins to retrieve
            
        Returns:
            List of matching plugins
        """
        return [
            p for p in self._plugins.values() 
            if p.plugin_type == plugin_type
        ]
    
    def list_all(self) -> List[PluginMetadata]:
        """
        List metadata for all registered plugins.
        
        Returns:
            List of PluginMetadata objects
        """
        return [
            PluginMetadata(
                name=p.name,
                version=p.version,
                plugin_type=p.plugin_type.value,
                description=p.description,
                initialized=p.is_initialized
            )
            for p in self._plugins.values()
        ]
    
    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        """
        List all plugins as MCP-compatible tools.
        
        Returns:
            List of tool definitions compatible with Model Context Protocol
        """
        return [meta.to_mcp_tool() for meta in self.list_all()]
    
    def initialize_all(self, context: Optional[PluginContext] = None) -> Dict[str, bool]:
        """
        Initialize all registered plugins.
        
        Args:
            context: Plugin context to pass to each plugin
            
        Returns:
            Dictionary mapping plugin names to initialization success
        """
        context = context or PluginContext()
        results = {}
        
        for name, plugin in self._plugins.items():
            try:
                plugin.initialize(context)
                plugin._initialized = True
                results[name] = True
                logger.debug(f"Initialized plugin: {name}")
            except Exception as e:
                results[name] = False
                logger.error(f"Failed to initialize plugin '{name}': {e}")
        
        self._initialized = True
        return results
    
    def cleanup_all(self) -> None:
        """Clean up all registered plugins."""
        for name, plugin in self._plugins.items():
            try:
                plugin.cleanup()
                plugin._initialized = False
            except Exception as e:
                logger.error(f"Error during cleanup of plugin '{name}': {e}")
        
        self._initialized = False
    
    @property
    def count(self) -> int:
        """Number of registered plugins."""
        return len(self._plugins)
    
    @property
    def names(self) -> List[str]:
        """Names of all registered plugins."""
        return list(self._plugins.keys())
    
    def __contains__(self, name: str) -> bool:
        return name in self._plugins
    
    def __len__(self) -> int:
        return self.count
    
    def __repr__(self) -> str:
        return f"<PluginRegistry(plugins={self.count})>"


# Global registry instance
_global_registry: Optional[PluginRegistry] = None


def get_registry() -> PluginRegistry:
    """Get the global plugin registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = PluginRegistry()
    return _global_registry
