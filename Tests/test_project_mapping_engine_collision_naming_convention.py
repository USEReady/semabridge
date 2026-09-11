"""Regression tests for the standing collision-naming convention:

    On a field-name collision during dry run:
    - The field from the primary/base source table keeps its name unrenamed.
    - Every other colliding field is renamed <SourceTableName>_<FieldName>.
    - This is applied automatically -- never a per-side prompt.

"Primary/base source table" = whichever colliding table looks fact-like
by name (is_fact_like_name / FACT_KEYWORDS in fact_table_naming.py -- the
real `is_fact` flag isn't populated until Stage 6, too late for dry-run).
With zero or more than one fact-like table in a collision group, the
first-encountered entry (stable iteration order) is used instead, so the
behavior stays fully deterministic.

Manually-edited (is_user_edited) rows are never auto-renamed -- an
explicit user choice is never silently overridden.
"""
from semabridge.api.services.project_mapping_engine import build_entity_mappings


def test_fact_like_table_wins_as_primary_even_when_listed_second():
    # Sales_Fact is fact-like by name; Region is not. Sales_Fact should
    # keep the bare name even though it's declared after Region.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Region", "columns": [{"unique_name": "Code", "data_type": "string"}]},
            {"unique_name": "Sales_Fact", "columns": [{"unique_name": "Code", "data_type": "string"}]},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    col_region = cols["datasets.Region.columns.Code"]
    col_fact = cols["datasets.Sales_Fact.columns.Code"]

    assert col_fact["target_name"] == "CODE"
    assert col_fact["collision_detected"] is False
    assert col_fact["validation_status"] == "valid"

    assert col_region["target_name"] == "REGION_CODE"
    assert col_region["collision_detected"] is False
    assert col_region["collision_auto_resolved"] is True
    assert col_region["validation_status"] == "valid"


def test_user_edited_field_is_never_auto_renamed_even_when_colliding():
    # A user-edited row is never renamed itself, but it IS treated as the
    # forced primary once it's present in a collision group -- the user's
    # explicit choice outranks the fact-table heuristic, and the other
    # (auto) side is renamed away from it, even though it's the only
    # auto side in the group.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Product", "columns": [{"unique_name": "Code", "data_type": "string"}]},
            {"unique_name": "Region", "columns": [{"unique_name": "Code", "data_type": "string"}]},
        ],
    }
    existing_mappings = {
        # User manually pinned Region.Code to stay "CODE" -- an explicit
        # choice that must never be silently overridden by auto-resolve.
        "datasets.Region.columns.Code": {"id": "m1", "target_name": "CODE", "is_user_edited": True},
    }

    payload = build_entity_mappings(
        project_id="p1", model=model, existing_mappings=existing_mappings, target_connector="snowflake"
    )
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    col_region = cols["datasets.Region.columns.Code"]
    col_product = cols["datasets.Product.columns.Code"]

    # The user-edited row is untouched (not renamed) and no longer
    # flagged, now that the other side has moved out of its way.
    assert col_region["target_name"] == "CODE"
    assert col_region["is_user_edited"] is True
    assert col_region["collision_detected"] is False

    # The auto side is renamed away from the user's pinned name instead.
    assert col_product["target_name"] == "PRODUCT_CODE"
    assert col_product["collision_detected"] is False
    assert col_product["collision_auto_resolved"] is True


def test_no_fact_like_table_falls_back_to_first_encountered_deterministically():
    # Neither Alpha nor Beta is fact-like -- primary must be whichever is
    # declared first (Alpha), deterministically, not arbitrary.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Alpha", "columns": [{"unique_name": "Ref", "data_type": "string"}]},
            {"unique_name": "Beta", "columns": [{"unique_name": "Ref", "data_type": "string"}]},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    assert cols["datasets.Alpha.columns.Ref"]["target_name"] == "REF"
    assert cols["datasets.Alpha.columns.Ref"]["collision_detected"] is False
    assert cols["datasets.Beta.columns.Ref"]["target_name"] == "BETA_REF"
    assert cols["datasets.Beta.columns.Ref"]["collision_auto_resolved"] is True


def test_multiple_fact_like_tables_falls_back_to_first_encountered_deterministically():
    # Both tables are fact-like by name -- the heuristic can't pick a
    # single winner, so it must fall back to first-encountered rather
    # than pick arbitrarily or leave both unresolved.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Sales_Fact", "columns": [{"unique_name": "Ref", "data_type": "string"}]},
            {"unique_name": "Transaction_Fact", "columns": [{"unique_name": "Ref", "data_type": "string"}]},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    assert cols["datasets.Sales_Fact.columns.Ref"]["target_name"] == "REF"
    assert cols["datasets.Sales_Fact.columns.Ref"]["collision_detected"] is False
    assert cols["datasets.Transaction_Fact.columns.Ref"]["target_name"] == "TRANSACTION_FACT_REF"
    assert cols["datasets.Transaction_Fact.columns.Ref"]["collision_auto_resolved"] is True


def test_no_collision_flag_or_prompt_lingers_once_auto_resolved():
    # Once auto-resolved, nothing about either row should still say
    # "collision" -- no lingering flag, no NAME_COLLISION code, no
    # per-side action required from the user.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Product", "columns": [{"unique_name": "Manufacturer", "data_type": "string"}]},
            {"unique_name": "Customer", "columns": [{"unique_name": "Manufacturer", "data_type": "string"}]},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    columns = [row for row in payload["mappings"] if row.get("entity_kind") == "column"]

    for col in columns:
        assert col["collision_detected"] is False
        assert col["collision_group"] == ""
        assert col["validation_status"] == "valid"
        assert col["validation_code"] != "NAME_COLLISION"
