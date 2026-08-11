"""Regression tests for the dry-run "predicted_failure" status fix.

Closes two independently-discovered false-positive gaps in the dry-run
dropped-fields report (all synthetic/placeholder data, no real project
names):

  1. A metric flagged by dax_calculate_filters_unreachable_dimension (the
     KPI01/KPI02-style "unreachable dimension" advisory, error 010211) was
     detected correctly but never surfaced past `advisory_notes` -- no
     consumer read it, so the metric still landed in entity_mappings with
     status "auto" and got folded into auto_count, indistinguishable from
     a genuinely healthy metric. Fixed by mirroring the detector's finding
     onto a structural `advisory_categories` code and having
     project_mapping_engine.py key its status computation on that code.

  2. entity_mappings (naming/collision status, computed before any
     DDL-emission/schema-validation pass runs) and dropped_entities (built
     by a later, independent pass -- see mappings_controller.py's trial
     DDL-build pass) could silently disagree about the same field: "auto"
     in one, dropped in the other. Fixed by reconcile_predicted_failures_
     with_drops(), which cross-checks by structural identity (entity_kind
     + dataset + name), not by field name.

Both fixes converge on the same status value, STATUS_PREDICTED_FAILURE
("predicted_failure"), so a client-facing consumer of either signal sees
one unambiguous "this will likely fail at real deploy" marker instead of
two independently-computed views of "did this succeed" that can disagree.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.api.services.project_mapping_engine import (
    STATUS_PREDICTED_FAILURE,
    build_entity_mappings,
    reconcile_predicted_failures_with_drops,
)
from semabridge.converter.dax_ast_parser import (
    ADVISORY_CATEGORY_UNREACHABLE_DIMENSION,
    dax_calculate_filters_unreachable_dimension,
)
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIAggregationType,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)


# ---------------------------------------------------------------------------
# Fix 1: advisory_categories -> "predicted_failure" status, not "auto"
# ---------------------------------------------------------------------------

def _synthetic_model_with_flagged_metric() -> dict:
    """A model dict shaped like an SML snapshot blob (what
    build_entity_mappings actually receives): one metric carrying the
    unreachable-dimension advisory + its structural category, one metric
    with no advisory at all -- proves the fix is selective, not a blanket
    "any metric" downgrade."""
    return {
        "unique_name": "SyntheticModel",
        "datasets": [
            {
                "unique_name": "SelectorDim",
                "columns": [{"unique_name": "Choice", "data_type": "int"}],
            },
        ],
        "metrics": [
            {
                "unique_name": "RiskyMetric",
                "dataset": "SelectorDim",
                "expression": "CALCULATE([Volume],'Other'[Year]=1)",
                "sync_enabled": True,
                "sync_failure_reason": None,
                "advisory_notes": [
                    "This metric's CALCULATE(...) filters by table 'Other', but "
                    "'SelectorDim' has no relationship path to 'Other'."
                ],
                "advisory_categories": [ADVISORY_CATEGORY_UNREACHABLE_DIMENSION],
            },
            {
                "unique_name": "HealthyMetric",
                "dataset": "SelectorDim",
                "expression": "SUM([Choice])",
                "sync_enabled": True,
                "sync_failure_reason": None,
                "advisory_notes": [],
                "advisory_categories": [],
            },
        ],
    }


def test_advisory_category_flags_metric_as_predicted_failure_not_auto():
    model = _synthetic_model_with_flagged_metric()

    built = build_entity_mappings(
        project_id="synthetic-proj",
        model=model,
        existing_mappings={},
        session_key="synthetic-session",
        target_connector="snowflake",
    )

    rows_by_name = {row["source_name"]: row for row in built["mappings"]}
    risky = rows_by_name["RiskyMetric"]
    healthy = rows_by_name["HealthyMetric"]

    assert risky["status"] == STATUS_PREDICTED_FAILURE
    assert risky["status"] != "auto"
    assert healthy["status"] == "auto"

    # The exact ask: this metric must not be countable as a success.
    metric_rows = [row for row in built["mappings"] if row["entity_kind"] == "metric"]
    auto_metric_count = sum(1 for row in metric_rows if row["status"] == "auto")
    assert auto_metric_count == 1  # only HealthyMetric
    predicted_failure_count = sum(
        1 for row in metric_rows if row["status"] == STATUS_PREDICTED_FAILURE
    )
    assert predicted_failure_count == 1  # only RiskyMetric


def test_predicted_failure_status_overrides_manual_naming_override_too():
    """A user manually renaming the target identifier doesn't change
    whether Snowflake's compiler will accept the metric's expression --
    predicted_failure must win over "manual" too, not just "auto"."""
    model = _synthetic_model_with_flagged_metric()

    built = build_entity_mappings(
        project_id="synthetic-proj",
        model=model,
        existing_mappings={
            "metrics.RiskyMetric": {"target_name": "RISKY_RENAMED", "is_user_edited": True},
        },
        session_key="synthetic-session",
        target_connector="snowflake",
    )

    risky = next(row for row in built["mappings"] if row["source_name"] == "RiskyMetric")
    assert risky["status"] == STATUS_PREDICTED_FAILURE
    assert risky["target_name"] == "RISKY_RENAMED"  # the override itself still applied


def test_unreachable_dimension_detector_stamps_the_shared_category_constant():
    """The detector's output category must be the same constant
    project_mapping_engine.py keys its status computation on -- proves the
    two sides of the wiring can't silently drift apart."""
    # build_relationship_edges accepts any relationship-like object exposing
    # is_active/from_dataset/to_dataset/from_columns/to_columns as
    # attributes (see utils/relationship_graph.py) -- a SimpleNamespace is
    # the minimal stand-in, deliberately not a dict.
    relationships = [
        SimpleNamespace(
            is_active=True,
            from_dataset="Fact",
            from_columns=["DimId"],
            to_dataset="Dim",
            to_columns=["DimId"],
        )
    ]
    hit = dax_calculate_filters_unreachable_dimension(
        "CALCULATE([Total],'Other'[Year]=1)",
        "Fact",
        relationships,
    )
    assert hit is not None
    assert hit.category == ADVISORY_CATEGORY_UNREACHABLE_DIMENSION


