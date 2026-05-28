"""
Base classes for notification adapters.
All adapters must implement this interface.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, Optional
import time

from ..models import NotificationEvent


class AdapterError(Exception):
    """Base exception for adapter errors."""
    pass


class AdapterConfigError(AdapterError):
    """Configuration validation error."""
    pass


class AdapterSendError(AdapterError):
    """Error sending notification."""
    pass


class BaseAdapter(ABC):
    """
    Base class for all notification adapters.
    
    Adapters are responsible for sending formatted payloads to external services.
    All implementations must be async.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize adapter with configuration.
        
        Args:
            config: Channel configuration dict
        """
        self.config = config
    
    @abstractmethod
    async def send(
        self,
        payload: Dict[str, Any],
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send a pre-formatted payload to the channel.
        
        Args:
            payload: Channel-ready payload (from formatter)
            channel_config: Channel configuration
        
        Returns:
            {
                "success": bool,
                "response_code": int (optional),
                "response_body": str (optional),
                "duration_ms": int,
                "error": str (optional)
            }
        
        Raises:
            AdapterSendError on unrecoverable failures
        """
        pass
    
    @abstractmethod
    async def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate channel configuration before saving.
        
        Args:
            config: Configuration to validate
        
        Returns:
            (is_valid, error_message)
        """
        pass
    
    async def test_connection(self, config: Dict[str, Any] = None) -> tuple[bool, str]:
        """
        Test connection to the service (optional, used for test endpoint).
        
        Args:
            config: Configuration to test (uses self.config if None)
        
        Returns:
            (is_connected, error_message)
        """
        return True, ""
    
    def _measure_duration(self, start_time: float) -> int:
        """Calculate elapsed time in milliseconds."""
        return int((time.time() - start_time) * 1000)
    
    async def close(self) -> None:
        """Close any open resources (like aiohttp.ClientSession)."""
        pass

