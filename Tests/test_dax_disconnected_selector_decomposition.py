"""Unit tests for dax_ast_parser.try_decompose_disconnected_selector_metric
-- detects a metric declared on a disconnected selector/parameter table
(e.g. a Power BI "field parameter" table with no real business key) whose
DAX is an IF-chain switching between OTHER tables' real aggregates based on
the selector's own value, and extracts each non-trivial branch (condition
stripped away) as a standalone, self-contained DAX expression.

Real motivating shape (generalized, not tied to real names):
IF(SUM('KPI'[KPI])=1, [MeasureA], IF(SUM('KPI'[KPI])=2, " ",
IF(SUM('KPI'[KPI])=3, CALCULATE([MeasureB], 'Date'[Year]=1))))
-- 'KPI' has no relationship to the tables MeasureA/MeasureB live on, so
Snowflake rejects the metric as one expression; but each branch, once its
own selector condition is removed, references NOTHING on 'KPI' and is a
perfectly valid standalone metric on MeasureA/MeasureB's own table.

IMPORTANT: this codebase's own AST renderer does NOT validate cross-table
reachability for an inlined MEASURE reference -- it just splices in the
referenced measure's SQL verbatim regardless of which table it lives on.
So a metric matching this shape typically translates "successfully" by
this codebase's own check even when genuinely broken (confirmed against a
real customer deploy where Snowflake rejected exactly this shape at
DDL-execution time with error 010211). That's why the detector requires
`relationships` and checks has_relationship_path itself, rather than
relying on translation having failed -- see the reachability tests below.
"""
from __future__ import annotations

from semabridge.converter.dax_ast_parser import (
    ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED,
    try_decompose_disconnected_selector_metric,
)
from semabridge.sml.models import SMLRelationship


def _rel(from_ds, from_cols, to_ds, to_cols):
    return SMLRelationship(
        unique_name=f"REL_{from_ds}_{to_ds}",
        from_dataset=from_ds,
        from_columns=from_cols,
        to_dataset=to_ds,
        to_columns=to_cols,
    )


_REAL_SHAPE_DAX = (
    "IF(SUM('KPI'[KPI])=1,CONCATENATE(LEFT([Total Units YTD Var %2],3),"
    '(" % ROI (Return on Investment)")),IF(SUM(\'KPI\'[KPI])=2," ",'
    "IF(SUM('KPI'[KPI])=3,CALCULATE([Total Category Volume],'Date'[Running Year]=1))))"
)
_REAL_SHAPE_METRIC_DATASETS = {
    "Total Units YTD Var %2": "SalesFact",
    "Total Category Volume": "SalesFact",
}


def test_real_shape_decomposes_into_two_branches_skipping_the_blank_one():
    result = try_decompose_disconnected_selector_metric(
        _REAL_SHAPE_DAX, calling_dataset="KPI", relationships=[],
        metric_datasets=_REAL_SHAPE_METRIC_DATASETS,
    )

    assert result is not None
    assert result.selector_table == "KPI"
    assert result.skipped_trivial_count == 1  # the bare " " branch (KPI=2)
    assert len(result.branches) == 2

    by_condition = {b.condition_dax: b for b in result.branches}
    assert "SUM('KPI'[KPI])=1" in by_condition
    assert "SUM('KPI'[KPI])=3" in by_condition
    assert by_condition["SUM('KPI'[KPI])=1"].branch_dataset == "SalesFact"
    assert by_condition["SUM('KPI'[KPI])=3"].branch_dataset == "SalesFact"
    assert "Total Units YTD Var %2" in by_condition["SUM('KPI'[KPI])=1"].branch_dax
    assert "Total Category Volume" in by_condition["SUM('KPI'[KPI])=3"].branch_dax
    # The branch DAX must not reference the selector table at all.
    assert "KPI" not in by_condition["SUM('KPI'[KPI])=1"].branch_dax
    assert "KPI" not in by_condition["SUM('KPI'[KPI])=3"].branch_dax


def test_real_shape_still_decomposes_when_kpi_has_an_unrelated_relationship():
    """A relationship existing SOMEWHERE in the model that doesn't
    actually connect KPI to SalesFact must not block decomposition."""
    rels = [_rel("Unrelated", ["Id"], "AlsoUnrelated", ["Id"])]
    result = try_decompose_disconnected_selector_metric(
        _REAL_SHAPE_DAX, calling_dataset="KPI", relationships=rels,
        metric_datasets=_REAL_SHAPE_METRIC_DATASETS,
    )
    assert result is not None
    assert len(result.branches) == 2


def test_declines_when_the_branch_table_is_actually_reachable():
    """The key safety check: if calling_dataset DOES have a relationship
    path to a branch's dataset, the original metric may well be valid
    Snowflake SQL as written (a real relationship exists) -- decline
    decomposing it rather than second-guess a metric that already works."""
    rels = [_rel("KPI", ["ProductId"], "SalesFact", ["ProductId"])]
    result = try_decompose_disconnected_selector_metric(
        _REAL_SHAPE_DAX, calling_dataset="KPI", relationships=rels,
        metric_datasets=_REAL_SHAPE_METRIC_DATASETS,
    )
    assert result is None


def test_re_serialized_branch_dax_is_itself_parseable_round_trip():
    """The extracted branch DAX gets fed back through the normal
    translation pipeline as if hand-authored -- it must parse cleanly."""
    from semabridge.converter.dax_ast_parser import DaxAstParser

    result = try_decompose_disconnected_selector_metric(
        _REAL_SHAPE_DAX, calling_dataset="KPI", relationships=[],
        metric_datasets=_REAL_SHAPE_METRIC_DATASETS,
    )
    assert result is not None
    for branch in result.branches:
        node = DaxAstParser().parse(branch.branch_dax)
        assert node is not None, f"failed to re-parse: {branch.branch_dax!r}"


