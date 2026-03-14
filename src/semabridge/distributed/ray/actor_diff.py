"""
Ray Actor for SML Tree-Edit-Distance (RTED) Diffing.

Each actor instance maintains an in-memory baseline for a single
tenant/model pair and computes diffs against incoming snapshots.
This avoids redundant I/O when the same model is diffed repeatedly
during a batch sync wave.

Usage (within Ray cluster):
    actor = DiffActor.remote(tenant_id="acme", model_id="sales")
    result = ray.get(actor.diff.remote(current_sml_dict, previous_sml_dict))
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import ray
except ImportError:
    ray = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


@dataclass
class DiffResult:
    """Structured output of a single diff operation."""

    added: int = 0
    modified: int = 0
    removed: int = 0
    changes: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added": self.added,
            "modified": self.modified,
            "removed": self.removed,
            "changes": self.changes,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Tree-diff helpers (pure functions)
# ---------------------------------------------------------------------------


def _diff_key_sets(
    old_keys: set,
    new_keys: set,
) -> tuple[set, set, set]:
    """Return (added, removed, common) key sets."""
    return new_keys - old_keys, old_keys - new_keys, old_keys & new_keys


def _shallow_dict_diff(
    old: Dict[str, Any],
    new: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a dict of changed fields between two flat dicts."""
    changes: Dict[str, Any] = {}
    all_keys = set(old) | set(new)
    for k in all_keys:
        ov = old.get(k)
        nv = new.get(k)
        if ov != nv:
            changes[k] = {"old": ov, "new": nv}
    return changes


def _diff_entity_list(
    old_entities: List[Dict[str, Any]],
    new_entities: List[Dict[str, Any]],
    key_field: str = "unique_name",
    entity_type: str = "entity",
) -> tuple[int, int, int, List[Dict[str, Any]]]:
    """
    Compare two lists of entity dicts by a key field.

    Returns: (added, modified, removed, change_records)
    """
    old_map = {e.get(key_field, ""): e for e in old_entities}
    new_map = {e.get(key_field, ""): e for e in new_entities}

    added_keys, removed_keys, common_keys = _diff_key_sets(
        set(old_map), set(new_map)
    )

    records: List[Dict[str, Any]] = []

    for k in sorted(added_keys):
        records.append({
            "entity_type": entity_type,
            "entity_id": k,
            "change_type": "added",
            "after": new_map[k],
        })

    for k in sorted(removed_keys):
        records.append({
            "entity_type": entity_type,
            "entity_id": k,
            "change_type": "removed",
            "before": old_map[k],
        })

    modified = 0
    for k in sorted(common_keys):
        field_changes = _shallow_dict_diff(old_map[k], new_map[k])
        if field_changes:
            modified += 1
            records.append({
                "entity_type": entity_type,
                "entity_id": k,
                "change_type": "modified",
                "before": old_map[k],
                "after": new_map[k],
                "field_changes": field_changes,
            })

    return len(added_keys), modified, len(removed_keys), records


def compute_sml_diff(
    old_sml: Dict[str, Any],
    new_sml: Dict[str, Any],
) -> DiffResult:
    """
    Compute a structural diff between two SML model dicts.

    Goes through each top-level entity list (datasets, relationships,
    metrics, hierarchies, dimensions) and produces a unified change set.
    """
    start = time.monotonic()

    total_added = 0
    total_modified = 0
    total_removed = 0
    all_changes: List[Dict[str, Any]] = []

    entity_sections = [
        ("datasets", "unique_name", "dataset"),
        ("relationships", "unique_name", "relationship"),
        ("metrics", "unique_name", "metric"),
        ("hierarchies", "unique_name", "hierarchy"),
        ("dimensions", "unique_name", "dimension"),
    ]

    for section, key_field, entity_type in entity_sections:
        old_list = old_sml.get(section, [])
        new_list = new_sml.get(section, [])
        if not old_list and not new_list:
            continue
        a, m, r, changes = _diff_entity_list(
            old_list, new_list, key_field, entity_type
        )
        total_added += a
        total_modified += m
        total_removed += r
        all_changes.extend(changes)

    elapsed = int((time.monotonic() - start) * 1000)
    return DiffResult(
        added=total_added,
        modified=total_modified,
        removed=total_removed,
        changes=all_changes,
        duration_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Ray Actor
# ---------------------------------------------------------------------------


def _make_actor_class():
    """Build and return the actor class, applying @ray.remote only when Ray is available."""

    class _DiffActor:
        """
        Stateful Ray actor that caches a baseline SML snapshot in memory
        and computes diffs against incoming current snapshots.

        One actor per tenant/model pair provides locality — repeated diffs
        of the same model avoid re-deserialising the baseline.
        """

        def __init__(self, tenant_id: str, model_id: str) -> None:
            self.tenant_id = tenant_id
            self.model_id = model_id
            self._baseline: Optional[Dict[str, Any]] = None

        def set_baseline(self, sml_dict: Dict[str, Any]) -> None:
            """Cache a baseline snapshot in actor memory."""
            self._baseline = sml_dict

        def get_baseline(self) -> Optional[Dict[str, Any]]:
            return self._baseline

        def diff(
            self,
            current: Dict[str, Any],
            previous: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            base = previous if previous is not None else self._baseline
            if base is None:
                self._baseline = current
                return DiffResult(added=-1, duration_ms=0).to_dict()

            result = compute_sml_diff(base, current)
            self._baseline = current
            return result.to_dict()

    if ray is not None:
        return ray.remote(_DiffActor)
    return _DiffActor


DiffActor = _make_actor_class()
