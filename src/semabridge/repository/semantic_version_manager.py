"""
Semantic Version Manager.

Centralized version tracking for semantic metadata across all adapters.
Maintains version registry with parent-child relationships and rollback lineage.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class VersionRecord(BaseModel):
    """A version record in the registry."""
    
    version_id: str = Field(..., description="Unique version identifier")
    adapter: str = Field(..., description="Adapter name (snowflake, fabric)")
    timestamp: str = Field(..., description="ISO8601 timestamp of version creation")
    parent_version_id: Optional[str] = Field(None, description="Parent version ID")
    change_type: str = Field("deploy", description="Type: deploy, rollback, snapshot")
    change_summary: str = Field("", description="Human-readable summary of changes")
    schema_hash: str = Field(..., description="SHA256 hash of semantic state")
    rollback_metadata: Optional[Dict[str, Any]] = Field(None, description="Rollback info if applicable")
    status: str = Field("active", description="Status: active, superseded, rolled_back")
    

class SemanticVersionManager:
    """
    Centralized version tracking for semantic metadata.
    
    Responsibilities:
    - Maintain version registry (version ID, timestamp, adapter, status, change_summary)
    - Track parent-child version relationships
    - Store version snapshots as immutable records
    - Query version history and state transitions
    - Assign unique version identifiers
    
    Version ID Format: v{YYYYMMDD_HHMMSS}_{adapter}_{sequence}
    Example: v20260119_103700_snowflake_001
    """
    
    def __init__(self, metadata_dir: Optional[str] = None):
        """
        Initialize the version manager.
        
        Args:
            metadata_dir: Directory for .semantic_metadata. 
                         Defaults to project root/.semantic_metadata
        """
        if metadata_dir is None:
            # Default to project root
            project_root = Path(__file__).parent.parent.parent
            metadata_dir = str(project_root / ".semantic_metadata")
        
        self.metadata_dir = Path(metadata_dir)
        self.registry_path = self.metadata_dir / "version_registry.json"
        
        # Ensure directory exists
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        
        # Load or create registry
        self._registry: Dict[str, List[VersionRecord]] = self._load_registry()
        
        # Sequence counters per adapter (for same-second versions)
        self._sequence_counters: Dict[str, int] = {}
        
        logger.debug(f"SemanticVersionManager initialized at {self.metadata_dir}")
    
    def _load_registry(self) -> Dict[str, List[VersionRecord]]:
        """Load version registry from disk."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Convert to VersionRecord objects
                    registry = {}
                    for adapter, versions in data.items():
                        registry[adapter] = [
                            VersionRecord(**v) for v in versions
                        ]
                    return registry
            except Exception as e:
                logger.warning(f"Failed to load version registry: {e}")
                return {}
        return {}
    
    def _save_registry(self) -> None:
        """Save version registry to disk."""
        try:
            data = {}
            for adapter, versions in self._registry.items():
                data[adapter] = [v.model_dump() for v in versions]
            
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            
            logger.debug(f"Version registry saved to {self.registry_path}")
        except Exception as e:
            logger.error(f"Failed to save version registry: {e}")
            raise
    
    def _generate_version_id(self, adapter: str) -> str:
        """
        Generate a unique version ID.
        
        Format: v{YYYYMMDD_HHMMSS}_{adapter}_{sequence}
        """
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        
        # Get sequence for this adapter/timestamp
        key = f"{adapter}_{timestamp_str}"
        if key not in self._sequence_counters:
            self._sequence_counters[key] = 0
        self._sequence_counters[key] += 1
        sequence = self._sequence_counters[key]
        
        return f"v{timestamp_str}_{adapter}_{sequence:03d}"
    
    @staticmethod
    def compute_schema_hash(semantic_state: Dict[str, Any]) -> str:
        """
        Compute SHA256 hash of semantic state.
        
        Args:
            semantic_state: SML JSON representation.
            
        Returns:
            Hex-encoded SHA256 hash.
        """
        # Sort keys for consistent hashing
        json_str = json.dumps(semantic_state, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()
    
    def get_current_version(self, adapter: str) -> Optional[VersionRecord]:
        """
        Get the current (latest active) version for an adapter.
        
        Args:
            adapter: Adapter name (snowflake, fabric).
            
        Returns:
            Latest active VersionRecord or None if no versions exist.
        """
        if adapter not in self._registry:
            return None
        
        versions = self._registry[adapter]
        if not versions:
            return None
        
        # Find latest active version
        active_versions = [v for v in versions if v.status == "active"]
        if not active_versions:
            # Return most recent if no active
            return versions[-1]
        
        return active_versions[-1]
    
    def get_version_history(
        self,
        adapter: str,
        limit: int = 10,
        include_superseded: bool = False,
    ) -> List[VersionRecord]:
        """
        Get version history for an adapter.
        
        Args:
            adapter: Adapter name.
            limit: Maximum number of versions to return.
            include_superseded: Whether to include superseded versions.
            
        Returns:
            List of VersionRecords, newest first.
        """
        if adapter not in self._registry:
            return []
        
        versions = self._registry[adapter]
        
        if not include_superseded:
            versions = [v for v in versions if v.status != "superseded"]
        
        # Return newest first, limited
        return list(reversed(versions[-limit:]))
    
    def get_version(self, version_id: str) -> Optional[VersionRecord]:
        """
        Get a specific version by ID.
        
        Args:
            version_id: The version ID to look up.
            
        Returns:
            VersionRecord or None if not found.
        """
        for adapter, versions in self._registry.items():
            for version in versions:
                if version.version_id == version_id:
                    return version
        return None
    
    def create_version_record(
        self,
        adapter: str,
        semantic_state: Dict[str, Any],
        change_type: str = "deploy",
        change_summary: str = "",
        parent_version_id: Optional[str] = None,
        rollback_metadata: Optional[Dict[str, Any]] = None,
    ) -> VersionRecord:
        """
        Create a new version record.
        
        Args:
            adapter: Adapter name.
            semantic_state: SML JSON at this version.
            change_type: Type of change (deploy, rollback, snapshot).
            change_summary: Human-readable summary.
            parent_version_id: Parent version ID (optional, auto-detected if None).
            rollback_metadata: Rollback info if this is a rollback version.
            
        Returns:
            The created VersionRecord.
        """
        # Generate version ID
        version_id = self._generate_version_id(adapter)
        
        # Compute schema hash
        schema_hash = self.compute_schema_hash(semantic_state)
        
        # Auto-detect parent if not provided
        if parent_version_id is None:
            current = self.get_current_version(adapter)
            if current:
                parent_version_id = current.version_id
        
        # Create record
        record = VersionRecord(
            version_id=version_id,
            adapter=adapter,
            timestamp=datetime.now(timezone.utc).isoformat(),
            parent_version_id=parent_version_id,
            change_type=change_type,
            change_summary=change_summary,
            schema_hash=schema_hash,
            rollback_metadata=rollback_metadata,
            status="active",
        )
        
        # Mark previous version as superseded if it exists
        if parent_version_id:
            self._mark_superseded(parent_version_id)
        
        # Add to registry
        if adapter not in self._registry:
            self._registry[adapter] = []
        self._registry[adapter].append(record)
        
        # Persist
        self._save_registry()
        
        logger.info(f"Created version {version_id} for {adapter}: {change_summary}")
        return record
    
    def _mark_superseded(self, version_id: str) -> None:
        """Mark a version as superseded."""
        for adapter, versions in self._registry.items():
            for version in versions:
                if version.version_id == version_id:
                    version.status = "superseded"
                    return
    
    def mark_version_as_target_for_rollback(self, version_id: str) -> bool:
        """
        Mark a version as the target for an upcoming rollback.
        
        This is used in rollback preview/validation.
        
        Args:
            version_id: The version ID to mark.
            
        Returns:
            True if version was found and marked.
        """
        version = self.get_version(version_id)
        if version is None:
            return False
        
        # The actual marking happens during rollback execution
        logger.debug(f"Version {version_id} validated as rollback target")
        return True
    
    def link_rollback_operation(
        self,
        from_version_id: str,
        to_version_id: str,
        rollback_version_id: str,
        reason: str = "",
    ) -> None:
        """
        Track rollback lineage.
        
        Creates a link showing that rollback_version_id was created
        by rolling back from from_version_id to to_version_id.
        
        Args:
            from_version_id: Source version before rollback.
            to_version_id: Target version being restored.
            rollback_version_id: The new version created by rollback.
            reason: Optional reason for rollback.
        """
        version = self.get_version(rollback_version_id)
        if version:
            version.rollback_metadata = {
                "from_version": from_version_id,
                "to_version": to_version_id,
                "reason": reason,
                "linked_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_registry()
            logger.info(
                f"Linked rollback: {from_version_id} -> {to_version_id} "
                f"(new: {rollback_version_id})"
            )
    
    def get_rollback_history(
        self,
        adapter: str,
        limit: int = 10,
    ) -> List[VersionRecord]:
        """
        Get only rollback versions for an adapter.
        
        Args:
            adapter: Adapter name.
            limit: Maximum number to return.
            
        Returns:
            List of rollback VersionRecords, newest first.
        """
        if adapter not in self._registry:
            return []
        
        rollbacks = [
            v for v in self._registry[adapter]
            if v.change_type == "rollback"
        ]
        
        return list(reversed(rollbacks[-limit:]))
    
    def version_exists(self, version_id: str) -> bool:
        """Check if a version exists."""
        return self.get_version(version_id) is not None
    
    def get_versions_between(
        self,
        adapter: str,
        from_version_id: str,
        to_version_id: str,
    ) -> List[VersionRecord]:
        """
        Get all versions between two version IDs (inclusive).
        
        Useful for showing what changed between two points.
        
        Args:
            adapter: Adapter name.
            from_version_id: Starting version.
            to_version_id: Ending version.
            
        Returns:
            List of VersionRecords in chronological order.
        """
        if adapter not in self._registry:
            return []
        
        versions = self._registry[adapter]
        
        # Find indices
        from_idx = None
        to_idx = None
        
        for i, v in enumerate(versions):
            if v.version_id == from_version_id:
                from_idx = i
            if v.version_id == to_version_id:
                to_idx = i
        
        if from_idx is None or to_idx is None:
            return []
        
        # Ensure from < to
        if from_idx > to_idx:
            from_idx, to_idx = to_idx, from_idx
        
        return versions[from_idx:to_idx + 1]