def test_osi_to_sml_wires_advisory_category_onto_the_metric():
    """End-to-end through the real conversion path (not a hand-built
    dict): OSIToSMLConverter must populate advisory_categories in lockstep
    with advisory_notes for a metric matching the unreachable-dimension
    shape, and leave a reachable metric's advisory_categories empty."""
    osi_model = OSIModel(
        unique_name="synthetic-model",
        label="Synthetic Model",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="Selector",
                columns=[OSIColumn(unique_name="Choice", data_type=OSIDataType.INTEGER)],
            ),
            OSIDataset(
                unique_name="Fact",
                columns=[
                    OSIColumn(unique_name="Amount", data_type=OSIDataType.FLOAT),
                    OSIColumn(unique_name="DimId", data_type=OSIDataType.INTEGER),
                ],
            ),
            OSIDataset(
                unique_name="Dim",
                columns=[
                    OSIColumn(unique_name="DimId", data_type=OSIDataType.INTEGER),
                    OSIColumn(unique_name="Year", data_type=OSIDataType.INTEGER),
                ],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="TotalVolume",
                label="Total Volume",
                dataset="Fact",
                expression="SUM([Amount])",
                aggregation=OSIAggregationType.NONE,
            ),
            OSIMetric(
                unique_name="MyKpi",
                label="My Kpi",
                dataset="Selector",
                expression="CALCULATE([TotalVolume],'Dim'[Year]=1)",
                aggregation=OSIAggregationType.NONE,
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="REL_FACT_DIMID__DIM_DIMID",
                from_dataset="Fact",
                from_columns=["DimId"],
                to_dataset="Dim",
                to_columns=["DimId"],
            )
        ],
    )

    sml_model = OSIToSMLConverter().from_osi(osi_model)
    my_kpi = next(m for m in sml_model.metrics if m.unique_name == "MyKpi")
    total_volume = next(m for m in sml_model.metrics if m.unique_name == "TotalVolume")

    assert my_kpi.advisory_categories == [ADVISORY_CATEGORY_UNREACHABLE_DIMENSION]
    assert len(my_kpi.advisory_categories) == len(my_kpi.advisory_notes)
    assert total_volume.advisory_categories == []


# ---------------------------------------------------------------------------
# Fix 2: dropped_entities reconciliation -> "predicted_failure", not "auto"
# ---------------------------------------------------------------------------

