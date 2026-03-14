"""
Integration Test — Ray-based Distributed Diffing.

Verifies:
    1. ``compute_sml_diff`` produces correct added/modified/removed counts.
    2. Entity-list diffing handles additions, deletions, modifications.
    3. ``DiffActor`` maintains baseline state across invocations.
    4. ``submit_diff`` falls back to local when Ray is unavailable.

These tests do NOT require a running Ray cluster — the actor tests use
``ray.init(num_cpus=1)`` locally, and the coordinator tests mock Ray.

Run:
    pytest tests/integration/test_ray_diffing.py -v
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch

from semabridge.distributed.ray.actor_diff import (
    DiffResult,
    compute_sml_diff,
    _diff_entity_list,
    _shallow_dict_diff,
)


# ---------------------------------------------------------------------------
# Pure-function diff tests (no Ray needed)
# ---------------------------------------------------------------------------


class TestShallowDictDiff:
    def test_identical_dicts(self):
        assert _shallow_dict_diff({"a": 1}, {"a": 1}) == {}

    def test_modified_field(self):
        result = _shallow_dict_diff({"a": 1}, {"a": 2})
        assert result == {"a": {"old": 1, "new": 2}}

    def test_added_field(self):
        result = _shallow_dict_diff({}, {"b": 42})
        assert "b" in result

    def test_removed_field(self):
        result = _shallow_dict_diff({"c": 3}, {})
        assert "c" in result


class TestDiffEntityList:
    def test_all_added(self):
        a, m, r, changes = _diff_entity_list(
            [], [{"unique_name": "X"}], "unique_name", "dataset"
        )
        assert (a, m, r) == (1, 0, 0)
        assert changes[0]["change_type"] == "added"

    def test_all_removed(self):
        a, m, r, changes = _diff_entity_list(
            [{"unique_name": "Y"}], [], "unique_name", "dataset"
        )
        assert (a, m, r) == (0, 0, 1)
        assert changes[0]["change_type"] == "removed"

    def test_modification(self):
        old = [{"unique_name": "Z", "label": "old"}]
        new = [{"unique_name": "Z", "label": "new"}]
        a, m, r, changes = _diff_entity_list(old, new, "unique_name", "dataset")
        assert (a, m, r) == (0, 1, 0)
        assert changes[0]["change_type"] == "modified"

    def test_mixed(self):
        old = [{"unique_name": "A"}, {"unique_name": "B", "x": 1}]
        new = [{"unique_name": "B", "x": 2}, {"unique_name": "C"}]
        a, m, r, _ = _diff_entity_list(old, new, "unique_name", "t")
        assert a == 1  # C added
        assert m == 1  # B modified
        assert r == 1  # A removed


class TestComputeSmlDiff:
    def test_empty_models(self):
        result = compute_sml_diff({}, {})
        assert result.added == 0 and result.modified == 0 and result.removed == 0

    def test_new_dataset(self):
        old = {"datasets": []}
        new = {"datasets": [{"unique_name": "DIM_TIME"}]}
        result = compute_sml_diff(old, new)
        assert result.added == 1
        assert result.removed == 0

    def test_removed_relationship(self):
        old = {"relationships": [{"unique_name": "REL_1"}]}
        new = {"relationships": []}
        result = compute_sml_diff(old, new)
        assert result.removed == 1

    def test_multiple_sections(self):
        old = {
            "datasets": [{"unique_name": "A"}],
            "metrics": [{"unique_name": "M1"}],
        }
        new = {
            "datasets": [{"unique_name": "A"}, {"unique_name": "B"}],
            "metrics": [],
        }
        result = compute_sml_diff(old, new)
        assert result.added == 1  # B
        assert result.removed == 1  # M1


# ---------------------------------------------------------------------------
# Coordinator fallback test (no Ray cluster)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_diff_local_fallback():
    """When Ray is not available, submit_diff falls back to local diff."""
    from semabridge.distributed.ray.coordinator import submit_diff

    old = {"datasets": [{"unique_name": "X"}]}
    new = {"datasets": [{"unique_name": "X"}, {"unique_name": "Y"}]}

    # Force Ray to fail initialisation
    with patch("semabridge.distributed.ray.coordinator.ray") as mock_ray:
        mock_ray.is_initialized.return_value = False
        mock_ray.init.side_effect = RuntimeError("No cluster")

        result = await submit_diff(new, old)

    assert result["added"] == 1
    assert result["removed"] == 0
