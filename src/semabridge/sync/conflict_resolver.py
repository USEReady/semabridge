"""
Conflict Resolver.

Detects and manages schema/data conflicts during bidirectional
synchronization. Implements the fail-and-approve pattern where blocking
conflicts pause the sync job until a human or policy resolves them.

Conflict Detection Flow:
    1. Compare source OSI model against target's last-known schema version.
    2. Classify each diff as INFO / WARNING / CRITICAL.
    3. For FAIL_AND_APPROVE strategy: persist conflicts and halt.
    4. For SOURCE_WINS / TARGET_WINS: auto-resolve and continue.
    5. For MERGE: attempt structural merge, escalate to CRITICAL on ambiguity.
"""

from __future__ import annotations

import hashlib
import re as _re
from typing import Dict, List, Optional, Tuple

from semabridge.core.exceptions import ConflictError
from semabridge.sync.type_compat import column_type_conflict_severity as _type_sev
from semabridge.intermediate.models import OSIColumn, OSIDataset, OSIModel, OSIRelationship
from semabridge.sync.models import (
    ConflictResolution,
    ConflictSeverity,
    SchemaChangeType,
    SyncConflict,
    _new_id,
)
from semabridge.sync.repository import SyncRepository
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def _measure_semantic_hash(metric_or_dict) -> str:
    """Hash that ignores DAX vs SQL textual differences."""
    if hasattr(metric_or_dict, 'expression'):
        expr = (getattr(metric_or_dict, 'sql_expression', None)
                or metric_or_dict.expression or "")
        agg = str(getattr(metric_or_dict, 'aggregation', ''))
    else:
        expr = metric_or_dict.get('sql_expression') or metric_or_dict.get('expression', '')
        agg = str(metric_or_dict.get('aggregation', ''))
    # Normalize: upper-case, collapse whitespace, strip quotes
    expr = _re.sub(r'\s+', ' ', expr.upper()).strip()
    expr = expr.replace('"', '').replace("'", "")
    return hashlib.md5(f"{agg}:{expr}".encode()).hexdigest()


