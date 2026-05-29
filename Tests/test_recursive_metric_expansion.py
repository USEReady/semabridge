from __future__ import annotations

from types import SimpleNamespace

from semabridge.compiler.compiler import DAXCompiler


def test_recursive_measure_expansion_compiles_dependency_chain() -> None:
    model = SimpleNamespace(
        datasets=[
            SimpleNamespace(
                unique_name="Sales Fact",
                label="Sales Fact",
                source_table="Sales Fact",
                columns=[SimpleNamespace(unique_name="Revenue"), SimpleNamespace(unique_name="Cost")],
                is_fact=True,
                is_hidden=False,
            )
        ],
        relationships=[],
        metrics=[],
    )
    base = SimpleNamespace(unique_name="Base Sales", dataset="Sales Fact", expression="SUM('Sales Fact'[Revenue])", sql_expression=None)
    margin = SimpleNamespace(unique_name="Margin", dataset="Sales Fact", expression="[Base Sales] - SUM('Sales Fact'[Cost])", sql_expression=None)
    margin_pct = SimpleNamespace(unique_name="Margin Pct", dataset="Sales Fact", expression="DIVIDE([Margin], [Base Sales])", sql_expression=None)
    model.metrics = [base, margin, margin_pct]

    compiler = DAXCompiler()
    results = compiler.compile_metrics(model.metrics, model=model, table_alias="Sales Fact", dataset_name="Sales Fact")

    assert results["Base Sales"].is_success is True
    assert results["Margin"].is_success is True
    assert results["Margin Pct"].is_success is True
    assert model.metrics[0].sql_expression is not None
    assert model.metrics[1].sql_expression is not None
    assert model.metrics[2].sql_expression is not None
    assert "[" not in model.metrics[2].sql_expression
    assert "CALCULATE" not in model.metrics[2].sql_expression.upper()


def test_recursive_measure_expansion_detects_cycles() -> None:
    model = SimpleNamespace(
        datasets=[SimpleNamespace(unique_name="Cycle Fact", label="Cycle Fact", source_table="Cycle Fact", columns=[SimpleNamespace(unique_name="Value")], is_fact=True, is_hidden=False)],
        relationships=[],
        metrics=[],
    )
    a = SimpleNamespace(unique_name="Metric A", dataset="Cycle Fact", expression="[Metric B] + 1", sql_expression=None)
    b = SimpleNamespace(unique_name="Metric B", dataset="Cycle Fact", expression="[Metric A] + 1", sql_expression=None)
    model.metrics = [a, b]

    compiler = DAXCompiler()
    results = compiler.compile_metrics(model.metrics, model=model, table_alias="Cycle Fact", dataset_name="Cycle Fact")

    assert results["Metric A"].is_success is False or results["Metric B"].is_success is False