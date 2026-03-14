"""
Semantic Snapshot Manager.

Manages immutable snapshots of semantic metadata state.
Snapshots are stored as JSON files in .semantic_metadata/snapshots/{adapter}/
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


class SemanticSnapshot(BaseModel):
    """An immutable snapshot of semantic state."""
    
    snapshot_id: str = Field(..., description="Unique snapshot identifier")
    adapter: str = Field(..., description="Adapter name (snowflake, fabric)")
    timestamp: str = Field(..., description="ISO8601 timestamp of creation")
    semantic_entities: Dict[str, Any] = Field(
        ..., 
        description="Full semantic state (measures, dimensions, hierarchies, relationships)"
    )
    schema_hash: str = Field(..., description="SHA256 hash of semantic state")
    parent_snapshot_id: Optional[str] = Field(None, description="Parent snapshot ID")
    change_description: str = Field("", description="Description of changes")
    
    # Counts for quick lookup
    measure_count: int = Field(0, description="Number of measures")
    dimension_count: int = Field(0, description="Number of dimensions")
    hierarchy_count: int = Field(0, description="Number of hierarchies")
    relationship_count: int = Field(0, description="Number of relationships")


class SemanticSnapshotManager:
    """
    Manage immutable snapshots of semantic metadata state.
    
    Responsibilities:
    - Create snapshots *before* any modification (pre-change snapshots)
    - Store snapshots in project structure with versioning
    - Associate snapshots with adapter-specific state
    - Enable retrieval of historical semantic states
    - Support point-in-time queries
    
    Storage: .semantic_metadata/snapshots/{adapter}/{snapshot_id}.json
    Index: .semantic_metadata/snapshot_index.json
    """
    
    def __init__(self, metadata_dir: Optional[str] = None):
        """
        Initialize the snapshot manager.
        
        Args:
            metadata_dir: Directory for .semantic_metadata.
                         Defaults to project root/.semantic_metadata
        """
        if metadata_dir is None:
            project_root = Path(__file__).parent.parent.parent
            metadata_dir = str(project_root / ".semantic_metadata")
        
        self.metadata_dir = Path(metadata_dir)
        self.snapshots_dir = self.metadata_dir / "snapshots"
        self.index_path = self.metadata_dir / "snapshot_index.json"
        
        # Ensure directories exist
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        
        # Load index
        self._index: Dict[str, List[str]] = self._load_index()
        
        # Sequence counters
        self._sequence_counters: Dict[str, int] = {}
        
        logger.debug(f"SemanticSnapshotManager initialized at {self.snapshots_dir}")
    
    def _load_index(self) -> Dict[str, List[str]]:
        """Load snapshot index from disk."""
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load snapshot index: {e}")
                return {}
        return {}
    
    def _save_index(self) -> None:
        """Save snapshot index to disk."""
        try:
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(self._index, f, indent=2)
            logger.debug(f"Snapshot index saved to {self.index_path}")
        except Exception as e:
            logger.error(f"Failed to save snapshot index: {e}")
            raise
    
    def _get_adapter_dir(self, adapter: str) -> Path:
        """Get snapshot directory for an adapter."""
        adapter_dir = self.snapshots_dir / adapter
        adapter_dir.mkdir(parents=True, exist_ok=True)
        return adapter_dir
    
    def _generate_snapshot_id(self, adapter: str) -> str:
        """
        Generate a unique snapshot ID.
        
        Format: v{YYYYMMDD_HHMMSS}_{adapter}_{sequence}
        """
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        
        key = f"{adapter}_{timestamp_str}"
        if key not in self._sequence_counters:
            self._sequence_counters[key] = 0
        self._sequence_counters[key] += 1
        sequence = self._sequence_counters[key]
        
        return f"v{timestamp_str}_{adapter}_{sequence:03d}"
    
    @staticmethod
    def compute_schema_hash(semantic_state: Dict[str, Any]) -> str:
        """Compute SHA256 hash of semantic state."""
        json_str = json.dumps(semantic_state, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()
    
    def _count_entities(self, semantic_state: Dict[str, Any]) -> Dict[str, int]:
        """Count entities in semantic state."""
        return {
            "measure_count": len(semantic_state.get("metrics", [])),
            "dimension_count": len(semantic_state.get("dimensions", [])),
            "hierarchy_count": sum(
                len(d.get("hierarchies", [])) 
                for d in semantic_state.get("dimensions", [])
            ),
            "relationship_count": len(semantic_state.get("relationships", [])),
        }
    
    def create_snapshot(
        self,
        adapter: str,
        semantic_state: Dict[str, Any],
        change_description: str = "",
        parent_snapshot_id: Optional[str] = None,
    ) -> SemanticSnapshot:
        """
        Create an immutable snapshot.
        
        Args:
            adapter: Adapter name (snowflake, fabric).
            semantic_state: Full SML JSON state.
            change_description: Description of what changed.
            parent_snapshot_id: Parent snapshot (auto-detected if None).
            
        Returns:
            The created SemanticSnapshot.
        """
        snapshot_id = self._generate_snapshot_id(adapter)
        
        # Compute hash
        schema_hash = self.compute_schema_hash(semantic_state)
        
        # Auto-detect parent
        if parent_snapshot_id is None:
            existing = self.list_snapshots(adapter, limit=1)
            if existing:
                parent_snapshot_id = existing[0].snapshot_id
        
        # Count entities
        counts = self._count_entities(semantic_state)
        
        # Create snapshot
        snapshot = SemanticSnapshot(
            snapshot_id=snapshot_id,
            adapter=adapter,
            timestamp=datetime.now(timezone.utc).isoformat(),
            semantic_entities=semantic_state,
            schema_hash=schema_hash,
            parent_snapshot_id=parent_snapshot_id,
            change_description=change_description,
            **counts,
        )
        
        # Save to disk
        self._save_snapshot(snapshot)
        
        # Update index
        if adapter not in self._index:
            self._index[adapter] = []
        self._index[adapter].append(snapshot_id)
        self._save_index()
        
        logger.info(
            f"Created snapshot {snapshot_id} for {adapter}: "
            f"{snapshot.measure_count} measures, {snapshot.dimension_count} dimensions"
        )
        return snapshot
    
    def _save_snapshot(self, snapshot: SemanticSnapshot) -> None:
        """Save snapshot to disk."""
        adapter_dir = self._get_adapter_dir(snapshot.adapter)
        snapshot_path = adapter_dir / f"{snapshot.snapshot_id}.json"
        
        try:
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(snapshot.model_dump(), f, indent=2, default=str)
            logger.debug(f"Snapshot saved to {snapshot_path}")
        except Exception as e:
            logger.error(f"Failed to save snapshot: {e}")
            raise
    
    def get_snapshot(self, snapshot_id: str) -> Optional[SemanticSnapshot]:
        """
        Retrieve a specific snapshot by ID.
        
        Args:
            snapshot_id: The snapshot ID to retrieve.
            
        Returns:
            SemanticSnapshot or None if not found.
        """
        # Parse adapter from snapshot ID (v{timestamp}_{adapter}_{seq})
        parts = snapshot_id.split("_")
        if len(parts) < 3:
            logger.warning(f"Invalid snapshot ID format: {snapshot_id}")
            return None
        
        adapter = parts[-2]  # Second to last is adapter
        adapter_dir = self._get_adapter_dir(adapter)
        snapshot_path = adapter_dir / f"{snapshot_id}.json"
        
        if not snapshot_path.exists():
            # Try to find in all adapter directories
            for adapter_dir in self.snapshots_dir.iterdir():
                if adapter_dir.is_dir():
                    snapshot_path = adapter_dir / f"{snapshot_id}.json"
                    if snapshot_path.exists():
                        break
            else:
                logger.debug(f"Snapshot not found: {snapshot_id}")
                return None
        
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return SemanticSnapshot(**data)
        except Exception as e:
            logger.error(f"Failed to load snapshot {snapshot_id}: {e}")
            return None
    
    def list_snapshots(
        self,
        adapter: str,
        limit: int = 10,
        filter_criteria: Optional[Dict[str, Any]] = None,
    ) -> List[SemanticSnapshot]:
        """
        List snapshots for an adapter, newest first.
        
        Args:
            adapter: Adapter name.
            limit: Maximum number to return.
            filter_criteria: Optional filters (e.g., {"change_type": "rollback"}).
            
        Returns:
            List of SemanticSnapshots, newest first.
        """
        if adapter not in self._index:
            return []
        
        snapshot_ids = self._index[adapter]
        
        # Load newest first, up to limit
        snapshots = []
        for snapshot_id in reversed(snapshot_ids):
            if len(snapshots) >= limit:
                break
            
            snapshot = self.get_snapshot(snapshot_id)
            if snapshot:
                # Apply filters
                if filter_criteria:
                    match = True
                    for key, value in filter_criteria.items():
                        if getattr(snapshot, key, None) != value:
                            match = False
                            break
                    if not match:
                        continue
                
                snapshots.append(snapshot)
        
        return snapshots
    
    def validate_snapshot_integrity(self, snapshot_id: str) -> bool:
        """
        Verify snapshot hasn't been modified.
        
        Recomputes hash and compares to stored hash.
        
        Args:
            snapshot_id: The snapshot ID to validate.
            
        Returns:
            True if integrity is valid, False otherwise.
        """
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            logger.warning(f"Snapshot not found for integrity check: {snapshot_id}")
            return False
        
        computed_hash = self.compute_schema_hash(snapshot.semantic_entities)
        is_valid = computed_hash == snapshot.schema_hash
        
        if not is_valid:
            logger.error(
                f"Snapshot integrity check FAILED for {snapshot_id}: "
                f"expected {snapshot.schema_hash}, got {computed_hash}"
            )
        else:
            logger.debug(f"Snapshot integrity check PASSED for {snapshot_id}")
        
        return is_valid
    
    def cleanup_old_snapshots(
        self,
        adapter: str,
        retention_days: int = 90,
    ) -> int:
        """
        Remove snapshots older than retention period.
        
        Args:
            adapter: Adapter name.
            retention_days: Number of days to keep.
            
        Returns:
            Number of snapshots removed.
        """
        if adapter not in self._index:
            return 0
        
        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff = cutoff - timedelta(days=retention_days)
        
        removed = 0
        remaining_ids = []
        
        for snapshot_id in self._index[adapter]:
            snapshot = self.get_snapshot(snapshot_id)
            if snapshot:
                snapshot_time = datetime.fromisoformat(snapshot.timestamp.replace("Z", "+00:00"))
                if snapshot_time < cutoff:
                    # Delete snapshot file
                    adapter_dir = self._get_adapter_dir(adapter)
                    snapshot_path = adapter_dir / f"{snapshot_id}.json"
                    if snapshot_path.exists():
                        snapshot_path.unlink()
                        removed += 1
                        logger.info(f"Cleaned up old snapshot: {snapshot_id}")
                else:
                    remaining_ids.append(snapshot_id)
            else:
                remaining_ids.append(snapshot_id)
        
        # Update index
        self._index[adapter] = remaining_ids
        self._save_index()
        
        logger.info(f"Cleaned up {removed} old snapshots for {adapter}")
        return removed
    
    def get_semantic_state_at(
        self,
        adapter: str,
        timestamp: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get semantic state at a specific point in time.
        
        Finds the most recent snapshot before the given timestamp.
        
        Args:
            adapter: Adapter name.
            timestamp: ISO8601 timestamp.
            
        Returns:
            Semantic state dictionary or None if no snapshots exist before timestamp.
        """
        target_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        
        if adapter not in self._index:
            return None
        
        # Find most recent snapshot before target time
        best_snapshot = None
        
        for snapshot_id in self._index[adapter]:
            snapshot = self.get_snapshot(snapshot_id)
            if snapshot:
                snapshot_time = datetime.fromisoformat(
                    snapshot.timestamp.replace("Z", "+00:00")
                )
                if snapshot_time <= target_time:
                    if best_snapshot is None or snapshot_time > datetime.fromisoformat(
                        best_snapshot.timestamp.replace("Z", "+00:00")
                    ):
                        best_snapshot = snapshot
        
        if best_snapshot:
            return best_snapshot.semantic_entities
        return None
    
    def get_latest_snapshot(self, adapter: str) -> Optional[SemanticSnapshot]:
        """Get the most recent snapshot for an adapter."""
        snapshots = self.list_snapshots(adapter, limit=1)
        return snapshots[0] if snapshots else None
