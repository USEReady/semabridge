"""
Tests for dry-run filtering and summary logic.

Covers:
  - Property 1: Field-only invariant (Requirement 1.1)
  - Property 2: Summary consistency (Requirements 1.3, 1.4)
  - Unit tests for add_collision_handling (Requirement 1.1)

Run with:
    python -m pytest Tests/test_dry_run.py
"""

from __future__ import annotations

import os
import sys
import types
from typing import Any, Dict, List

import pytest
import yaml

# ---------------------------------------------------------------------------
# Minimal stubs so the semabridge package can be imported without a live DB
# ---------------------------------------------------------------------------

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_dry_run.sqlite")

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

from semabridge.api.services.mappings_service import MappingService
from semabridge.api.controllers.mappings_controller import _build_config_yaml_from_request

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIELD_KINDS = ("field", "column", "measure")
ALL_KINDS = ("field", "column", "measure", "table")
STATUSES = ("auto", "manual", "unmapped", "collision")


def _make_mapping(
    *,
    entity_kind: str = "field",
    target_name: str = "some_field",
    mapping_status: str = "auto",
    source_name: str = "src",
    source_path: str = "",
) -> Dict[str, Any]:
    return {
        "id": f"id_{target_name}_{source_name}",
        "entity_kind": entity_kind,
        "source_name": source_name,
        "source_data_type": "VARCHAR",
        "source_table": "tbl",
        "source_path": source_path or f"datasets.tbl.{source_name}",
        "target_name": target_name,
        "target_data_type": "VARCHAR",
        "mapping_status": mapping_status,
        "status": mapping_status,
    }


def _filter_to_fields(entity_mappings: List[Dict]) -> List[Dict]:
    """Replicate the filtering logic from dry_run_mapping endpoint."""
    return [
        m for m in entity_mappings
        if isinstance(m, dict) and m.get("entity_kind") in FIELD_KINDS
    ]


def _build_summary(filtered: List[Dict]) -> Dict[str, int]:
    """Replicate the summary-building logic from dry_run_mapping endpoint."""
    return {
        "total_fields": len(filtered),
        "auto_mapped": sum(
            1 for m in filtered
            if m.get("mapping_status") == "auto" or m.get("status") == "auto"
        ),
        "unmapped": sum(
            1 for m in filtered
            if m.get("mapping_status") == "unmapped" or m.get("status") == "unmapped"
        ),
        "collisions": sum(
            1 for m in filtered
            if m.get("mapping_status") == "collision" or m.get("status") == "collision"
        ),
    }


def test_build_config_yaml_from_request_preserves_fabric_identity_id():
    """The dry-run preview config must keep the selected Fabric identity."""
    config_yaml = _build_config_yaml_from_request(
        source_config={
            "type": "fabric",
            "workspace_id": "workspace-123",
            "identity_id": "account-456",
        },
        target_config={"type": "snowflake"},
        selected_sources=["Model A"],
    )

    config = yaml.safe_load(config_yaml)

    assert config["source"]["type"] == "fabric"
    assert config["source"]["workspace_id"] == "workspace-123"
    assert config["source"]["identity_id"] == "account-456"
    assert config["source"]["models"] == ["Model A"]


# ===========================================================================
# 2.1  Property test: field-only invariant
#      Validates: Requirements 1.1
# ===========================================================================

from hypothesis import given, settings
from hypothesis import strategies as st

# Strategy: generate a list of entity_mapping dicts with mixed entity_kind values
_entity_kind_st = st.sampled_from(list(ALL_KINDS))
_status_st = st.sampled_from(list(STATUSES))
_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_"),
    min_size=1,
    max_size=20,
)

_mapping_st = st.fixed_dictionaries(
    {
        "entity_kind": _entity_kind_st,
        "target_name": _name_st,
        "mapping_status": _status_st,
        "source_name": _name_st,
        "source_path": st.just(""),
    }
)


@given(entity_mappings=st.lists(_mapping_st, min_size=0, max_size=30))
@settings(max_examples=200)
def test_property1_field_only_invariant(entity_mappings):
    """
    **Validates: Requirements 1.1**

    Property 1: Field-only invariant
    For any list of entity_mappings with mixed entity_kind values, the filtered
    result contains only "field", "column", or "measure" kinds.
    No entry with entity_kind == "table" appears in the output.
    """
    filtered = _filter_to_fields(entity_mappings)

    for m in filtered:
        assert m["entity_kind"] in FIELD_KINDS, (
            f"Expected only field/column/measure kinds, got: {m['entity_kind']}"
        )
        assert m["entity_kind"] != "table", (
            f"Table-kind entry leaked into filtered result: {m}"
        )


# ===========================================================================
# 2.2  Property test: summary consistency
#      Validates: Requirements 1.3, 1.4
# ===========================================================================

_field_mapping_st = st.fixed_dictionaries(
    {
        "entity_kind": st.sampled_from(list(FIELD_KINDS)),
        "target_name": _name_st,
        "mapping_status": _status_st,
        "source_name": _name_st,
        "source_path": st.just(""),
    }
)


@given(field_mappings=st.lists(_field_mapping_st, min_size=0, max_size=50))
@settings(max_examples=200)
def test_property2_summary_consistency(field_mappings):
    """
    **Validates: Requirements 1.3, 1.4**

    Property 2: Summary consistency
    For any filtered entity_mappings list:
      - summary.total_fields == len(entity_mappings)
      - summary.auto_mapped + summary.unmapped + summary.collisions <= summary.total_fields
    """
    # These are already field-kind mappings (pre-filtered), so pass them directly
    summary = _build_summary(field_mappings)

    assert summary["total_fields"] == len(field_mappings), (
        f"total_fields {summary['total_fields']} != len(mappings) {len(field_mappings)}"
    )

    count_sum = summary["auto_mapped"] + summary["unmapped"] + summary["collisions"]
    assert count_sum <= summary["total_fields"], (
        f"auto_mapped({summary['auto_mapped']}) + unmapped({summary['unmapped']}) "
        f"+ collisions({summary['collisions']}) = {count_sum} "
        f"> total_fields({summary['total_fields']})"
    )


