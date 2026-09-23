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

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake", auto_resolve_collisions=True)
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
        project_id="p1", model=model, existing_mappings=existing_mappings, target_connector="snowflake",
        auto_resolve_collisions=True,
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

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake", auto_resolve_collisions=True)
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

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake", auto_resolve_collisions=True)
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

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake", auto_resolve_collisions=True)
    columns = [row for row in payload["mappings"] if row.get("entity_kind") == "column"]

    for col in columns:
        assert col["collision_detected"] is False
        assert col["collision_group"] == ""
        assert col["validation_status"] == "valid"
        assert col["validation_code"] != "NAME_COLLISION"


def test_renamed_row_carries_a_structured_peer_reference_to_the_primary():
    # collision_group is cleared to "" on resolution, and validation_message
    # only embeds the peer's name as free text -- collision_peer_source_name/
    # collision_peer_table_name are the structured fields a UI can actually
    # read to answer "which other row did this collide with", for the
    # DryRunMappingTable.jsx auto-resolved detail panel.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Region", "columns": [{"unique_name": "Code", "data_type": "string"}]},
            {"unique_name": "Sales_Fact", "columns": [{"unique_name": "Code", "data_type": "string"}]},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake", auto_resolve_collisions=True)
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    col_region = cols["datasets.Region.columns.Code"]
    assert col_region["collision_peer_source_name"] == "Code"
    assert col_region["collision_peer_table_name"] == "SALES_FACT"


def test_primary_row_carries_reason_and_the_list_of_siblings_it_caused_to_rename():
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Region", "columns": [{"unique_name": "Code", "data_type": "string"}]},
            {"unique_name": "Sales_Fact", "columns": [{"unique_name": "Code", "data_type": "string"}]},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake", auto_resolve_collisions=True)
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    col_fact = cols["datasets.Sales_Fact.columns.Code"]
    assert col_fact["collision_primary_reason"] == "fact_table"
    assert col_fact["collision_resolved_siblings"] == [
        {"source_name": "Code", "table_name": "REGION", "resolved_target_name": "REGION_CODE"}
    ]
    # The renamed row itself carries no siblings list -- only the primary does.
    assert cols["datasets.Region.columns.Code"].get("collision_resolved_siblings") is None


def test_primary_reason_reflects_user_edited_and_first_encountered_branches():
    # user_edited: forced primary by an explicit user choice, not the
    # fact-table heuristic.
    model_user_edited = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Product", "columns": [{"unique_name": "Code", "data_type": "string"}]},
            {"unique_name": "Region", "columns": [{"unique_name": "Code", "data_type": "string"}]},
        ],
    }
    existing_mappings = {
        "datasets.Region.columns.Code": {"id": "m1", "target_name": "CODE", "is_user_edited": True},
    }
    payload = build_entity_mappings(
        project_id="p1", model=model_user_edited, existing_mappings=existing_mappings, target_connector="snowflake",
        auto_resolve_collisions=True,
    )
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}
    assert cols["datasets.Region.columns.Code"]["collision_primary_reason"] == "user_edited"

    # first_encountered: neither table is fact-like, so the tie-break
    # fallback (not the fact-table heuristic) decided the primary.
    model_no_fact = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Alpha", "columns": [{"unique_name": "Ref", "data_type": "string"}]},
            {"unique_name": "Beta", "columns": [{"unique_name": "Ref", "data_type": "string"}]},
        ],
    }
    payload2 = build_entity_mappings(project_id="p1", model=model_no_fact, target_connector="snowflake", auto_resolve_collisions=True)
    cols2 = {row["source_path"]: row for row in payload2["mappings"] if row.get("entity_kind") == "column"}
    assert cols2["datasets.Alpha.columns.Ref"]["collision_primary_reason"] == "first_encountered"


