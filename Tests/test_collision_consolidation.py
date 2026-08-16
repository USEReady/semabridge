"""
Tests for the consolidated collision-detection path.

Prior to this consolidation, dry-run (build_entity_mappings +
_compat_serialize_auto_map_entity_mappings) and deploy-time DDL emission
(SemanticViewBuilder in ddl_builder.py) computed collision-suffix hashes with
different algorithms/seeds and Layer 2 re-derived collisions independently of
Layer 1 -- so a name shown in the dry-run UI was not guaranteed to match what
actually got deployed. These tests guard the fix:
  - the dry-run hash helper agrees with the deploy-time hash helper
  - the serializer no longer disagrees with build_entity_mappings' verdict
  - the persisted duplicate-name repository's read-only preview never
    diverges from what get_or_create_assigned_name would persist

Run with:
    python -m pytest Tests/test_collision_consolidation.py
"""

from __future__ import annotations

import os
import sys
import types

import pytest

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_collision_consolidation.sqlite")

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.api.services.project_mapping_engine import (
    build_entity_mappings,
    collision_suggestion_hash,
)
from semabridge.api.services.mapping_service import _compat_serialize_auto_map_entity_mappings
from semabridge.connectors.ddl_builder import SemanticViewBuilder
from semabridge.repository.duplicate_name_mapping_repository import DuplicateNameMappingRepository


# ---------------------------------------------------------------------------
# Hash-algorithm parity between dry-run and deploy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entity_name,field_name",
    [
        ("STORE", "REVENUE"),
        ("Web", "Revenue Total"),
        ("Customers", "ID"),
        ("", "unnamed"),
    ],
)
def test_collision_suggestion_hash_matches_ddl_builder(entity_name, field_name):
    """Layer 1's suggestion hash must equal Layer 4's deploy-time hash for the
    same (entity_name, field_name) seed -- same algorithm, same format."""
    assert collision_suggestion_hash(entity_name, field_name) == (
        SemanticViewBuilder._generate_deterministic_hash(entity_name, field_name)
    )


# ---------------------------------------------------------------------------
# Layer 1 -> Layer 2 agreement (no independent re-derivation)
# ---------------------------------------------------------------------------

def _collision_model() -> dict:
    """Two tables ("Store", "Web") each with a "Revenue" column -- Snowflake's
    flat DIMENSIONS namespace makes this a real cross-table collision."""
    return {
        "unique_name": "CollisionModel",
        "datasets": [
            {
                "unique_name": "Store",
                "columns": [
                    {"name": "Revenue", "data_type": "decimal"},
                    {"name": "Name", "data_type": "string"},
                ],
            },
            {
                "unique_name": "Web",
                "columns": [
                    {"name": "Revenue", "data_type": "decimal"},
                ],
            },
        ],
        "metrics": [],
    }


def _built_and_serialized():
    built = build_entity_mappings(
        project_id="test-collision-project",
        model=_collision_model(),
        existing_mappings={},
        session_key="test-session",
        target_connector="snowflake",
    )
    serialized = _compat_serialize_auto_map_entity_mappings(built["mappings"])
    return built, serialized


def test_layer2_does_not_reintroduce_a_second_verdict():
    """The serializer must reflect exactly the collision rows Layer 1 flagged
    -- no more, no fewer -- since it no longer recomputes collisions itself."""
    built, serialized = _built_and_serialized()

    built_collisions = {
        row["source_path"] for row in built["mappings"] if row.get("collision_detected")
    }
    serialized_collisions = {
        row["source_path"] for row in serialized if row.get("collision_detected")
    }

    assert built_collisions, "fixture should contain at least one real collision"
    assert serialized_collisions == built_collisions


def test_layer2_preserves_layer1_resolution_suggestions():
    """The suggestion list shown to the user must be Layer 1's own list, not
    a second, differently-seeded guess computed inside the serializer."""
    built, serialized = _built_and_serialized()

    by_path_built = {row["source_path"]: row for row in built["mappings"]}
    for row in serialized:
        if not row.get("collision_detected"):
            continue
        source_row = by_path_built[row["source_path"]]
        assert row["resolution_suggestions"] == list(source_row.get("resolution_suggestions") or [])


# ---------------------------------------------------------------------------
# Persisted repository: preview never diverges from the real assignment
# ---------------------------------------------------------------------------

@pytest.fixture()
def dup_repo(tmp_path, monkeypatch):
    db_path = tmp_path / "collision_repo_test.sqlite"
    monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", f"sqlite:///{db_path}")
    return DuplicateNameMappingRepository()


def test_peek_matches_get_or_create_for_a_fresh_name(dup_repo):
    """Before anything is persisted, peek_assigned_name's prediction for a
    brand-new entity must equal what get_or_create_assigned_name would
    actually persist -- the common case (first dry-run before any deploy)."""
    kwargs = dict(
        scope_type="column",
        namespace_key="DB.SCHEMA",
        dataset_key="STORE",
        normalized_base="REVENUE",
        source_signature="Revenue|expr|decimal|",
    )
    predicted = dup_repo.peek_assigned_name(preferred_name="REVENUE_ABCD", **kwargs)
    assert predicted == "REVENUE_ABCD"

    actual = dup_repo.get_or_create_assigned_name(
        source_name="Revenue", preferred_name="REVENUE_ABCD", **kwargs
    )
    assert actual == predicted


def test_peek_reflects_already_persisted_assignment(dup_repo):
    """Once a name is persisted (e.g. from a prior deploy), a later dry-run's
    preview for the SAME entity must return that already-assigned name
    unchanged -- never a fresh, differently-hashed guess."""
    kwargs = dict(
        scope_type="column",
        namespace_key="DB.SCHEMA",
        dataset_key="STORE",
        normalized_base="REVENUE",
        source_signature="Revenue|expr|decimal|",
    )
    first = dup_repo.get_or_create_assigned_name(
        source_name="Revenue", preferred_name="REVENUE_ABCD", **kwargs
    )

    # A later dry-run computes a *different* preferred_name (e.g. the hash
    # seed drifted) -- the persisted assignment must still win.
    predicted = dup_repo.peek_assigned_name(preferred_name="REVENUE_ZZZZ", **kwargs)
    assert predicted == first == "REVENUE_ABCD"


def test_peek_never_writes_to_the_repository(dup_repo):
    """peek_assigned_name is read-only: calling it for a brand-new entity must
    not create a row that a later get_or_create call for a *different*,
    colliding entity would then have to avoid."""
    namespace = dict(scope_type="column", namespace_key="DB.SCHEMA", dataset_key="STORE")

    dup_repo.peek_assigned_name(
        normalized_base="REVENUE",
        source_signature="sig-a",
        preferred_name="REVENUE_ABCD",
        **namespace,
    )

    # If peek had persisted anything, this fresh get_or_create for the SAME
    # preferred_name but a DIFFERENT signature would collide against it.
    assigned = dup_repo.get_or_create_assigned_name(
        normalized_base="REVENUE",
        source_name="Revenue",
        source_signature="sig-b",
        preferred_name="REVENUE_ABCD",
        **namespace,
    )
    assert assigned == "REVENUE_ABCD"