def test_ordinary_calculate_is_not_mistaken_for_a_selector_chain():
    """A completely normal metric (no IF-chain at all) must never match."""
    result = try_decompose_disconnected_selector_metric(
        "CALCULATE(SUM([Amount]), 'Date'[Year]=2024)", calling_dataset="Fact",
        relationships=[], metric_datasets={},
    )
    assert result is None


def test_single_branch_if_is_not_decomposed():
    """Needs at least 2 conditioned branches to look like a real selector
    -- a single plain IF is just ordinary conditional logic, not this
    pattern, and forcing a decomposition here would be overreach."""
    dax = "IF(SUM('KPI'[KPI])=1, CALCULATE([MeasureA],'Date'[Year]=1))"
    result = try_decompose_disconnected_selector_metric(
        dax, calling_dataset="KPI", relationships=[], metric_datasets={"MeasureA": "Fact"},
    )
    assert result is None


def test_condition_referencing_a_different_table_declines_entirely():
    """If a condition in the chain references some OTHER table (not the
    calling dataset), this isn't the pure-selector shape understood here
    -- decline rather than guess."""
    dax = (
        "IF('OtherTable'[Flag]=1, [MeasureA], "
        "IF('OtherTable'[Flag]=2, [MeasureB], [MeasureC]))"
    )
    result = try_decompose_disconnected_selector_metric(
        dax, calling_dataset="KPI", relationships=[],
        metric_datasets={"MeasureA": "Fact", "MeasureB": "Fact", "MeasureC": "Fact"},
    )
    assert result is None


def test_condition_containing_a_measure_reference_declines_entirely():
    dax = (
        "IF([SomeMeasure]=1, [MeasureA], IF([SomeMeasure]=2, [MeasureB], [MeasureC]))"
    )
    result = try_decompose_disconnected_selector_metric(
        dax, calling_dataset="KPI", relationships=[],
        metric_datasets={"MeasureA": "Fact", "MeasureB": "Fact", "MeasureC": "Fact", "SomeMeasure": "Fact"},
    )
    assert result is None


def test_no_condition_actually_references_calling_dataset_declines():
    """Every condition happens to reference nothing at all (constants) --
    this isn't "a selector on calling_dataset" in any meaningful sense."""
    dax = "IF(1=1, [MeasureA], IF(1=2, [MeasureB], [MeasureC]))"
    result = try_decompose_disconnected_selector_metric(
        dax, calling_dataset="KPI", relationships=[],
        metric_datasets={"MeasureA": "Fact", "MeasureB": "Fact", "MeasureC": "Fact"},
    )
    assert result is None


def test_branch_still_referencing_calling_dataset_declines_entirely():
    """A branch that ITSELF still depends on the selector table can't be
    safely decomposed -- decline the whole metric rather than guess."""
    dax = (
        "IF(SUM('KPI'[KPI])=1, CALCULATE([MeasureA], 'KPI'[Category]=\"X\"), "
        "IF(SUM('KPI'[KPI])=2, [MeasureB], [MeasureC]))"
    )
    result = try_decompose_disconnected_selector_metric(
        dax, calling_dataset="KPI", relationships=[],
        metric_datasets={"MeasureA": "Fact", "MeasureB": "Fact", "MeasureC": "Fact"},
    )
    assert result is None


def test_branch_with_ambiguous_multi_measure_datasets_declines_entirely():
    """A branch whose measures resolve to more than one distinct dataset
    is too ambiguous to anchor a new standalone metric on -- decline."""
    dax = (
        "IF(SUM('KPI'[KPI])=1, [MeasureA]+[MeasureFromOtherTable], "
        "IF(SUM('KPI'[KPI])=2, [MeasureB], [MeasureC]))"
    )
    result = try_decompose_disconnected_selector_metric(
        dax, calling_dataset="KPI", relationships=[],
        metric_datasets={
            "MeasureA": "Fact", "MeasureFromOtherTable": "SomethingElse",
            "MeasureB": "Fact", "MeasureC": "Fact",
        },
    )
    assert result is None


def test_all_branches_trivial_returns_none():
    """If every branch is a bare literal, there's nothing to decompose --
    the existing advisory-only handling should apply unchanged."""
    dax = 'IF(SUM(\'KPI\'[KPI])=1," ",IF(SUM(\'KPI\'[KPI])=2,"  "))'
    result = try_decompose_disconnected_selector_metric(
        dax, calling_dataset="KPI", relationships=[], metric_datasets={},
    )
    assert result is None


def test_terminal_else_branch_is_included_when_present():
    dax = "IF(SUM('KPI'[KPI])=1, [MeasureA], IF(SUM('KPI'[KPI])=2, [MeasureB], [MeasureC]))"
    result = try_decompose_disconnected_selector_metric(
        dax, calling_dataset="KPI", relationships=[],
        metric_datasets={"MeasureA": "Fact", "MeasureB": "Fact", "MeasureC": "Fact"},
    )
    assert result is not None
    conditions = {b.condition_dax for b in result.branches}
    assert "otherwise" in conditions  # the terminal else (KPI is neither 1 nor 2)
    assert len(result.branches) == 3


def test_empty_dax_or_calling_dataset_returns_none():
    assert try_decompose_disconnected_selector_metric("", calling_dataset="KPI", relationships=[]) is None
    assert try_decompose_disconnected_selector_metric("IF(1=1,2,3)", calling_dataset="", relationships=[]) is None
