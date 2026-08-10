"""Unit tests for dax_ast_parser.dax_calculate_filters_unreachable_dimension
-- the mapping-time DETECTOR (not rewriter) for the KPI01/KPI02 idiom:
CALCULATE(measure, filter-on-a-table-with-no-relationship-path-from-the-
calling-metric's-own-base-table). This is the shape Snowflake's semantic
view compiler rejects as "a metric cannot refer to another dimension from
an unrelated entity" (error 010211) regardless of how the DAX is
translated -- see the module-level comment in dax_ast_parser.py for why a
rewrite can't fix this and only detection is in scope.

All DAX bodies and table names are placeholders (Selector/Fact/Dim/etc.),
generalized from — but not tied to — the KPI/Date/Category shape that
motivated this.
"""
from __future__ import annotations

from semabridge.converter.dax_ast_parser import dax_calculate_filters_unreachable_dimension
from semabridge.sml.models import SMLRelationship


def _rel(from_ds, from_cols, to_ds, to_cols):
    return SMLRelationship(
        unique_name=f"REL_{from_ds}_{to_ds}",
        from_dataset=from_ds,
        from_columns=from_cols,
        to_dataset=to_ds,
        to_columns=to_cols,
    )


def test_disconnected_selector_table_calculate_is_detected():
    """The exact KPI01/KPI02 shape, generalized: a disconnected selector
    table's metric wraps CALCULATE(measure, filter-on-a-table-it-cannot-
    reach). Fact has a real path to Dim; Selector has none."""
    rels = [_rel("Fact", ["DimId"], "Dim", ["DimId"])]
    dax = "IF(SUM('Selector'[Choice])=1, CALCULATE([TotalVolume], 'Dim'[Year]=1))"

    result = dax_calculate_filters_unreachable_dimension(
        dax,
        calling_dataset="Selector",
        relationships=rels,
        metric_datasets={"TotalVolume": "Fact"},
    )

    assert result is not None
    assert result.filtered_table == "Dim"
    assert result.referenced_measure == "TotalVolume"
    assert result.referenced_measure_dataset == "Fact"
    assert result.referenced_measure_has_path is True
    assert "Selector" in result.reason
    assert "Dim" in result.reason
    assert "TotalVolume" in result.reason


def test_reachable_filter_table_is_not_flagged():
    """The ordinary, working case: the calling metric's own table DOES
    have a path to the filtered dimension -- CALCULATE(agg, filter) is
    completely normal here and must not be flagged."""
    rels = [_rel("Fact", ["DimId"], "Dim", ["DimId"])]
    dax = "CALCULATE(SUM([Amount]), 'Dim'[Year]=1)"

    result = dax_calculate_filters_unreachable_dimension(
        dax, calling_dataset="Fact", relationships=rels,
    )

    assert result is None


def test_multi_hop_reachable_filter_table_is_not_flagged():
    rels = [
        _rel("Fact", ["ProductId"], "Product", ["ProductId"]),
        _rel("Product", ["CategoryId"], "Category", ["CategoryId"]),
    ]
    dax = "CALCULATE(SUM([Amount]), 'Category'[Name]=\"Widgets\")"

    result = dax_calculate_filters_unreachable_dimension(
        dax, calling_dataset="Fact", relationships=rels,
    )

    assert result is None


def test_no_relationship_anywhere_reaching_filter_table_omits_fix_suggestion():
    """When NEITHER the calling table NOR the referenced measure's table
    reaches the filtered dimension, referenced_measure_has_path is False,
    not True -- there's no relationship in the model to point users at,
    so the reason text must not imply one exists."""
    rels = [_rel("Fact", ["DimId"], "Dim", ["DimId"])]
    dax = "CALCULATE([SomeMeasure], 'Unrelated'[Flag]=1)"

    result = dax_calculate_filters_unreachable_dimension(
        dax,
        calling_dataset="Selector",
        relationships=rels,
        metric_datasets={"SomeMeasure": "Fact"},
    )

    assert result is not None
    assert result.referenced_measure_has_path is False
    assert "relationship anywhere in this model connects" in result.reason


def test_missing_metric_datasets_map_still_detects_but_skips_enrichment():
    """The detector's core job (is the filtered table reachable at all)
    doesn't require metric_datasets -- it's an optional enrichment only."""
    rels = [_rel("Fact", ["DimId"], "Dim", ["DimId"])]
    dax = "CALCULATE([SomeMeasure], 'Dim'[Year]=1)"

    result = dax_calculate_filters_unreachable_dimension(
        dax, calling_dataset="Selector", relationships=rels,
    )

    assert result is not None
    assert result.filtered_table == "Dim"
    assert result.referenced_measure == "SomeMeasure"
    assert result.referenced_measure_dataset is None
    assert result.referenced_measure_has_path is None


def test_compound_filter_inside_filter_function_is_still_walked():
    """Generalization beyond the direct-comparison shape: CALCULATE(agg,
    FILTER(table, predicate)) must still have its predicate's column
    references collected, not just a bare top-level comparison."""
    rels = [_rel("Fact", ["DimId"], "Dim", ["DimId"])]
    dax = "CALCULATE(SUM([Amount]), FILTER(ALL('OtherDim'), 'OtherDim'[Flag]=1))"

    result = dax_calculate_filters_unreachable_dimension(
        dax, calling_dataset="Selector", relationships=rels,
    )

    assert result is not None
    assert result.filtered_table == "OtherDim"


def test_calculate_nested_inside_if_branches_is_found():
    """The IF/SWITCH-selector idiom: CALCULATE can be nested several
    levels deep inside a branch, not just as the top-level expression."""
    rels = [_rel("Fact", ["DimId"], "Dim", ["DimId"])]
    dax = (
        "IF(SUM('Selector'[Choice])=1, \"literal\", "
        "IF(SUM('Selector'[Choice])=2, CALCULATE([SomeMeasure], 'Dim'[Year]=1)))"
    )

    result = dax_calculate_filters_unreachable_dimension(
        dax, calling_dataset="Selector", relationships=rels,
    )

    assert result is not None
    assert result.filtered_table == "Dim"


def test_no_relationships_at_all_is_detected():
    dax = "CALCULATE([SomeMeasure], 'Dim'[Year]=1)"

    result = dax_calculate_filters_unreachable_dimension(
        dax, calling_dataset="Selector", relationships=[],
    )

    assert result is not None
    assert result.filtered_table == "Dim"


def test_dax_with_no_calculate_returns_none():
    result = dax_calculate_filters_unreachable_dimension(
        "SUM([Amount])", calling_dataset="Fact", relationships=[],
    )
    assert result is None


def test_empty_dax_returns_none():
    assert dax_calculate_filters_unreachable_dimension("", calling_dataset="Fact", relationships=[]) is None


def test_empty_calling_dataset_returns_none():
    assert dax_calculate_filters_unreachable_dimension(
        "CALCULATE([X], 'Dim'[Year]=1)", calling_dataset="", relationships=[],
    ) is None
