"""
SemaBridge Plugins Module.

Provides extensibility for custom connectors, transformers, and validators.

Usage:
    from semabridge.plugins import SemaBridgePlugin, PluginRegistry, get_registry

    class MyPlugin(SemaBridgePlugin):
        ...
"""

from .base import (
    SemaBridgePlugin,
    PluginContext,
    PluginResult,
    PluginType,
)
from .registry import (
    PluginRegistry,
    PluginMetadata,
    get_registry,
)

__all__ = [
    "SemaBridgePlugin",
    "PluginContext",
    "PluginResult",
    "PluginType",
    "PluginRegistry",
    "PluginMetadata",
    "get_registry",
]