def test_field_in_dropped_entities_is_not_left_as_auto_in_entity_mappings():
    """The exact shape of the MonthIndex-style gap: a column entity_mappings
    computed as a clean "auto" mapping (naming succeeded), while a LATER,
    independent pass (the trial DDL-build pass, standing in here as a
    hand-built drop record) drops the same column for schema-validation
    reasons. After reconciliation the two views must agree."""
    mappings = [
        {
            "entity_kind": "column",
            "source_name": "LiveSchemaOnlyCol",
            "parent_source_path": "datasets.SelectorDim",
            "status": "auto",
        },
        {
            "entity_kind": "column",
            "source_name": "UnaffectedCol",
            "parent_source_path": "datasets.SelectorDim",
            "status": "auto",
        },
    ]
    dropped_entities = [
        {
            "entity_kind": "column",
            "entity_name": "LiveSchemaOnlyCol",
            "dataset": "SelectorDim",
            "stage": "schema_validation",
            "reason": "no live schema was available for dataset 'SelectorDim'",
            "by_design": False,
        },
    ]

    reconcile_predicted_failures_with_drops(mappings, dropped_entities)

    dropped_row = next(m for m in mappings if m["source_name"] == "LiveSchemaOnlyCol")
    unaffected_row = next(m for m in mappings if m["source_name"] == "UnaffectedCol")
    assert dropped_row["status"] == STATUS_PREDICTED_FAILURE
    assert unaffected_row["status"] == "auto"


def test_reconciliation_is_scoped_by_dataset_not_just_name():
    """The same column name can legitimately exist on two different
    datasets with different live-schema outcomes -- the match must not
    blindly fire on name alone when the drop record carries a dataset."""
    mappings = [
        {
            "entity_kind": "column",
            "source_name": "MonthIndex",
            "parent_source_path": "datasets.DimA",
            "status": "auto",
        },
        {
            "entity_kind": "column",
            "source_name": "MonthIndex",
            "parent_source_path": "datasets.DimB",
            "status": "auto",
        },
    ]
    dropped_entities = [
        {
            "entity_kind": "column",
            "entity_name": "MonthIndex",
            "dataset": "DimA",
            "stage": "schema_validation",
            "reason": "no live schema was available for dataset 'DimA'",
            "by_design": False,
        },
    ]

    reconcile_predicted_failures_with_drops(mappings, dropped_entities)

    dim_a_row = next(m for m in mappings if m["parent_source_path"] == "datasets.DimA")
    dim_b_row = next(m for m in mappings if m["parent_source_path"] == "datasets.DimB")
    assert dim_a_row["status"] == STATUS_PREDICTED_FAILURE
    assert dim_b_row["status"] == "auto"


def test_by_design_drops_are_not_treated_as_predicted_failures():
    """by_design=True drops (e.g. Power BI's own auto-generated shadow
    tables) are intentional exclusions, not failures -- reconciliation
    must leave them alone rather than alarming on them."""
    mappings = [
        {
            "entity_kind": "column",
            "source_name": "ShadowCol",
            "parent_source_path": "datasets.SelectorDim",
            "status": "auto",
        },
    ]
    dropped_entities = [
        {
            "entity_kind": "column",
            "entity_name": "ShadowCol",
            "dataset": "SelectorDim",
            "stage": "extraction",
            "reason": "intentional by-design shadow exclusion",
            "by_design": True,
        },
    ]

    reconcile_predicted_failures_with_drops(mappings, dropped_entities)

    assert mappings[0]["status"] == "auto"


def test_reconciliation_end_to_end_with_build_entity_mappings_output():
    """Mirrors mappings_controller.py's real dry-run flow: build_entity_
    mappings() produces the naming/collision view first (a column mapped
    cleanly as "auto"), and only afterwards does the trial DDL-build
    pass's independent drop_ledger surface the same column as dropped --
    exactly the two-independently-computed-views bug shape."""
    model = {
        "unique_name": "SyntheticModel",
        "datasets": [
            {
                "unique_name": "SelectorDim",
                "columns": [{"unique_name": "LiveSchemaOnlyCol", "data_type": "int"}],
            },
        ],
        "metrics": [],
    }

    built = build_entity_mappings(
        project_id="synthetic-proj",
        model=model,
        existing_mappings={},
        session_key="synthetic-session",
        target_connector="snowflake",
    )
    col_row = next(row for row in built["mappings"] if row["source_name"] == "LiveSchemaOnlyCol")
    assert col_row["status"] == "auto"  # precondition: naming pass alone sees no problem

    trial_pass_drops = [
        {
            "entity_kind": "column",
            "entity_name": "LiveSchemaOnlyCol",
            "dataset": "SelectorDim",
            "stage": "schema_validation",
            "reason": "no live schema was available for dataset 'SelectorDim'",
            "by_design": False,
        },
    ]
    reconcile_predicted_failures_with_drops(built["mappings"], trial_pass_drops)

    col_row_after = next(row for row in built["mappings"] if row["source_name"] == "LiveSchemaOnlyCol")
    assert col_row_after["status"] == STATUS_PREDICTED_FAILURE
