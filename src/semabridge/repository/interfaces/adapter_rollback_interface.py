"""
Abstract interface for adapter rollback operations.

All adapters (Snowflake, Fabric) must implement this interface
to support universal rollback via the Semantic API.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RollbackResult:
    """Result of a rollback operation."""
    
    success: bool
    """Whether the rollback completed successfully."""
    
    new_version_id: Optional[str] = None
    """The new version ID created by the rollback (rollback is itself a version)."""
    
    applied_changes: List[Dict[str, Any]] = field(default_factory=list)
    """List of changes that were applied during rollback."""
    
    state_hash: Optional[str] = None
    """SHA256 hash of the semantic state after rollback."""
    
    error_message: Optional[str] = None
    """Error message if rollback failed."""
    
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    """Additional diagnostic information for debugging."""
    
    duration_ms: int = 0
    """Duration of the rollback operation in milliseconds."""


class AdapterRollbackInterface(ABC):
    """
    Standardized interface for rollback operations.
    
    Adapters implement this interface; Semantic API calls these methods.
    This ensures consistent rollback behavior across all data platforms.
    """
    
    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Return the adapter name (e.g., 'snowflake', 'fabric')."""
        pass
    
    @abstractmethod
    def supports_rollback(self) -> bool:
        """
        Declare if adapter supports rollback.
        
        Returns:
            True if rollback is supported, False otherwise.
        """
        pass
    
    @abstractmethod
    def get_rollback_artifacts(self, version_id: str) -> Dict[str, Any]:
        """
        Retrieve artifacts needed to perform rollback to version_id.
        
        Args:
            version_id: Target version to rollback to.
            
        Returns:
            Dictionary containing:
            - 'semantic_state': The SML JSON at the target version
            - 'ddl_statements': Any DDL needed (optional)
            - 'config': Adapter-specific configuration (optional)
            
        Raises:
            ValueError: If version_id not found.
        """
        pass
    
    @abstractmethod
    def validate_rollback_target(self, version_id: str) -> bool:
        """
        Validate that target version exists and rollback is feasible.
        
        Args:
            version_id: Target version to validate.
            
        Returns:
            True if rollback is possible.
            
        Raises:
            ValueError: If version doesn't exist.
            RuntimeError: If rollback is not feasible (e.g., schema conflicts).
        """
        pass
    
    @abstractmethod
    def execute_rollback(
        self,
        version_id: str,
        semantic_state_snapshot: Dict[str, Any],
        reason: Optional[str] = None,
    ) -> RollbackResult:
        """
        Execute the actual rollback operation.
        
        This method:
        1. Applies changes to reverse from current state to target version
        2. Validates data consistency after rollback
        3. Handles adapter-specific artifacts (tables, columns, permissions)
        
        Args:
            version_id: Target version to rollback to.
            semantic_state_snapshot: Pre-rollback semantic state (for comparison).
            reason: Optional reason for the rollback (for audit).
            
        Returns:
            RollbackResult with success status and details.
        """
        pass
    
    @abstractmethod
    def get_current_state(self) -> Dict[str, Any]:
        """
        Get the current semantic state from the adapter.
        
        Returns:
            Dictionary representing current SML state.
        """
        pass
    
    def get_rollback_status(self) -> Dict[str, Any]:
        """
        Return status of last rollback operation.
        
        Default implementation returns empty dict.
        Override to provide more detail.
        
        Returns:
            Dictionary with status information.
        """
        return {}
