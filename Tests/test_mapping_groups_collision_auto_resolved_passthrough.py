from __future__ import annotations

from semabridge.api.services.mapping_service import _compat_format_mapping_groups


def test_format_mapping_groups_passes_through_collision_auto_resolved_for_table_and_column():
    """build_entity_mappings() (project_mapping_engine.py) sets
    collision_auto_resolved=True on a row whose naming collision was
    silently auto-renamed -- but _compat_format_mapping_groups builds a
    fresh whitelisted dict per row for the /api/mappings response (used by
    ProjectConfigPage.jsx's Saved Mappings panel), and collision_detected
    was the only collision-related key on that whitelist. Without also
    passing collision_auto_resolved through here, the new AUTO-RESOLVED
    badge added to ProjectConfigPage.jsx would never have anything to key
    off of -- this is the wiring that makes it possible.
    """
    payload = {
        "mappings": [
            {
                "entity_kind": "table",
                "source_path": "datasets.Product",
                "source_name": "Product",
                "target_name": "PRODUCT",
                "collision_detected": False,
                "collision_auto_resolved": False,
            },
            {
                "entity_kind": "column",
                "parent_source_path": "datasets.Product",
                "source_path": "datasets.Product.columns.Category",
                "source_name": "Category",
                "target_name": "PRODUCT_CATEGORY",
                "collision_detected": False,
                "collision_auto_resolved": True,
            },
        ]
    }

    grouped = _compat_format_mapping_groups(payload)

    assert len(grouped) == 1
    table_row = grouped[0]
    assert table_row["collision_auto_resolved"] is False

    assert len(table_row["columns"]) == 1
    column_row = table_row["columns"][0]
    assert column_row["collision_auto_resolved"] is True


def test_format_mapping_groups_defaults_collision_auto_resolved_to_false_when_absent():
    """A row that never went through the collision path at all (the
    common case) has no collision_auto_resolved key -- must default to
    False, not None/missing, so the frontend's `mapping.collision_auto_resolved`
    check is a clean boolean rather than undefined.
    """
    payload = {
        "mappings": [
            {
                "entity_kind": "table",
                "source_path": "datasets.Date",
                "source_name": "Date",
                "target_name": "DATE",
            },
            {
                "entity_kind": "column",
                "parent_source_path": "datasets.Date",
                "source_path": "datasets.Date.columns.Year",
                "source_name": "Year",
                "target_name": "YEAR",
            },
        ]
    }

    grouped = _compat_format_mapping_groups(payload)

    assert grouped[0]["collision_auto_resolved"] is False
    assert grouped[0]["columns"][0]["collision_auto_resolved"] is False
