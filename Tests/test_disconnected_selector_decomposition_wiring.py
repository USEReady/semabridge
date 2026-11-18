"""Regression test for wiring
dax_ast_parser.try_decompose_disconnected_selector_metric into
osi_to_sml.py's _decompose_disconnected_selector_metrics -- the real
resolution path for a metric declared on a disconnected selector/parameter
table (e.g. a Power BI field-parameter/what-if table with no real business
key) whose DAX switches between other tables' aggregates based on the
selector's own value.

NOTE: commit 44766b0 deliberately disabled this wiring (selector KPI
metrics are kept single as built), so the main test below now pins THAT
behaviour; the original intent is kept here for history.

Originally proved:
  1. Each non-trivial branch becomes its own standalone metric, translated
     and deployable (sync_enabled=True, real sql_expression), anchored on
     the table its own expression actually needs.
  2. The original metric is left otherwise unchanged (still can't be one
     metric) but gets an advisory pointing at its new companions, tagged
     with a category that maps to needs_review (not predicted_failure).
  3. The now-redundant, less-specific unreachable-dimension advisory does
     NOT also fire on the original metric -- decomposition takes priority.
  4. A metric that doesn't match this shape at all is completely
     unaffected (no new metrics, no advisory change) -- proving this is
     additive and narrowly scoped.

All placeholder names (Fact/Selector/Date), generalized from -- not tied
to -- the "KPI01"/"Total Category Volume" shape that motivated this.
"""
from __future__ import annotations

from semabridge.api.services.project_mapping_engine import (
    STATUS_NEEDS_REVIEW,
    STATUS_PREDICTED_FAILURE,
    _entity_status,
)
from semabridge.converter.dax_ast_parser import (
    ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED,
    ADVISORY_CATEGORY_UNREACHABLE_DIMENSION,
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


def _build_osi_model() -> OSIModel:
    return OSIModel(
        unique_name="synthetic-model",
        label="Synthetic Model",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="Fact",
                columns=[
                    OSIColumn(unique_name="Amount", data_type=OSIDataType.FLOAT),
                    OSIColumn(unique_name="Units", data_type=OSIDataType.INTEGER),
                    OSIColumn(unique_name="Year", data_type=OSIDataType.INTEGER),
                ],
            ),
            OSIDataset(
                unique_name="Date",
                columns=[OSIColumn(unique_name="Year", data_type=OSIDataType.INTEGER)],
            ),
            OSIDataset(
                unique_name="Selector",
                columns=[OSIColumn(unique_name="Choice", data_type=OSIDataType.INTEGER)],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="MeasureA",
                label="Measure A",
                dataset="Fact",
                expression="SUM([Amount])",
                aggregation=OSIAggregationType.NONE,
            ),
            OSIMetric(
                unique_name="MeasureB",
                label="Measure B",
                dataset="Fact",
                expression="SUM([Units])",
                aggregation=OSIAggregationType.NONE,
            ),
            OSIMetric(
                unique_name="Switcher",
                label="Switcher",
                dataset="Selector",
                # The KPI01 idiom, generalized: a disconnected selector
                # table's metric picks between two OTHER tables' real
                # aggregates based on its own value. Selector has no
                # relationship to Fact/Date; each branch, condition
                # stripped, needs nothing from Selector at all.
                expression=(
                    "IF(SUM('Selector'[Choice])=1, [MeasureA], "
                    "IF(SUM('Selector'[Choice])=2, \" \", "
                    "IF(SUM('Selector'[Choice])=3, CALCULATE([MeasureB],'Date'[Year]=1))))"
                ),
                aggregation=OSIAggregationType.NONE,
            ),
            OSIMetric(
                unique_name="OrdinaryMetric",
                label="Ordinary Metric",
                dataset="Fact",
                expression="SUM([Amount])+SUM([Units])",
                aggregation=OSIAggregationType.NONE,
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="REL_FACT_YEAR__DATE_YEAR",
                from_dataset="Fact",
                from_columns=["Year"],
                to_dataset="Date",
                to_columns=["Year"],
            )
        ],
    )


def test_disconnected_selector_metric_is_kept_single_as_built():
    """Commit 44766b0 ("keep selector KPI metrics single as built without
    splitting") intentionally disabled the conversion-time decomposition
    (osi_to_sml step 4a2). The selector metric must stay ONE metric, no
    Switcher_BRANCH_* companions are generated, and its relationship-graph
    problem is still surfaced through the unreachable-dimension advisory.
    The decomposition helper itself (try_decompose_disconnected_selector_metric)
    is still covered directly in test_dax_disconnected_selector_decomposition.py.
    """
    sml_model = OSIToSMLConverter().from_osi(_build_osi_model())

    switcher = next(m for m in sml_model.metrics if m.unique_name == "Switcher")
    assert ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED not in switcher.advisory_categories
    assert ADVISORY_CATEGORY_UNREACHABLE_DIMENSION in switcher.advisory_categories
    # advisory_notes/advisory_categories stay parallel (same length).
    assert len(switcher.advisory_notes) == len(switcher.advisory_categories)

    companions = [m for m in sml_model.metrics if m.unique_name.startswith("Switcher_BRANCH_")]
    assert companions == []
    assert not any(m.parent_metric_name == "Switcher" for m in sml_model.metrics)


def test_decomposed_metrics_map_to_needs_review_not_predicted_failure():
    entity = {
        "entity_kind": "metric",
        "advisory_categories": [ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED],
    }
    assert _entity_status(entity, is_manual=False) == STATUS_NEEDS_REVIEW
    assert _entity_status(entity, is_manual=False) != STATUS_PREDICTED_FAILURE


def test_ordinary_metric_is_completely_unaffected():
    """A metric with no IF-chain at all must see zero change: no new
    metrics generated on its behalf, no advisory added to it."""
    sml_model = OSIToSMLConverter().from_osi(_build_osi_model())

    ordinary = next(m for m in sml_model.metrics if m.unique_name == "OrdinaryMetric")
    assert ordinary.sync_enabled is True
    assert ordinary.sql_expression
    assert ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED not in ordinary.advisory_categories

    generated_for_ordinary = [
        m for m in sml_model.metrics if m.unique_name.startswith("OrdinaryMetric_BRANCH_")
    ]
    assert generated_for_ordinary == []


def test_measure_a_and_measure_b_are_also_unaffected():
    """The metrics referenced BY the branches must not themselves be
    touched -- decomposition only appends new metrics, never mutates an
    existing one other than the original selector metric's own advisory
    fields."""
    sml_model = OSIToSMLConverter().from_osi(_build_osi_model())

    measure_a = next(m for m in sml_model.metrics if m.unique_name == "MeasureA")
    measure_b = next(m for m in sml_model.metrics if m.unique_name == "MeasureB")
    assert measure_a.sync_enabled is True and measure_a.sql_expression
    assert measure_b.sync_enabled is True and measure_b.sql_expression
    assert measure_a.advisory_categories == []
    assert measure_b.advisory_categories == []
