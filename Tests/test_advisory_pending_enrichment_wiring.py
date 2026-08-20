"""Regression test for wiring
dax_calculate_filters_reachable_dimension_pending_enrichment into
osi_to_sml.py (_flag_reachable_dimension_filters_pending_enrichment) --
the opposite outcome of test_advisory_unreachable_dimension_wiring.py's
shape: a metric whose CALCULATE(...) filters a table WITH a relationship
path, whose translation still failed at dry-run (no live connection to run
the precomputed-column rewrite that resolves this at real deploy time).

Proves:
  1. The detector fires and records the advisory note/category on the
     metric, only once translation has already failed for it.
  2. It does NOT fire for a metric whose translation succeeded (nothing to
     flag), and does NOT fire for the unrelated ADVISORY_CATEGORY_
     UNREACHABLE_DIMENSION shape (no relationship path at all).

All placeholder names (Fact/Product), generalized from -- not tied to --
the "Total VanArsdel Units YTD" shape that motivated this.
"""
from __future__ import annotations

from semabridge.converter.dax_ast_parser import ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT
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
                    OSIColumn(unique_name="ProductId", data_type=OSIDataType.INTEGER),
                ],
            ),
            OSIDataset(
                unique_name="Product",
                columns=[
                    OSIColumn(unique_name="ProductId", data_type=OSIDataType.INTEGER),
                    OSIColumn(unique_name="isSpecial", data_type=OSIDataType.BOOLEAN),
                ],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="TotalAmount",
                label="Total Amount",
                dataset="Fact",
                expression="SUM([Amount])",
                aggregation=OSIAggregationType.NONE,
            ),
            OSIMetric(
                unique_name="SpecialProductAmount",
                label="Special Product Amount",
                dataset="Fact",
                # Reachable via the relationship below, but the AST renderer
                # refuses to guess a cross-table alias for 'Product', and
                # Tier-5 has no relationship info either -- fails today.
                expression='CALCULATE([TotalAmount], FILTER(ALL(Product[isSpecial]), Product[isSpecial]=TRUE))',
                aggregation=OSIAggregationType.NONE,
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="REL_FACT_PRODUCTID__PRODUCT_PRODUCTID",
                from_dataset="Fact",
                from_columns=["ProductId"],
                to_dataset="Product",
                to_columns=["ProductId"],
            )
        ],
    )


def test_pending_enrichment_advisory_recorded_for_failed_reachable_cross_table_metric():
    sml_model = OSIToSMLConverter().from_osi(_build_osi_model())

    special = next(m for m in sml_model.metrics if m.unique_name == "SpecialProductAmount")

    # Translation genuinely failed at dry-run -- this metric's DAX (a
    # measure reference wrapped in CALCULATE with a cross-table filter) is
    # not something any tier here can resolve without live schema.
    assert special.sync_enabled is False
    assert not special.sql_expression

    assert ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT in special.advisory_categories
    assert special.advisory_notes, "expected an advisory note explaining the pending-enrichment outcome"
    reason = special.advisory_notes[-1]
    assert "Fact" in reason
    assert "Product" in reason


def test_successfully_translated_metric_is_never_flagged():
    sml_model = OSIToSMLConverter().from_osi(_build_osi_model())

    total = next(m for m in sml_model.metrics if m.unique_name == "TotalAmount")

    assert total.sql_expression, "expected this ordinary same-table metric to translate normally"
    assert ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT not in total.advisory_categories
