"""Regression tests for _rename_column_references (core/engine/config.py)
and the allow_column_rename guard on _apply_mapping_overrides_from_config.

Context: a column-name override (manual, or the naming-convention rule's
auto-resolved collision rename) used to only touch column.label, never
column.unique_name -- so a physical CTAS/DDL rename was never real.
Fixing that means a column can now genuinely be renamed, which surfaces a
second problem: DAX expression text, relationship join columns, and
simple-aggregation metric/dimension-attribute source_column fields all
refer to a column by name as a separate string, not by holding the same
object -- none of those get updated by mutating column.unique_name alone.
_rename_column_references closes that gap; these tests cover each
reference kind it must rewrite, plus the guard that keeps it from running
at the one call site (the Snowflake-source metadata-inference fallback,
engine.py's late _apply_mapping_overrides_from_config call) where
relationships/metrics are already built against the original name before
this function ever runs.
"""
from __future__ import annotations

from pathlib import Path

from semabridge.core.engine.config import _apply_mapping_overrides_from_config, _rename_column_references
from semabridge.intermediate.models import (
    OSIAggregationType,
    OSIAttribute,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIDimension,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)


def _build_model() -> OSIModel:
    return OSIModel(
        unique_name="synthetic-model",
        label="Synthetic Model",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="AssociatedProduct",
                columns=[OSIColumn(unique_name="ProductID", data_type=OSIDataType.INTEGER)],
            ),
            OSIDataset(
                unique_name="Sales",
                columns=[
                    OSIColumn(unique_name="ProductID", data_type=OSIDataType.INTEGER),
                    OSIColumn(unique_name="Amount", data_type=OSIDataType.FLOAT),
                ],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="TotalAmount",
                dataset="Sales",
                expression="SUM(Sales[Amount])",
                aggregation=OSIAggregationType.NONE,
            ),
            # DAX-expression reference to the renamed column, table-qualified.
            OSIMetric(
                unique_name="DistinctProducts",
                dataset="Sales",
                expression="DISTINCTCOUNT('AssociatedProduct'[ProductID])",
                aggregation=OSIAggregationType.NONE,
            ),
            # Simple-aggregation metric referencing the column directly by name.
            OSIMetric(
                unique_name="ProductIdSum",
                dataset="AssociatedProduct",
                source_column="ProductID",
                aggregation=OSIAggregationType.SUM,
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="REL_SALES_PRODUCTID__ASSOCIATEDPRODUCT_PRODUCTID",
                from_dataset="Sales",
                from_columns=["ProductID"],
                to_dataset="AssociatedProduct",
                to_columns=["ProductID"],
            )
        ],
        dimensions=[
            OSIDimension(
                unique_name="Product",
                dataset="AssociatedProduct",
                attributes=[
                    OSIAttribute(
                        unique_name="ProductID",
                        dataset="AssociatedProduct",
                        source_column="ProductID",
                    )
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# One test per reference kind
# ---------------------------------------------------------------------------

def test_rewrites_table_qualified_dax_expression_text():
    model = _build_model()
    counts = _rename_column_references("ProductID", "ASSOCIATED_PRODUCT_PRODUCTID", "AssociatedProduct", model)

    distinct = next(m for m in model.metrics if m.unique_name == "DistinctProducts")
    assert distinct.expression == "DISTINCTCOUNT('AssociatedProduct'[ASSOCIATED_PRODUCT_PRODUCTID])"
    assert counts["expressions"] == 1

    # A different table's same-named column must be untouched.
    total = next(m for m in model.metrics if m.unique_name == "TotalAmount")
    assert total.expression == "SUM(Sales[Amount])"


def test_rewrites_relationship_join_columns_on_the_matching_side_only():
    model = _build_model()
    _rename_column_references("ProductID", "ASSOCIATED_PRODUCT_PRODUCTID", "AssociatedProduct", model)

    rel = model.relationships[0]
    # Renamed table is the "to" side here -- only that side changes.
    assert rel.to_columns == ["ASSOCIATED_PRODUCT_PRODUCTID"]
    assert rel.from_columns == ["ProductID"]


def test_rewrites_relationship_join_columns_on_the_from_side_when_that_matches():
    model = _build_model()
    _rename_column_references("ProductID", "SALES_PRODUCTID", "Sales", model)

    rel = model.relationships[0]
    assert rel.from_columns == ["SALES_PRODUCTID"]
    assert rel.to_columns == ["ProductID"]


def test_rewrites_simple_aggregation_metric_source_column():
    model = _build_model()
    counts = _rename_column_references("ProductID", "ASSOCIATED_PRODUCT_PRODUCTID", "AssociatedProduct", model)

    metric = next(m for m in model.metrics if m.unique_name == "ProductIdSum")
    assert metric.source_column == "ASSOCIATED_PRODUCT_PRODUCTID"
    assert counts["metric_source_column"] == 1


def test_rewrites_dimension_attribute_source_column():
    model = _build_model()
    counts = _rename_column_references("ProductID", "ASSOCIATED_PRODUCT_PRODUCTID", "AssociatedProduct", model)

    attr = model.dimensions[0].attributes[0]
    assert attr.source_column == "ASSOCIATED_PRODUCT_PRODUCTID"
    assert counts["attribute_source_column"] == 1


def test_does_not_touch_a_same_named_column_on_a_different_table():
    """Sales also has a column literally named ProductID -- renaming
    AssociatedProduct's must never bleed into Sales' own references."""
    model = _build_model()
    _rename_column_references("ProductID", "ASSOCIATED_PRODUCT_PRODUCTID", "AssociatedProduct", model)

    sales_col = next(c for c in model.datasets[1].columns if c.unique_name == "ProductID")
    assert sales_col.unique_name == "ProductID"


# ---------------------------------------------------------------------------
# End-to-end: _apply_mapping_overrides_from_config wires the rename +
# reference rewrite together for a real collision-auto-resolved override.
# ---------------------------------------------------------------------------

def test_apply_mapping_overrides_renames_column_and_propagates_references(tmp_path: Path):
    model = _build_model()
    cfg = tmp_path / "semabridge.yaml"
    cfg.write_text(
        "\n".join(
            [
                "project_name: test",
                "mappings_overrides:",
                "  - source_path: datasets.AssociatedProduct.columns.ProductID",
                "    target_name: ASSOCIATED_PRODUCT_PRODUCTID",
            ]
        ),
        encoding="utf-8",
    )

    _apply_mapping_overrides_from_config(model, cfg)

    renamed_col = model.datasets[0].columns[0]
    assert renamed_col.unique_name == "ASSOCIATED_PRODUCT_PRODUCTID"

    distinct = next(m for m in model.metrics if m.unique_name == "DistinctProducts")
    assert "ASSOCIATED_PRODUCT_PRODUCTID" in distinct.expression

    agg_metric = next(m for m in model.metrics if m.unique_name == "ProductIdSum")
    assert agg_metric.source_column == "ASSOCIATED_PRODUCT_PRODUCTID"

    assert model.relationships[0].to_columns == ["ASSOCIATED_PRODUCT_PRODUCTID"]
    assert model.dimensions[0].attributes[0].source_column == "ASSOCIATED_PRODUCT_PRODUCTID"


# ---------------------------------------------------------------------------
# The guard: allow_column_rename=False must never rename, only relabel --
# proving the late (post-build) fallback call site can't reintroduce the
# exact stale-reference bug this whole mechanism exists to fix.
# ---------------------------------------------------------------------------

def test_allow_column_rename_false_relabels_but_never_renames_or_rewrites_references(tmp_path: Path, caplog):
    model = _build_model()
    cfg = tmp_path / "semabridge.yaml"
    cfg.write_text(
        "\n".join(
            [
                "project_name: test",
                "mappings_overrides:",
                "  - source_path: datasets.AssociatedProduct.columns.ProductID",
                "    target_name: ASSOCIATED_PRODUCT_PRODUCTID",
            ]
        ),
        encoding="utf-8",
    )

    _apply_mapping_overrides_from_config(model, cfg, allow_column_rename=False)

    col = model.datasets[0].columns[0]
    # Cosmetic label update is still fine -- it's the physical/unique_name
    # rename and reference rewrite that must be skipped.
    assert col.label == "ASSOCIATED_PRODUCT_PRODUCTID"
    assert col.unique_name == "ProductID"

    # Nothing that referenced the column by name was touched, because
    # nothing was actually renamed.
    distinct = next(m for m in model.metrics if m.unique_name == "DistinctProducts")
    assert distinct.expression == "DISTINCTCOUNT('AssociatedProduct'[ProductID])"
    agg_metric = next(m for m in model.metrics if m.unique_name == "ProductIdSum")
    assert agg_metric.source_column == "ProductID"
    assert model.relationships[0].to_columns == ["ProductID"]
    assert model.dimensions[0].attributes[0].source_column == "ProductID"

    assert "Not renaming column" in caplog.text