class ConflictResolver:
    """
    Detects conflicts between a source OSI model and the last-known
    target schema, then resolves or persists them based on the
    configured strategy.

    Args:
        repository: SyncRepository for persisting conflict records.
    """

    def __init__(self, repository: SyncRepository) -> None:
        self._repo = repository

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def detect_conflicts(
        self,
        source_model: OSIModel,
        target_schema: Optional[Dict] | None,
        job_id: str,
        item_id: Optional[str] = None,
    ) -> List[SyncConflict]:
        """
        Compare source OSI model against the target's last-known schema
        and return a list of SyncConflict objects.

        Args:
            source_model: The new OSI model from the source system.
            target_schema: JSON dict of the target's last schema snapshot
                           (from SchemaVersion). None if first sync.
            job_id: Parent sync job ID.
            item_id: Optional sync item ID.

        Returns:
            List of detected conflicts (may be empty).
        """
        if target_schema is None:
            logger.info(
                f"First sync for model '{source_model.unique_name}' — no conflicts"
            )
            return []

        conflicts: List[SyncConflict] = []
        model_name = source_model.unique_name

        # Build lookup maps from target schema
        target_datasets = {
            ds["unique_name"]: ds for ds in target_schema.get("datasets", [])
        }
        target_metrics = {
            m["unique_name"]: m for m in target_schema.get("metrics", [])
        }
        target_rels = {
            r["unique_name"]: r for r in target_schema.get("relationships", [])
        }

        # --- Dataset / Table diffs ---
        source_ds_names = {ds.unique_name for ds in source_model.datasets}
        target_ds_names = set(target_datasets.keys())

        # New tables
        for name in source_ds_names - target_ds_names:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.TABLE_ADDED,
                    severity=ConflictSeverity.INFO,
                    description=f"New table '{name}' will be created in target",
                    source_value={"table": name},
                )
            )

        # Removed tables
        for name in target_ds_names - source_ds_names:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.TABLE_REMOVED,
                    severity=ConflictSeverity.CRITICAL,
                    description=f"Table '{name}' exists in target but not in source — will be dropped",
                    target_value={"table": name},
                )
            )

        # Column-level diffs for overlapping tables
        for ds in source_model.datasets:
            if ds.unique_name not in target_datasets:
                continue
            target_ds = target_datasets[ds.unique_name]
            col_conflicts = self._diff_columns(
                ds, target_ds, job_id, item_id, model_name
            )
            conflicts.extend(col_conflicts)

        # --- Measure diffs ---
        source_metric_names = {m.unique_name for m in source_model.metrics}
        target_metric_names = set(target_metrics.keys())

        for name in source_metric_names - target_metric_names:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.MEASURE_ADDED,
                    severity=ConflictSeverity.INFO,
                    description=f"New measure '{name}' will be created",
                    source_value={"measure": name},
                )
            )

        for name in target_metric_names - source_metric_names:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.MEASURE_REMOVED,
                    severity=ConflictSeverity.WARNING,
                    description=f"Measure '{name}' exists in target but not in source",
                    target_value={"measure": name},
                )
            )

        # Modified measures (expression / aggregation change)
        for metric in source_model.metrics:
            if metric.unique_name not in target_metrics:
                continue
            target_m = target_metrics[metric.unique_name]
            src_expr = metric.expression or ""
            tgt_expr = target_m.get("expression", "") or ""
            if _measure_semantic_hash(metric) != _measure_semantic_hash(target_m):
                conflicts.append(
                    self._make_conflict(
                        job_id=job_id,
                        item_id=item_id,
                        model_name=model_name,
                        change_type=SchemaChangeType.MEASURE_MODIFIED,
                        severity=ConflictSeverity.WARNING,
                        description=(
                            f"Measure '{metric.unique_name}' expression changed"
                        ),
                        source_value={"expression": src_expr},
                        target_value={"expression": tgt_expr},
                    )
                )

        # --- Relationship diffs ---
        source_rel_names = {r.unique_name for r in source_model.relationships}
        target_rel_names = set(target_rels.keys())

        for name in source_rel_names - target_rel_names:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.RELATIONSHIP_ADDED,
                    severity=ConflictSeverity.INFO,
                    description=f"New relationship '{name}' will be created",
                    source_value={"relationship": name},
                )
            )

        for name in target_rel_names - source_rel_names:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.RELATIONSHIP_REMOVED,
                    severity=ConflictSeverity.WARNING,
                    description=f"Relationship '{name}' removed from source",
                    target_value={"relationship": name},
                )
            )

        # Relationship cardinality / cross-filter changes
        for src_rel in source_model.relationships:
            if src_rel.unique_name not in target_rels:
                continue
            tgt_rel = target_rels[src_rel.unique_name]
            src_card = src_rel.cardinality.value if hasattr(src_rel.cardinality, "value") else str(src_rel.cardinality)
            tgt_card = tgt_rel.get("cardinality", "")
            if src_card != tgt_card:
                conflicts.append(
                    self._make_conflict(
                        job_id=job_id,
                        item_id=item_id,
                        model_name=model_name,
                        change_type=SchemaChangeType.RELATIONSHIP_MODIFIED,
                        severity=ConflictSeverity.WARNING,
                        description=(
                            f"Relationship '{src_rel.unique_name}' cardinality changed: "
                            f"{tgt_card} → {src_card}"
                        ),
                        source_value={"cardinality": src_card},
                        target_value={"cardinality": tgt_card},
                    )
                )
            src_cf = src_rel.cross_filter_direction.value if hasattr(src_rel.cross_filter_direction, "value") else str(src_rel.cross_filter_direction)
            tgt_cf = tgt_rel.get("cross_filter_direction", "")
            if src_cf != tgt_cf:
                conflicts.append(
                    self._make_conflict(
                        job_id=job_id,
                        item_id=item_id,
                        model_name=model_name,
                        change_type=SchemaChangeType.RELATIONSHIP_MODIFIED,
                        severity=ConflictSeverity.WARNING,
                        description=(
                            f"Relationship '{src_rel.unique_name}' cross-filter changed: "
                            f"{tgt_cf} → {src_cf}"
                        ),
                        source_value={"cross_filter_direction": src_cf},
                        target_value={"cross_filter_direction": tgt_cf},
                    )
                )

        # --- Hierarchy diffs ---
        target_hierarchies = {
            h_name
            for dim in target_schema.get("dimensions", [])
            for h_name in [h.get("unique_name", "") for h in dim.get("hierarchies", [])]
            if h_name
        }
        source_hierarchies = {
            h.unique_name
            for dim in source_model.dimensions
            for h in dim.hierarchies
        }
        for h_name in source_hierarchies - target_hierarchies:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.HIERARCHY_ADDED,
                    severity=ConflictSeverity.INFO,
                    description=f"New hierarchy '{h_name}' will be created",
                    source_value={"hierarchy": h_name},
                )
            )
        for h_name in target_hierarchies - source_hierarchies:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.HIERARCHY_REMOVED,
                    severity=ConflictSeverity.WARNING,
                    description=f"Hierarchy '{h_name}' removed from source",
                    target_value={"hierarchy": h_name},
                )
            )

        logger.info(
            f"Detected {len(conflicts)} conflicts for '{model_name}' "
            f"(critical={sum(1 for c in conflicts if c.severity == ConflictSeverity.CRITICAL)})"
        )
        return conflicts

    def apply_strategy(
        self,
        conflicts: List[SyncConflict],
        strategy: ConflictResolution,
        job_id: str,
    ) -> Tuple[bool, List[SyncConflict]]:
        """
        Apply the conflict resolution strategy to a list of conflicts.

        Args:
            conflicts: Detected conflicts.
            strategy: Resolution strategy.
            job_id: Parent job ID.

        Returns:
            Tuple of (can_proceed: bool, persisted_conflicts: List).
            can_proceed is False when there are CRITICAL conflicts and
            strategy is FAIL_AND_APPROVE.
        """
        if not conflicts:
            return True, []

        persisted: List[SyncConflict] = []

        for conflict in conflicts:
            if strategy == ConflictResolution.SOURCE_WINS:
                conflict.resolution = ConflictResolution.SOURCE_WINS
                conflict.resolved_by = "auto:source_wins"
            elif strategy == ConflictResolution.TARGET_WINS:
                conflict.resolution = ConflictResolution.TARGET_WINS
                conflict.resolved_by = "auto:target_wins"
            elif strategy == ConflictResolution.MERGE:
                if conflict.severity == ConflictSeverity.CRITICAL:
                    # Cannot auto-merge critical conflicts
                    pass
                else:
                    conflict.resolution = ConflictResolution.MERGE
                    conflict.resolved_by = "auto:merge"
            # FAIL_AND_APPROVE: leave unresolved

            self._repo.create_conflict(conflict)
            persisted.append(conflict)

        # Determine if we can proceed
        unresolved_critical = [
            c
            for c in persisted
            if c.resolution is None and c.severity == ConflictSeverity.CRITICAL
        ]

        if unresolved_critical and strategy == ConflictResolution.FAIL_AND_APPROVE:
            logger.warning(
                f"Sync job {job_id} blocked by {len(unresolved_critical)} "
                f"critical unresolved conflicts"
            )
            return False, persisted

        return True, persisted

    def resolve_all(
        self,
        job_id: str,
        resolution: ConflictResolution,
        resolved_by: str = "user",
    ) -> int:
        """
        Bulk-resolve all unresolved conflicts for a job.

        Args:
            job_id: Sync job ID.
            resolution: Resolution to apply.
            resolved_by: Who resolved the conflicts.

        Returns:
            Number of conflicts resolved.
        """
        unresolved = self._repo.get_unresolved_conflicts(job_id)
        for conflict in unresolved:
            self._repo.resolve_conflict(
                conflict.conflict_id, resolution, resolved_by
            )
        logger.info(
            f"Resolved {len(unresolved)} conflicts for job {job_id} "
            f"with strategy={resolution.value}"
        )
        return len(unresolved)

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    def _diff_columns(
        self,
        source_ds: OSIDataset,
        target_ds: Dict,
        job_id: str,
        item_id: Optional[str],
        model_name: str,
    ) -> List[SyncConflict]:
        """Diff columns between source OSI dataset and target schema dict."""
        conflicts: List[SyncConflict] = []
        table_name = source_ds.unique_name

        target_cols = {
            c["unique_name"]: c for c in target_ds.get("columns", [])
        }
        source_col_names = {c.unique_name for c in source_ds.columns}
        target_col_names = set(target_cols.keys())

        # Added columns
        for name in source_col_names - target_col_names:
            col = next(c for c in source_ds.columns if c.unique_name == name)
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.COLUMN_ADDED,
                    severity=ConflictSeverity.INFO,
                    description=f"Column '{name}' added to table '{table_name}'",
                    source_value={
                        "column": name,
                        "table": table_name,
                        "data_type": col.data_type.value,
                    },
                )
            )

        # Removed columns
        for name in target_col_names - source_col_names:
            conflicts.append(
                self._make_conflict(
                    job_id=job_id,
                    item_id=item_id,
                    model_name=model_name,
                    change_type=SchemaChangeType.COLUMN_REMOVED,
                    severity=ConflictSeverity.WARNING,
                    description=f"Column '{name}' removed from table '{table_name}'",
                    target_value={
                        "column": name,
                        "table": table_name,
                    },
                )
            )

        # Type changes
        for col in source_ds.columns:
            if col.unique_name not in target_cols:
                continue
            target_col = target_cols[col.unique_name]
            src_type = col.data_type.value
            tgt_type = target_col.get("data_type", "unknown")
            if src_type != tgt_type:
                _sev_str = _type_sev(src_type, tgt_type)
                _sev = ConflictSeverity[_sev_str]
                conflicts.append(
                    self._make_conflict(
                        job_id=job_id,
                        item_id=item_id,
                        model_name=model_name,
                        change_type=SchemaChangeType.COLUMN_TYPE_CHANGED,
                        severity=_sev,
                        description=(
                            f"Column '{col.unique_name}' in '{table_name}' "
                            f"type changed: {tgt_type} → {src_type}"
                        ),
                        source_value={"data_type": src_type},
                        target_value={"data_type": tgt_type},
                    )
                )

        return conflicts

    @staticmethod
    def _make_conflict(
        job_id: str,
        item_id: Optional[str],
        model_name: str,
        change_type: SchemaChangeType,
        severity: ConflictSeverity,
        description: str,
        source_value: Optional[Dict] = None,
        target_value: Optional[Dict] = None,
    ) -> SyncConflict:
        """Factory for SyncConflict with consistent defaults."""
        return SyncConflict(
            conflict_id=_new_id(),
            job_id=job_id,
            item_id=item_id,
            model_name=model_name,
            change_type=change_type,
            severity=severity,
            description=description,
            source_value=source_value,
            target_value=target_value,
        )
