"""
Rollback Orchestrator.

Coordinates rollback operations across adapters without modifying existing adapter code.
Creates pre/post rollback snapshots and tracks rollback lineage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from semabridge.repository.interfaces.adapter_rollback_interface import (
    AdapterRollbackInterface,
    RollbackResult,
)
from semabridge.repository.semantic_version_manager import SemanticVersionManager
from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager
from semabridge.repository.semantic_diff_engine import SemanticDiffEngine
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RollbackOperation:
    """Metadata about a rollback operation."""
    operation_id: str
    adapter: str
    from_version_id: str
    to_version_id: str
    status: str = "pending"  # pending, in_progress, completed, failed
    initiated_at: str = ""
    completed_at: Optional[str] = None
    initiated_by: str = "system"
    reason: str = ""
    pre_rollback_snapshot_id: Optional[str] = None
    post_rollback_snapshot_id: Optional[str] = None
    result: Optional[RollbackResult] = None
    error_message: Optional[str] = None
    
    def __post_init__(self):
        if not self.initiated_at:
            self.initiated_at = datetime.now(timezone.utc).isoformat()


class RollbackOrchestrator:
    """
    Coordinate rollback operations across adapters.
    
    Responsibilities:
    - Accept rollback request (target_version, adapter_name)
    - Retrieve rollback metadata from version manager
    - Create pre-rollback snapshot (current semantic state)
    - Delegate execution to adapter-specific rollback handler
    - Track rollback operation as new version
    - Provide rollback status and result
    
    Key Principle: Rollback is itself a new version, preserving history.
    """
    
    def __init__(
        self,
        version_manager: Optional[SemanticVersionManager] = None,
        snapshot_manager: Optional[SemanticSnapshotManager] = None,
        diff_engine: Optional[SemanticDiffEngine] = None,
    ):
        """
        Initialize the orchestrator.
        
        Args:
            version_manager: Version manager instance.
            snapshot_manager: Snapshot manager instance.
            diff_engine: Diff engine instance.
        """
        self.version_manager = version_manager or SemanticVersionManager()
        self.snapshot_manager = snapshot_manager or SemanticSnapshotManager()
        self.diff_engine = diff_engine or SemanticDiffEngine(self.snapshot_manager)
        
        # Registered adapters
        self._adapters: Dict[str, AdapterRollbackInterface] = {}
        
        # Operation tracking
        self._operations: Dict[str, RollbackOperation] = {}
        self._operation_counter = 0
        
        logger.debug("RollbackOrchestrator initialized")
    
    def register_adapter(self, adapter: AdapterRollbackInterface) -> None:
        """
        Register an adapter for rollback operations.
        
        Args:
            adapter: Adapter implementing AdapterRollbackInterface.
        """
        name = adapter.adapter_name
        self._adapters[name] = adapter
        logger.info(f"Registered adapter: {name} (rollback supported: {adapter.supports_rollback()})")
    
    def get_adapter(self, adapter_name: str) -> Optional[AdapterRollbackInterface]:
        """Get a registered adapter by name."""
        return self._adapters.get(adapter_name)
    
    def _generate_operation_id(self) -> str:
        """Generate unique operation ID."""
        self._operation_counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"op_{timestamp}_{self._operation_counter:04d}"
    
    def validate_rollback_feasibility(
        self,
        adapter_name: str,
        target_version_id: str,
    ) -> Dict[str, Any]:
        """
        Check if rollback is possible.
        
        Args:
            adapter_name: Adapter to rollback.
            target_version_id: Version to rollback to.
            
        Returns:
            Dictionary with:
            - feasible: bool
            - reason: str (if not feasible)
            - target_version: VersionRecord
            - current_version: VersionRecord
            - changes_preview: DiffSummary
        """
        result = {
            "feasible": False,
            "reason": "",
            "target_version": None,
            "current_version": None,
            "changes_preview": None,
        }
        
        # Check adapter is registered
        adapter = self.get_adapter(adapter_name)
        if adapter is None:
            result["reason"] = f"Adapter '{adapter_name}' not registered"
            return result
        
        # Check adapter supports rollback
        if not adapter.supports_rollback():
            result["reason"] = f"Adapter '{adapter_name}' does not support rollback"
            return result
        
        # Check target version exists
        target_version = self.version_manager.get_version(target_version_id)
        if target_version is None:
            result["reason"] = f"Target version '{target_version_id}' not found"
            return result
        
        result["target_version"] = target_version
        
        # Check current version
        current_version = self.version_manager.get_current_version(adapter_name)
        result["current_version"] = current_version
        
        # Check if already at target
        if current_version and current_version.version_id == target_version_id:
            result["reason"] = "Already at target version"
            result["feasible"] = True  # Still feasible, just no-op
            return result
        
        # Validate with adapter
        try:
            adapter.validate_rollback_target(target_version_id)
        except ValueError as e:
            result["reason"] = f"Adapter validation failed: {e}"
            return result
        except RuntimeError as e:
            result["reason"] = f"Rollback not feasible: {e}"
            return result
        
        # Get changes preview if snapshots exist
        try:
            latest_snapshot = self.snapshot_manager.get_latest_snapshot(adapter_name)
            if latest_snapshot:
                # Get target snapshot (need to find by version)
                target_snapshots = self.snapshot_manager.list_snapshots(adapter_name, limit=100)
                target_snapshot = None
                for s in target_snapshots:
                    if target_version_id in s.snapshot_id or s.snapshot_id == target_version_id:
                        target_snapshot = s
                        break
                
                if target_snapshot:
                    diff = self.diff_engine.compare_snapshots(
                        latest_snapshot.snapshot_id,
                        target_snapshot.snapshot_id,
                    )
                    result["changes_preview"] = diff.summary
        except Exception as e:
            logger.warning(f"Could not generate changes preview: {e}")
        
        result["feasible"] = True
        logger.debug(f"Rollback validation passed for {adapter_name} -> {target_version_id}")
        return result
    
    def initiate_rollback(
        self,
        adapter_name: str,
        target_version_id: str,
        reason: str = "",
        initiated_by: str = "system",
        dry_run: bool = False,
    ) -> RollbackOperation:
        """
        Start a rollback operation.
        
        Args:
            adapter_name: Adapter to rollback.
            target_version_id: Version to rollback to.
            reason: Reason for rollback (for audit).
            initiated_by: User/system initiating rollback.
            dry_run: If True, only validate without executing.
            
        Returns:
            RollbackOperation with result.
            
        Raises:
            ValueError: If rollback is not feasible.
        """
        operation_id = self._generate_operation_id()
        
        # Get current version
        current_version = self.version_manager.get_current_version(adapter_name)
        from_version_id = current_version.version_id if current_version else "none"
        
        # Create operation record
        operation = RollbackOperation(
            operation_id=operation_id,
            adapter=adapter_name,
            from_version_id=from_version_id,
            to_version_id=target_version_id,
            reason=reason,
            initiated_by=initiated_by,
        )
        self._operations[operation_id] = operation
        
        logger.info(
            f"[{operation_id}] Initiating rollback: {adapter_name} "
            f"{from_version_id} -> {target_version_id}"
        )
        
        # Validate feasibility
        validation = self.validate_rollback_feasibility(adapter_name, target_version_id)
        if not validation["feasible"]:
            operation.status = "failed"
            operation.error_message = validation["reason"]
            logger.error(f"[{operation_id}] Rollback validation failed: {validation['reason']}")
            return operation
        
        if dry_run:
            operation.status = "validated"
            logger.info(f"[{operation_id}] Dry run completed - rollback is feasible")
            return operation
        
        # Get adapter
        adapter = self.get_adapter(adapter_name)
        if adapter is None:
            operation.status = "failed"
            operation.error_message = f"Adapter not found: {adapter_name}"
            return operation
        
        operation.status = "in_progress"
        start_time = time.time()
        
        try:
            # Step 1: Retrieve sync_mode from target snapshot in database
            model_repo = ModelRepository()
            target_snapshot = model_repo.get_snapshot(target_version_id)
            sync_mode = target_snapshot.sync_mode if target_snapshot and hasattr(target_snapshot, 'sync_mode') else 'copy'
            logger.info(f"[{operation_id}] Rolling back to version {target_version_id} (sync_mode={sync_mode})")
            
            # Step 2: Create pre-rollback snapshot
            logger.info(f"[{operation_id}] Creating pre-rollback snapshot...")
            current_state = adapter.get_current_state()
            
            pre_snapshot = self.snapshot_manager.create_snapshot(
                adapter=adapter_name,
                semantic_state=current_state,
                change_description=f"Pre-rollback snapshot before reverting to {target_version_id}",
                sync_mode=sync_mode,
            )
            operation.pre_rollback_snapshot_id = pre_snapshot.snapshot_id
            logger.debug(f"[{operation_id}] Pre-rollback snapshot: {pre_snapshot.snapshot_id} (sync_mode={sync_mode})")
            
            # Step 3: Get rollback artifacts
            logger.info(f"[{operation_id}] Retrieving rollback artifacts...")
            artifacts = adapter.get_rollback_artifacts(target_version_id)
            
            # Step 4: Execute rollback
            logger.info(f"[{operation_id}] Executing rollback...")
            result = adapter.execute_rollback(
                version_id=target_version_id,
                semantic_state_snapshot=current_state,
                reason=reason,
            )
            operation.result = result
            
            if result.success:
                # Step 5: Create post-rollback snapshot (inherits sync_mode from rollback context)
                logger.info(f"[{operation_id}] Creating post-rollback snapshot...")
                new_state = adapter.get_current_state()
                
                post_snapshot = self.snapshot_manager.create_snapshot(
                    adapter=adapter_name,
                    semantic_state=new_state,
                    change_description=f"Rollback completed to {target_version_id}",
                    parent_snapshot_id=pre_snapshot.snapshot_id,
                    sync_mode=sync_mode,
                )
                operation.post_rollback_snapshot_id = post_snapshot.snapshot_id
                
                # Step 5: Create version record for rollback
                version_record = self.version_manager.create_version_record(
                    adapter=adapter_name,
                    semantic_state=new_state,
                    change_type="rollback",
                    change_summary=f"Rollback from {from_version_id} to {target_version_id}",
                    rollback_metadata={
                        "from_version": from_version_id,
                        "to_version": target_version_id,
                        "reason": reason,
                        "operation_id": operation_id,
                    },
                )
                
                # Link rollback operation
                self.version_manager.link_rollback_operation(
                    from_version_id=from_version_id,
                    to_version_id=target_version_id,
                    rollback_version_id=version_record.version_id,
                    reason=reason,
                )
                
                operation.status = "completed"
                operation.completed_at = datetime.now(timezone.utc).isoformat()
                
                logger.info(
                    f"[{operation_id}] Rollback completed successfully. "
                    f"New version: {version_record.version_id}"
                )
            else:
                operation.status = "failed"
                operation.error_message = result.error_message
                logger.error(
                    f"[{operation_id}] Rollback failed: {result.error_message}"
                )
        
        except Exception as e:
            operation.status = "failed"
            operation.error_message = str(e)
            logger.exception(f"[{operation_id}] Rollback exception: {e}")
        
        # Record duration
        if operation.result:
            operation.result.duration_ms = int((time.time() - start_time) * 1000)
        
        return operation
    
    def get_rollback_status(self, operation_id: str) -> Optional[RollbackOperation]:
        """
        Get status of a rollback operation.
        
        Args:
            operation_id: The operation ID to query.
            
        Returns:
            RollbackOperation or None if not found.
        """
        return self._operations.get(operation_id)
    
    def list_rollback_operations(
        self,
        adapter_name: Optional[str] = None,
        limit: int = 10,
    ) -> List[RollbackOperation]:
        """
        List rollback operations.
        
        Args:
            adapter_name: Filter by adapter (optional).
            limit: Maximum number to return.
            
        Returns:
            List of RollbackOperations, newest first.
        """
        operations = list(self._operations.values())
        
        if adapter_name:
            operations = [op for op in operations if op.adapter == adapter_name]
        
        # Sort by initiated_at descending
        operations.sort(key=lambda x: x.initiated_at, reverse=True)
        
        return operations[:limit]
    
    def preview_rollback(
        self,
        adapter_name: str,
        target_version_id: str,
    ) -> Dict[str, Any]:
        """
        Preview what rollback will change.
        
        Args:
            adapter_name: Adapter to rollback.
            target_version_id: Version to rollback to.
            
        Returns:
            Dictionary with:
            - feasible: bool
            - from_version: str
            - to_version: str
            - changes: list of changes
            - breaking_changes: list of breaking changes
            - summary: DiffSummary
        """
        validation = self.validate_rollback_feasibility(adapter_name, target_version_id)
        
        result = {
            "feasible": validation["feasible"],
            "from_version": validation.get("current_version", {}).version_id if validation.get("current_version") else None,
            "to_version": target_version_id,
            "changes": [],
            "breaking_changes": [],
            "summary": None,
            "reason": validation.get("reason", ""),
        }
        
        if not validation["feasible"]:
            return result
        
        # Get full diff if possible
        try:
            adapter = self.get_adapter(adapter_name)
            if adapter:
                current_state = adapter.get_current_state()
                
                # Get target state from artifacts
                artifacts = adapter.get_rollback_artifacts(target_version_id)
                target_state = artifacts.get("semantic_state", {})
                
                if current_state and target_state:
                    diff = self.diff_engine.compare_states(
                        current_state,
                        target_state,
                        from_snapshot_id="current",
                        to_snapshot_id=target_version_id,
                    )
                    
                    result["changes"] = [
                        {
                            "entity_type": c.entity_type.value,
                            "entity_name": c.entity_name,
                            "change_type": c.change_type.value,
                            "change_reason": c.change_reason,
                        }
                        for c in diff.changes
                    ]
                    result["breaking_changes"] = [
                        {
                            "entity_type": c.entity_type.value,
                            "entity_name": c.entity_name,
                            "recommendation": self.diff_engine._get_breaking_change_recommendation(c),
                        }
                        for c in diff.breaking_changes
                    ]
                    result["summary"] = {
                        "total_changes": diff.summary.total_changes,
                        "measures_changed": diff.summary.measures_added + diff.summary.measures_modified + diff.summary.measures_removed,
                        "dimensions_changed": diff.summary.dimensions_added + diff.summary.dimensions_modified + diff.summary.dimensions_removed,
                        "has_breaking_changes": diff.summary.has_breaking_changes,
                    }
        except Exception as e:
            logger.warning(f"Could not generate rollback preview: {e}")
        
        return result
    
    def get_rollback_history(
        self,
        adapter_name: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get rollback history from version registry.
        
        Args:
            adapter_name: Adapter to get history for.
            limit: Maximum number to return.
            
        Returns:
            List of rollback version records.
        """
        rollback_versions = self.version_manager.get_rollback_history(adapter_name, limit)
        
        return [
            {
                "version_id": v.version_id,
                "timestamp": v.timestamp,
                "change_summary": v.change_summary,
                "from_version": v.rollback_metadata.get("from_version") if v.rollback_metadata else None,
                "to_version": v.rollback_metadata.get("to_version") if v.rollback_metadata else None,
                "reason": v.rollback_metadata.get("reason") if v.rollback_metadata else None,
            }
            for v in rollback_versions
        ]
