"""
Schema Evolution Tracker.

Tracks structural changes to semantic models across sync iterations
and produces versioned schema snapshots. Used by the ConflictResolver
to detect drift between source and target.

Schema Fingerprinting:
    - OSI model → canonical JSON dict (sorted keys).
    - SHA-256 hash of that JSON = schema_hash.
    - If hash unchanged since last sync → skip (incremental).
    - If hash changed → diff against previous SchemaVersion.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from semabridge.intermediate.models import OSIModel
from semabridge.sync.models import (
    SchemaChangeType,
    SchemaVersion,
    _new_id,
)
from semabridge.sync.repository import SyncRepository
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SchemaEvolutionTracker:
    """
    Manages schema versioning and change detection for OSI models.

    Each time a model is synced, the tracker:
    1. Computes a schema fingerprint (hash).
    2. Compares against the previous version.
    3. Records a new SchemaVersion with a list of changes.

    Args:
        repository: SyncRepository for persistence.
    """

    def __init__(self, repository: SyncRepository) -> None:
        self._repo = repository

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def compute_schema_hash(self, model: OSIModel) -> str:
        """
        Compute a deterministic hash of the model's schema structure.

        Only structural elements (datasets, columns, types, relationships,
        metrics) are included — not display labels or descriptions.

        Args:
            model: OSI model to fingerprint.

        Returns:
            SHA-256 hex digest string.
        """
        snapshot = self._model_to_schema_snapshot(model)
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def has_changed(self, model: OSIModel) -> bool:
        """
        Check if the model's schema has changed since the last version.

        Args:
            model: OSI model to check.

        Returns:
            True if schema changed or if this is a new model.
        """
        current_hash = self.compute_schema_hash(model)
        latest = self._repo.get_latest_schema_version(model.unique_name)
        if latest is None:
            return True
        return latest.schema_hash != current_hash

    def record_version(
        self,
        model: OSIModel,
        job_id: Optional[str] = None,
    ) -> SchemaVersion:
        """
        Record a new schema version for the model.

        Computes the hash, diffs against the previous version, and
        persists a new SchemaVersion record.

        Args:
            model: OSI model whose schema to record.
            job_id: Optional sync job that triggered this version.

        Returns:
            The newly created SchemaVersion.
        """
        snapshot = self._model_to_schema_snapshot(model)
        schema_hash = self.compute_schema_hash(model)

        latest = self._repo.get_latest_schema_version(model.unique_name)
        version_number = (latest.version_number + 1) if latest else 1

        # Compute changes from previous version
        changes: List[Dict[str, Any]] = []
        if latest:
            changes = self._diff_snapshots(latest.schema_snapshot, snapshot)

        version = SchemaVersion(
            version_id=_new_id(),
            model_name=model.unique_name,
            version_number=version_number,
            schema_hash=schema_hash,
            schema_snapshot=snapshot,
            changes_from_previous=changes,
            created_by_job_id=job_id,
        )

        self._repo.create_schema_version(version)
        logger.info(
            f"Recorded schema v{version_number} for '{model.unique_name}' "
            f"(hash={schema_hash[:12]}…, changes={len(changes)})"
        )
        return version

    def get_target_schema(self, model_name: str) -> Optional[Dict[str, Any]]:
        """
        Get the last-known schema snapshot for a model.

        Used by the ConflictResolver to compare against the source.

        Args:
            model_name: Canonical model name.

        Returns:
            Schema snapshot dict or None if no versions exist.
        """
        latest = self._repo.get_latest_schema_version(model_name)
        if latest is None:
            return None
        return latest.schema_snapshot

    def get_history(
        self, model_name: str, limit: int = 20
    ) -> List[SchemaVersion]:
        """
        Get version history for a model.

        Args:
            model_name: Canonical model name.
            limit: Maximum number of versions to return.

        Returns:
            List of SchemaVersion objects (newest first).
        """
        return self._repo.get_schema_history(model_name, limit)

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _model_to_schema_snapshot(model: OSIModel) -> Dict[str, Any]:
        """
        Convert an OSI model to a canonical schema snapshot dict.

        Only includes structural fields relevant for change detection.
        """
        datasets = []
        for ds in sorted(model.datasets, key=lambda d: d.unique_name):
            columns = []
            for col in sorted(ds.columns, key=lambda c: c.unique_name):
                columns.append(
                    {
                        "unique_name": col.unique_name,
                        "data_type": col.data_type.value,
                        "is_key": col.is_key,
                    }
                )
            datasets.append(
                {
                    "unique_name": ds.unique_name,
                    "source_table": ds.source_table,
                    "source_schema": ds.source_schema,
                    "columns": columns,
                }
            )

        metrics = []
        for m in sorted(model.metrics, key=lambda m: m.unique_name):
            metrics.append(
                {
                    "unique_name": m.unique_name,
                    "dataset": m.dataset,
                    "expression": m.expression,
                    "aggregation": m.aggregation.value,
                    "source_column": m.source_column,
                }
            )

        relationships = []
        for r in sorted(model.relationships, key=lambda r: r.unique_name):
            relationships.append(
                {
                    "unique_name": r.unique_name,
                    "from_dataset": r.from_dataset,
                    "from_columns": r.from_columns,
                    "to_dataset": r.to_dataset,
                    "to_columns": r.to_columns,
                    "cardinality": r.cardinality.value,
                }
            )

        return {
            "unique_name": model.unique_name,
            "datasets": datasets,
            "metrics": metrics,
            "relationships": relationships,
        }

    @staticmethod
    def _diff_snapshots(
        old: Dict[str, Any],
        new: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Diff two schema snapshots and return a list of change records.

        Each record has: change_type, object_name, details.
        """
        changes: List[Dict[str, Any]] = []

        # --- Dataset diffs ---
        old_ds = {d["unique_name"]: d for d in old.get("datasets", [])}
        new_ds = {d["unique_name"]: d for d in new.get("datasets", [])}

        for name in set(new_ds) - set(old_ds):
            changes.append(
                {
                    "change_type": SchemaChangeType.TABLE_ADDED.value,
                    "object_name": name,
                }
            )
        for name in set(old_ds) - set(new_ds):
            changes.append(
                {
                    "change_type": SchemaChangeType.TABLE_REMOVED.value,
                    "object_name": name,
                }
            )

        # Column diffs within shared tables
        for name in set(old_ds) & set(new_ds):
            old_cols = {c["unique_name"]: c for c in old_ds[name].get("columns", [])}
            new_cols = {c["unique_name"]: c for c in new_ds[name].get("columns", [])}

            for cname in set(new_cols) - set(old_cols):
                changes.append(
                    {
                        "change_type": SchemaChangeType.COLUMN_ADDED.value,
                        "object_name": f"{name}.{cname}",
                        "details": {"data_type": new_cols[cname].get("data_type")},
                    }
                )
            for cname in set(old_cols) - set(new_cols):
                changes.append(
                    {
                        "change_type": SchemaChangeType.COLUMN_REMOVED.value,
                        "object_name": f"{name}.{cname}",
                    }
                )
            for cname in set(old_cols) & set(new_cols):
                if old_cols[cname].get("data_type") != new_cols[cname].get("data_type"):
                    changes.append(
                        {
                            "change_type": SchemaChangeType.COLUMN_TYPE_CHANGED.value,
                            "object_name": f"{name}.{cname}",
                            "details": {
                                "old_type": old_cols[cname].get("data_type"),
                                "new_type": new_cols[cname].get("data_type"),
                            },
                        }
                    )

        # --- Metric diffs ---
        old_m = {m["unique_name"]: m for m in old.get("metrics", [])}
        new_m = {m["unique_name"]: m for m in new.get("metrics", [])}

        for name in set(new_m) - set(old_m):
            changes.append(
                {
                    "change_type": SchemaChangeType.MEASURE_ADDED.value,
                    "object_name": name,
                }
            )
        for name in set(old_m) - set(new_m):
            changes.append(
                {
                    "change_type": SchemaChangeType.MEASURE_REMOVED.value,
                    "object_name": name,
                }
            )
        for name in set(old_m) & set(new_m):
            if old_m[name].get("expression") != new_m[name].get("expression"):
                changes.append(
                    {
                        "change_type": SchemaChangeType.MEASURE_MODIFIED.value,
                        "object_name": name,
                        "details": {
                            "old_expression": old_m[name].get("expression"),
                            "new_expression": new_m[name].get("expression"),
                        },
                    }
                )

        # --- Relationship diffs ---
        old_r = {r["unique_name"]: r for r in old.get("relationships", [])}
        new_r = {r["unique_name"]: r for r in new.get("relationships", [])}

        for name in set(new_r) - set(old_r):
            changes.append(
                {
                    "change_type": SchemaChangeType.RELATIONSHIP_ADDED.value,
                    "object_name": name,
                }
            )
        for name in set(old_r) - set(new_r):
            changes.append(
                {
                    "change_type": SchemaChangeType.RELATIONSHIP_REMOVED.value,
                    "object_name": name,
                }
            )

        return changes
