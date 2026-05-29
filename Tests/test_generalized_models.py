from __future__ import annotations

from types import SimpleNamespace

from semabridge.compiler.compiler import DAXCompiler
from semabridge.compiler.schema_resolver import SemanticSchemaResolver


def test_generalized_model_with_spaces_and_relationship_path() -> None:
    model = SimpleNamespace(
        datasets=[
            SimpleNamespace(
                unique_name="Sales Fact",
                label="Sales Fact",
                source_table="Sales Fact",
                columns=[
                    SimpleNamespace(unique_name="Product ID"),
                    SimpleNamespace(unique_name="Customer ID"),
                    SimpleNamespace(unique_name="Net Revenue"),
                ],
                is_fact=True,
                is_hidden=False,
            ),
            SimpleNamespace(
                unique_name="Product Dimension",
                label="Product Dimension",
                source_table="Product Dimension",
                columns=[SimpleNamespace(unique_name="Product ID"), SimpleNamespace(unique_name="Category")],
                is_fact=False,
                is_hidden=False,
            ),
            SimpleNamespace(
                unique_name="Customer Segment",
                label="Customer Segment",
                source_table="Customer Segment",
                columns=[SimpleNamespace(unique_name="Customer ID"), SimpleNamespace(unique_name="Region")],
                is_fact=False,
                is_hidden=False,
            ),
        ],
        relationships=[
            SimpleNamespace(from_dataset="Sales Fact", from_columns=["Product ID"], to_dataset="Product Dimension", to_columns=["Product ID"], is_active=True),
            SimpleNamespace(from_dataset="Sales Fact", from_columns=["Customer ID"], to_dataset="Customer Segment", to_columns=["Customer ID"], is_active=True),
        ],
        metrics=[
            SimpleNamespace(
                unique_name="Net Revenue",
                dataset="Sales Fact",
                expression="SUM('Sales Fact'[Net Revenue])",
                sql_expression='SUM("SALES FACT"."NET REVENUE")',
            )
        ],
    )

    resolver = SemanticSchemaResolver(model=model, metrics=model.metrics)
    path = resolver.resolve_relationship_path("Sales Fact", "Customer Segment")
    assert path is not None
    assert len(path) == 1

    result = DAXCompiler().compile_expression(
        "CALCULATE([Net Revenue], 'Customer Segment'[Region] = \"US\")",
        model=model,
        metrics=model.metrics,
        metric_name="Filtered Revenue",
        table_alias="Sales Fact",
        dataset_name="Sales Fact",
    )

    assert result.is_success is True
    assert result.sql is not None
    assert "SALES FACT" in result.sql.upper()
    assert "CUSTOMER SEGMENT" in result.sql.upper()
    assert "US" in result.sql.upper()
    assert "[" not in result.sql and "]" not in result.sql