"""
Semantic Diff Engine.

Compares semantic states and generates human-readable diffs.
Identifies changes at entity level (measures, dimensions, hierarchies, relationships).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from semabridge.repository.semantic_snapshot_manager import SemanticSnapshot, SemanticSnapshotManager
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class ChangeType(str, Enum):
    """Type of change detected."""
    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    UNCHANGED = "unchanged"


class EntityType(str, Enum):
    """Type of semantic entity."""
    MEASURE = "measure"
    DIMENSION = "dimension"
    HIERARCHY = "hierarchy"
    RELATIONSHIP = "relationship"
    DATASET = "dataset"
    COLUMN = "column"


@dataclass
class EntityChange:
    """A single entity change."""
    entity_type: EntityType
    entity_id: str
    entity_name: str
    change_type: ChangeType
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    change_reason: str = ""
    is_breaking: bool = False


@dataclass
class DiffSummary:
    """Summary of changes between two snapshots."""
    measures_added: int = 0
    measures_modified: int = 0
    measures_removed: int = 0
    dimensions_added: int = 0
    dimensions_modified: int = 0
    dimensions_removed: int = 0
    hierarchies_added: int = 0
    hierarchies_modified: int = 0
    hierarchies_removed: int = 0
    relationships_added: int = 0
    relationships_modified: int = 0
    relationships_removed: int = 0
    datasets_added: int = 0
    datasets_modified: int = 0
    datasets_removed: int = 0
    
    @property
    def total_changes(self) -> int:
        """Total number of changes."""
        return (
            self.measures_added + self.measures_modified + self.measures_removed +
            self.dimensions_added + self.dimensions_modified + self.dimensions_removed +
            self.hierarchies_added + self.hierarchies_modified + self.hierarchies_removed +
            self.relationships_added + self.relationships_modified + self.relationships_removed +
            self.datasets_added + self.datasets_modified + self.datasets_removed
        )
    
    @property
    def has_breaking_changes(self) -> bool:
        """Check if there are potentially breaking changes."""
        return (
            self.measures_removed > 0 or
            self.dimensions_removed > 0 or
            self.hierarchies_removed > 0 or
            self.relationships_removed > 0 or
            self.datasets_removed > 0
        )


@dataclass
class SemanticDiff:
    """Full diff result between two snapshots."""
    from_snapshot_id: str
    to_snapshot_id: str
    from_adapter: str
    to_adapter: str
    summary: DiffSummary
    changes: List[EntityChange] = field(default_factory=list)
    breaking_changes: List[EntityChange] = field(default_factory=list)
    generated_at: str = ""
    comparison_duration_ms: int = 0
    
    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()


class SemanticDiffEngine:
    """
    Compare semantic states and generate human-readable diffs.
    
    Responsibilities:
    - Compare two semantic snapshots
    - Identify changes at entity level (measures, dimensions, hierarchies)
    - Classify changes (added, modified, removed)
    - Generate detailed diff reports
    - Highlight breaking changes
    """
    
    def __init__(self, snapshot_manager: Optional[SemanticSnapshotManager] = None):
        """
        Initialize the diff engine.
        
        Args:
            snapshot_manager: Optional snapshot manager for retrieving snapshots.
        """
        self.snapshot_manager = snapshot_manager or SemanticSnapshotManager()
    
    def compare_snapshots(
        self,
        snapshot_id_1: str,
        snapshot_id_2: str,
    ) -> SemanticDiff:
        """
        Generate comprehensive diff between two snapshots.
        
        Args:
            snapshot_id_1: The "from" snapshot (older).
            snapshot_id_2: The "to" snapshot (newer).
            
        Returns:
            SemanticDiff with all changes.
            
        Raises:
            ValueError: If either snapshot not found.
        """
        import time
        start_time = time.time()
        
        # Load snapshots
        snapshot_1 = self.snapshot_manager.get_snapshot(snapshot_id_1)
        snapshot_2 = self.snapshot_manager.get_snapshot(snapshot_id_2)
        
        if snapshot_1 is None:
            raise ValueError(f"Snapshot not found: {snapshot_id_1}")
        if snapshot_2 is None:
            raise ValueError(f"Snapshot not found: {snapshot_id_2}")
        
        # Compare states
        diff = self.compare_states(
            snapshot_1.semantic_entities,
            snapshot_2.semantic_entities,
            from_snapshot_id=snapshot_id_1,
            to_snapshot_id=snapshot_id_2,
            from_adapter=snapshot_1.adapter,
            to_adapter=snapshot_2.adapter,
        )
        
        diff.comparison_duration_ms = int((time.time() - start_time) * 1000)
        
        logger.info(
            f"Compared {snapshot_id_1} vs {snapshot_id_2}: "
            f"{diff.summary.total_changes} changes, "
            f"{len(diff.breaking_changes)} breaking"
        )
        
        return diff
    
    def compare_states(
        self,
        state_1: Dict[str, Any],
        state_2: Dict[str, Any],
        from_snapshot_id: str = "current",
        to_snapshot_id: str = "proposed",
        from_adapter: str = "unknown",
        to_adapter: str = "unknown",
    ) -> SemanticDiff:
        """
        Compare two semantic states directly.
        
        Args:
            state_1: The "from" state (older).
            state_2: The "to" state (newer).
            from_snapshot_id: ID for the from state.
            to_snapshot_id: ID for the to state.
            from_adapter: Adapter for from state.
            to_adapter: Adapter for to state.
            
        Returns:
            SemanticDiff with all changes.
        """
        changes: List[EntityChange] = []
        summary = DiffSummary()
        
        # Compare measures (metrics)
        measure_changes = self._compare_entities(
            state_1.get("metrics", []),
            state_2.get("metrics", []),
            EntityType.MEASURE,
            key_field="unique_name",
        )
        changes.extend(measure_changes)
        
        for c in measure_changes:
            if c.change_type == ChangeType.ADDED:
                summary.measures_added += 1
            elif c.change_type == ChangeType.MODIFIED:
                summary.measures_modified += 1
            elif c.change_type == ChangeType.REMOVED:
                summary.measures_removed += 1
                c.is_breaking = True
        
        # Compare dimensions
        dim_changes = self._compare_entities(
            state_1.get("dimensions", []),
            state_2.get("dimensions", []),
            EntityType.DIMENSION,
            key_field="unique_name",
        )
        changes.extend(dim_changes)
        
        for c in dim_changes:
            if c.change_type == ChangeType.ADDED:
                summary.dimensions_added += 1
            elif c.change_type == ChangeType.MODIFIED:
                summary.dimensions_modified += 1
            elif c.change_type == ChangeType.REMOVED:
                summary.dimensions_removed += 1
                c.is_breaking = True
        
        # Compare relationships
        rel_changes = self._compare_entities(
            state_1.get("relationships", []),
            state_2.get("relationships", []),
            EntityType.RELATIONSHIP,
            key_field="unique_name",
        )
        changes.extend(rel_changes)
        
        for c in rel_changes:
            if c.change_type == ChangeType.ADDED:
                summary.relationships_added += 1
            elif c.change_type == ChangeType.MODIFIED:
                summary.relationships_modified += 1
            elif c.change_type == ChangeType.REMOVED:
                summary.relationships_removed += 1
                c.is_breaking = True
        
        # Compare datasets
        dataset_changes = self._compare_entities(
            state_1.get("datasets", []),
            state_2.get("datasets", []),
            EntityType.DATASET,
            key_field="unique_name",
        )
        changes.extend(dataset_changes)
        
        for c in dataset_changes:
            if c.change_type == ChangeType.ADDED:
                summary.datasets_added += 1
            elif c.change_type == ChangeType.MODIFIED:
                summary.datasets_modified += 1
            elif c.change_type == ChangeType.REMOVED:
                summary.datasets_removed += 1
                c.is_breaking = True
        
        # Extract breaking changes
        breaking_changes = [c for c in changes if c.is_breaking]
        
        return SemanticDiff(
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            from_adapter=from_adapter,
            to_adapter=to_adapter,
            summary=summary,
            changes=changes,
            breaking_changes=breaking_changes,
        )
    
    def _compare_entities(
        self,
        entities_1: List[Dict[str, Any]],
        entities_2: List[Dict[str, Any]],
        entity_type: EntityType,
        key_field: str = "unique_name",
    ) -> List[EntityChange]:
        """Compare two lists of entities."""
        changes = []
        
        # Index by key
        index_1 = {e.get(key_field, ""): e for e in entities_1}
        index_2 = {e.get(key_field, ""): e for e in entities_2}
        
        all_keys = set(index_1.keys()) | set(index_2.keys())
        
        for key in all_keys:
            e1 = index_1.get(key)
            e2 = index_2.get(key)
            
            if e1 is None and e2 is not None:
                # Added
                changes.append(EntityChange(
                    entity_type=entity_type,
                    entity_id=key,
                    entity_name=e2.get("label", key),
                    change_type=ChangeType.ADDED,
                    before=None,
                    after=e2,
                    change_reason=f"New {entity_type.value} added",
                ))
            elif e1 is not None and e2 is None:
                # Removed
                changes.append(EntityChange(
                    entity_type=entity_type,
                    entity_id=key,
                    entity_name=e1.get("label", key),
                    change_type=ChangeType.REMOVED,
                    before=e1,
                    after=None,
                    change_reason=f"{entity_type.value.capitalize()} removed",
                    is_breaking=True,
                ))
            elif e1 != e2:
                # Modified
                change_reason = self._describe_modification(e1, e2, entity_type)
                changes.append(EntityChange(
                    entity_type=entity_type,
                    entity_id=key,
                    entity_name=e1.get("label", key),
                    change_type=ChangeType.MODIFIED,
                    before=e1,
                    after=e2,
                    change_reason=change_reason,
                ))
        
        return changes
    
    def _describe_modification(
        self,
        before: Dict[str, Any],
        after: Dict[str, Any],
        entity_type: EntityType,
    ) -> str:
        """Generate a human-readable description of modification."""
        changed_fields = []
        
        all_keys = set(before.keys()) | set(after.keys())
        
        for key in all_keys:
            v1 = before.get(key)
            v2 = after.get(key)
            if v1 != v2:
                changed_fields.append(key)
        
        if not changed_fields:
            return "Unknown modification"
        
        if len(changed_fields) == 1:
            return f"Changed {changed_fields[0]}"
        elif len(changed_fields) <= 3:
            return f"Changed {', '.join(changed_fields)}"
        else:
            return f"Changed {len(changed_fields)} fields"
    
    def get_measure_diff(
        self,
        from_snapshot_id: str,
        to_snapshot_id: str,
        measure_id: str,
    ) -> Optional[EntityChange]:
        """
        Get focused diff for a single measure.
        
        Args:
            from_snapshot_id: The "from" snapshot.
            to_snapshot_id: The "to" snapshot.
            measure_id: The measure unique_name to focus on.
            
        Returns:
            EntityChange for the measure or None if unchanged.
        """
        diff = self.compare_snapshots(from_snapshot_id, to_snapshot_id)
        
        for change in diff.changes:
            if change.entity_type == EntityType.MEASURE and change.entity_id == measure_id:
                return change
        
        return None
    
    def get_dimension_diff(
        self,
        from_snapshot_id: str,
        to_snapshot_id: str,
        dimension_id: str,
    ) -> Optional[EntityChange]:
        """
        Get focused diff for a single dimension.
        
        Args:
            from_snapshot_id: The "from" snapshot.
            to_snapshot_id: The "to" snapshot.
            dimension_id: The dimension unique_name to focus on.
            
        Returns:
            EntityChange for the dimension or None if unchanged.
        """
        diff = self.compare_snapshots(from_snapshot_id, to_snapshot_id)
        
        for change in diff.changes:
            if change.entity_type == EntityType.DIMENSION and change.entity_id == dimension_id:
                return change
        
        return None
    
    def highlight_breaking_changes(self, diff: SemanticDiff) -> List[Dict[str, Any]]:
        """
        Flag changes that impact downstream systems.
        
        Args:
            diff: The SemanticDiff to analyze.
            
        Returns:
            List of breaking change details with impact analysis.
        """
        breaking = []
        
        for change in diff.breaking_changes:
            impact = {
                "entity_type": change.entity_type.value,
                "entity_name": change.entity_name,
                "change_type": change.change_type.value,
                "impact_level": "HIGH" if change.entity_type in [
                    EntityType.MEASURE, EntityType.RELATIONSHIP
                ] else "MEDIUM",
                "recommendation": self._get_breaking_change_recommendation(change),
            }
            breaking.append(impact)
        
        return breaking
    
    def _get_breaking_change_recommendation(self, change: EntityChange) -> str:
        """Get recommendation for handling a breaking change."""
        if change.change_type == ChangeType.REMOVED:
            if change.entity_type == EntityType.MEASURE:
                return "Check for reports/dashboards using this measure before removing"
            elif change.entity_type == EntityType.DIMENSION:
                return "Verify no slicers or groupings depend on this dimension"
            elif change.entity_type == EntityType.RELATIONSHIP:
                return "Ensure data model integrity; may affect joins"
        return "Review impact before applying"
    
    def export_diff_as_report(
        self,
        diff: SemanticDiff,
        format: str = "json",
    ) -> str:
        """
        Export diff in specified format.
        
        Args:
            diff: The SemanticDiff to export.
            format: Output format ('json', 'cli', 'html').
            
        Returns:
            Formatted diff string.
        """
        if format == "json":
            return self._export_json(diff)
        elif format == "cli":
            return self._export_cli(diff)
        elif format == "html":
            return self._export_html(diff)
        else:
            raise ValueError(f"Unknown format: {format}")
    
    def _export_json(self, diff: SemanticDiff) -> str:
        """Export diff as JSON."""
        data = {
            "from_snapshot": diff.from_snapshot_id,
            "to_snapshot": diff.to_snapshot_id,
            "diff_summary": {
                "measures_added": diff.summary.measures_added,
                "measures_modified": diff.summary.measures_modified,
                "measures_removed": diff.summary.measures_removed,
                "dimensions_added": diff.summary.dimensions_added,
                "dimensions_modified": diff.summary.dimensions_modified,
                "dimensions_removed": diff.summary.dimensions_removed,
                "relationships_added": diff.summary.relationships_added,
                "relationships_modified": diff.summary.relationships_modified,
                "relationships_removed": diff.summary.relationships_removed,
                "total_changes": diff.summary.total_changes,
                "has_breaking_changes": diff.summary.has_breaking_changes,
            },
            "changes": [
                {
                    "entity_type": c.entity_type.value,
                    "entity_id": c.entity_id,
                    "entity_name": c.entity_name,
                    "change_type": c.change_type.value,
                    "before": c.before,
                    "after": c.after,
                    "change_reason": c.change_reason,
                    "is_breaking": c.is_breaking,
                }
                for c in diff.changes
            ],
            "generated_at": diff.generated_at,
            "comparison_duration_ms": diff.comparison_duration_ms,
        }
        return json.dumps(data, indent=2, default=str)
    
    def _export_cli(self, diff: SemanticDiff) -> str:
        """Export diff as CLI-friendly text."""
        lines = []
        lines.append("=" * 60)
        lines.append(f"Comparison: {diff.from_snapshot_id} vs {diff.to_snapshot_id}")
        lines.append("=" * 60)
        lines.append("")
        
        # Summary
        lines.append("SUMMARY:")
        lines.append(f"  Total Changes: {diff.summary.total_changes}")
        if diff.summary.has_breaking_changes:
            lines.append(f"  ⚠ BREAKING CHANGES DETECTED: {len(diff.breaking_changes)}")
        lines.append("")
        
        # Measures
        if diff.summary.measures_added + diff.summary.measures_modified + diff.summary.measures_removed > 0:
            lines.append("MEASURES:")
            if diff.summary.measures_added:
                lines.append(f"  ✓ Added ({diff.summary.measures_added})")
            if diff.summary.measures_modified:
                lines.append(f"  ⚠ Modified ({diff.summary.measures_modified})")
            if diff.summary.measures_removed:
                lines.append(f"  ✗ Removed ({diff.summary.measures_removed})")
            lines.append("")
        
        # Dimensions
        if diff.summary.dimensions_added + diff.summary.dimensions_modified + diff.summary.dimensions_removed > 0:
            lines.append("DIMENSIONS:")
            if diff.summary.dimensions_added:
                lines.append(f"  ✓ Added ({diff.summary.dimensions_added})")
            if diff.summary.dimensions_modified:
                lines.append(f"  ⚠ Modified ({diff.summary.dimensions_modified})")
            if diff.summary.dimensions_removed:
                lines.append(f"  ✗ Removed ({diff.summary.dimensions_removed})")
            lines.append("")
        
        # Relationships
        if diff.summary.relationships_added + diff.summary.relationships_modified + diff.summary.relationships_removed > 0:
            lines.append("RELATIONSHIPS:")
            if diff.summary.relationships_added:
                lines.append(f"  ✓ Added ({diff.summary.relationships_added})")
            if diff.summary.relationships_modified:
                lines.append(f"  ⚠ Modified ({diff.summary.relationships_modified})")
            if diff.summary.relationships_removed:
                lines.append(f"  ✗ Removed ({diff.summary.relationships_removed})")
            lines.append("")
        
        # Detailed changes
        if diff.changes:
            lines.append("DETAILED CHANGES:")
            for c in diff.changes:
                symbol = {"added": "+", "modified": "~", "removed": "-"}.get(c.change_type.value, "?")
                lines.append(f"  [{symbol}] {c.entity_type.value}: {c.entity_name}")
                lines.append(f"      {c.change_reason}")
            lines.append("")
        
        # Breaking changes
        if diff.breaking_changes:
            lines.append("⚠ BREAKING CHANGES:")
            for c in diff.breaking_changes:
                lines.append(f"  - {c.entity_type.value}: {c.entity_name}")
                lines.append(f"    Recommendation: {self._get_breaking_change_recommendation(c)}")
            lines.append("")
        
        lines.append(f"Generated at: {diff.generated_at}")
        lines.append(f"Comparison took: {diff.comparison_duration_ms}ms")
        
        return "\n".join(lines)
    
    def _export_html(self, diff: SemanticDiff) -> str:
        """Export diff as HTML report."""
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Semantic Diff Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 15px; border-radius: 5px; }}
        .added {{ color: #28a745; }}
        .modified {{ color: #ffc107; }}
        .removed {{ color: #dc3545; }}
        .breaking {{ background: #fff3cd; border: 1px solid #ffc107; padding: 10px; margin: 10px 0; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f2f2f2; }}
    </style>
</head>
<body>
    <h1>Semantic Diff Report</h1>
    <p>From: <code>{diff.from_snapshot_id}</code> → To: <code>{diff.to_snapshot_id}</code></p>
    
    <div class="summary">
        <h2>Summary</h2>
        <p>Total Changes: <strong>{diff.summary.total_changes}</strong></p>
        <ul>
            <li class="added">Measures Added: {diff.summary.measures_added}</li>
            <li class="modified">Measures Modified: {diff.summary.measures_modified}</li>
            <li class="removed">Measures Removed: {diff.summary.measures_removed}</li>
            <li class="added">Dimensions Added: {diff.summary.dimensions_added}</li>
            <li class="modified">Dimensions Modified: {diff.summary.dimensions_modified}</li>
            <li class="removed">Dimensions Removed: {diff.summary.dimensions_removed}</li>
        </ul>
    </div>
    
    {"<div class='breaking'><h3>⚠ Breaking Changes Detected</h3><p>" + str(len(diff.breaking_changes)) + " breaking changes</p></div>" if diff.breaking_changes else ""}
    
    <h2>All Changes</h2>
    <table>
        <tr><th>Type</th><th>Entity</th><th>Change</th><th>Reason</th></tr>
        {"".join(f"<tr><td>{c.entity_type.value}</td><td>{c.entity_name}</td><td class='{c.change_type.value}'>{c.change_type.value}</td><td>{c.change_reason}</td></tr>" for c in diff.changes)}
    </table>
    
    <p><em>Generated at {diff.generated_at} ({diff.comparison_duration_ms}ms)</em></p>
</body>
</html>"""
        return html
