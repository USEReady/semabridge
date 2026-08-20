"""Unit tests for
dax_ast_parser.dax_calculate_filters_reachable_dimension_pending_enrichment
-- the mapping-time detector for the OPPOSITE outcome of
dax_calculate_filters_unreachable_dimension: a CALCULATE(...) filter that
references a table WITH a relationship path from the calling metric's own
base table. Dry-run's translators can't yet resolve that reference (no live
connection to run the precomputed-column rewrite that resolves it at real
deploy time), so this is a "may succeed at deploy, needs review" signal, not
a "will fail at deploy" one.

All DAX bodies and table names are placeholders (Fact/Product/etc.),
generalized from -- but not tied to -- the "Total VanArsdel Units YTD"
(CALCULATE([Total Units YTD], FILTER(ALL(Product[isVanArsdel]),
Product[isVanArsdel]="Yes"))) shape that motivated this.
"""
from __future__ import annotations

from semabridge.converter.dax_ast_parser import (
    dax_calculate_filters_reachable_dimension_pending_enrichment,
    ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT,
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


def test_reachable_cross_table_filter_is_detected():
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    dax = 'CALCULATE([Total Units YTD], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]="Yes"))'

    result = dax_calculate_filters_reachable_dimension_pending_enrichment(
        dax, calling_dataset="Fact", relationships=rels,
    )

    assert result is not None
    assert result.filtered_table == "Product"
    assert result.category == ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT
    assert "Fact" in result.reason
    assert "Product" in result.reason


def test_multi_hop_reachable_cross_table_filter_is_detected():
    rels = [
        _rel("Fact", ["ProductId"], "Product", ["ProductId"]),
        _rel("Product", ["CategoryId"], "Category", ["CategoryId"]),
    ]
    dax = 'CALCULATE(SUM([Amount]), FILTER(ALL(Category[Name]), Category[Name]="Widgets"))'

    result = dax_calculate_filters_reachable_dimension_pending_enrichment(
        dax, calling_dataset="Fact", relationships=rels,
    )

    assert result is not None
    assert result.filtered_table == "Category"


def test_unreachable_cross_table_filter_is_not_flagged_by_this_detector():
    """This is dax_calculate_filters_unreachable_dimension's shape, not
    this one -- no relationship path exists at all here."""
    rels = [_rel("Fact", ["DimId"], "Dim", ["DimId"])]
    dax = "CALCULATE([SomeMeasure], 'Unrelated'[Flag]=1)"

    result = dax_calculate_filters_reachable_dimension_pending_enrichment(
        dax, calling_dataset="Fact", relationships=rels,
    )

    assert result is None


def test_same_table_filter_is_not_flagged():
    """CALCULATE(agg, filter) on the metric's own table is completely
    ordinary and must never be flagged as a cross-table reference."""
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    dax = "CALCULATE(SUM([Amount]), 'Fact'[Region]=\"West\")"

    result = dax_calculate_filters_reachable_dimension_pending_enrichment(
        dax, calling_dataset="Fact", relationships=rels,
    )

    assert result is None


def test_no_relationships_at_all_is_not_flagged():
    dax = 'CALCULATE([Total Units YTD], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]="Yes"))'

    result = dax_calculate_filters_reachable_dimension_pending_enrichment(
        dax, calling_dataset="Fact", relationships=[],
    )

    assert result is None


def test_dax_with_no_calculate_returns_none():
    result = dax_calculate_filters_reachable_dimension_pending_enrichment(
        "SUM([Amount])", calling_dataset="Fact", relationships=[],
    )
    assert result is None


def test_empty_dax_returns_none():
    assert dax_calculate_filters_reachable_dimension_pending_enrichment(
        "", calling_dataset="Fact", relationships=[],
    ) is None


def test_empty_calling_dataset_returns_none():
    assert dax_calculate_filters_reachable_dimension_pending_enrichment(
        "CALCULATE([X], 'Product'[Y]=1)", calling_dataset="", relationships=[],
    ) is None