def test_three_way_collision_primary_row_lists_every_sibling_correctly():
    # Regression guard for the exact ambiguity the standalone `collisions`
    # list has (pairs every group member against indices[0], not
    # necessarily primary_idx) -- collision_resolved_siblings is built
    # directly off primary_idx/rename_indices instead, so it must list
    # BOTH renamed siblings correctly even when the fact-like table isn't
    # first in declaration order.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "KPI", "columns": [{"unique_name": "Category", "data_type": "string"}]},
            {"unique_name": "Product", "columns": [{"unique_name": "Category", "data_type": "string"}]},
            {"unique_name": "Sales_Fact", "columns": [{"unique_name": "Category", "data_type": "string"}]},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake", auto_resolve_collisions=True)
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    col_fact = cols["datasets.Sales_Fact.columns.Category"]
    assert col_fact["target_name"] == "CATEGORY"
    assert col_fact["collision_primary_reason"] == "fact_table"

    siblings_by_table = {s["table_name"]: s for s in col_fact["collision_resolved_siblings"]}
    assert siblings_by_table["KPI"]["resolved_target_name"] == "KPI_CATEGORY"
    assert siblings_by_table["PRODUCT"]["resolved_target_name"] == "PRODUCT_CATEGORY"

    assert cols["datasets.KPI.columns.Category"]["collision_peer_source_name"] == "Category"
    assert cols["datasets.KPI.columns.Category"]["collision_peer_table_name"] == "SALES_FACT"
    assert cols["datasets.Product.columns.Category"]["collision_peer_table_name"] == "SALES_FACT"


def test_two_independent_calls_never_see_each_others_colliding_field():
    # DOCUMENTED, INTENTIONAL invariant -- not a bug someone should
    # "discover" and try to fix later. Multi-file dispatch (both the
    # dry-run background-job path and the real sync pipeline) calls
    # build_entity_mappings() once per file, each with its own independent
    # model -- see mappings_controller.py::_run_dry_run_pipeline's own
    # docstring: "no merging requirement... structurally", and
    # snapshot_service.py::_compat_latest_sml_state, which returns exactly
    # ONE snapshot's model for a project, never a merge of several. Two
    # files that each happen to declare a table/column with the same name
    # (as if two separately-uploaded .pbix files both had a "Category"
    # table) must never be treated as colliding with EACH OTHER, because
    # build_entity_mappings() has no cross-call state and is never handed
    # a merged model. This guards against a future change (memoization,
    # a cache keyed too loosely, or "helpfully" merging models across
    # files before calling this function) silently breaking that contract.
    same_project_id = "proj-multi-file-demo"
    model_file_a = {
        "unique_name": "File_A_Model",
        "datasets": [{"unique_name": "Category", "columns": [{"unique_name": "Category", "data_type": "string"}]}],
    }
    model_file_b = {
        "unique_name": "File_B_Model",
        "datasets": [{"unique_name": "Category", "columns": [{"unique_name": "Category", "data_type": "string"}]}],
    }

    # Same project_id on both calls, matching how two files in one
    # multi-file project are actually dispatched (own model, shared
    # project_id) -- if any cross-call leakage existed, this is where
    # it would show up.
    payload_a = build_entity_mappings(project_id=same_project_id, model=model_file_a, target_connector="snowflake")
    payload_b = build_entity_mappings(project_id=same_project_id, model=model_file_b, target_connector="snowflake")

    col_a = next(row for row in payload_a["mappings"] if row.get("entity_kind") == "column")
    col_b = next(row for row in payload_b["mappings"] if row.get("entity_kind") == "column")

    # Both come back exactly as if each were the only file in the
    # project: plain target name, no collision, no auto-resolve, no peer
    # reference to the other call at all.
    for col in (col_a, col_b):
        assert col["target_name"] == "CATEGORY"
        assert col["collision_detected"] is False
        assert not col.get("collision_auto_resolved")
        assert col.get("collision_peer_source_name") is None
        assert col.get("collision_peer_table_name") is None
        assert col.get("collision_resolved_siblings") is None
        assert col.get("collision_primary_reason") is None
