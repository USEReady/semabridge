from __future__ import annotations

from types import SimpleNamespace

from semabridge.compiler.compiler import DAXCompiler
from semabridge.compiler.dependency_resolver import MetricDependencyResolver


def _metric(name: str, expression: str, dataset: str = "Sales Fact") -> SimpleNamespace:
    return SimpleNamespace(unique_name=name, dataset=dataset, expression=expression, sql_expression=None)


def _model(metrics: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(
        datasets=[
            SimpleNamespace(
                unique_name="Sales Fact",
                label="Sales Fact",
                source_table="Sales Fact",
                columns=[SimpleNamespace(unique_name="Revenue"), SimpleNamespace(unique_name="Cost"), SimpleNamespace(unique_name="Units")],
                is_fact=True,
                is_hidden=False,
            ),
            SimpleNamespace(
                unique_name="Finance Fact",
                label="Finance Fact",
                source_table="Finance Fact",
                columns=[SimpleNamespace(unique_name="Total")],
                is_fact=True,
                is_hidden=False,
            ),
        ],
        relationships=[],
        metrics=metrics,
    )


def test_dependency_plan_simple_chain() -> None:
    metrics = [
        _metric("A", "SUM('Sales Fact'[Revenue])"),
        _metric("B", "[A] * 1.1"),
        _metric("C", "[B] + [A]"),
    ]
    plan = MetricDependencyResolver(metrics=metrics, model=_model(metrics)).build_dependency_plan(metrics)

    assert plan.graph["A"] == []
    assert plan.graph["B"] == ["A"]
    assert plan.graph["C"] == ["A", "B"]
    assert plan.ordered == ["A", "B", "C"]
    assert plan.groups == [["A"], ["B"], ["C"]]
    assert plan.cycles == []
    assert plan.missing_dependencies == {}


def test_dependency_plan_multiple_dependencies_is_deterministic() -> None:
    metrics = [
        _metric("A", "SUM('Sales Fact'[Revenue])"),
        _metric("B", "SUM('Sales Fact'[Cost])"),
        _metric("C", "[A] + [B]"),
    ]
    plan = MetricDependencyResolver(metrics=metrics, model=_model(metrics)).build_dependency_plan(metrics)

    assert plan.graph["C"] == ["A", "B"]
    assert plan.ordered == ["A", "B", "C"]
    assert plan.groups[0] == ["A", "B"]
    assert plan.groups[1] == ["C"]


def test_dependency_plan_tracks_missing_dependencies() -> None:
    metrics = [
        _metric("A", "[B] + 1"),
    ]
    plan = MetricDependencyResolver(metrics=metrics, model=_model(metrics)).build_dependency_plan(metrics)

    assert plan.graph["A"] == []
    assert plan.missing_dependencies == {"A": ["B"]}
    assert plan.ordered == ["A"]


def test_dependency_plan_propagates_missing_dependencies_to_dependents() -> None:
    metrics = [
        _metric("A", "[Missing Base] + 1"),
        _metric("B", "[A] * 2"),
        _metric("C", "[B] + SUM('Sales Fact'[Revenue])"),
        _metric("Unrelated", "SUM('Sales Fact'[Cost])"),
    ]
    plan = MetricDependencyResolver(metrics=metrics, model=_model(metrics)).build_dependency_plan(metrics)

    assert plan.missing_dependencies["A"] == ["Missing Base"]
    assert plan.missing_dependencies["B"] == ["Missing Base"]
    assert plan.missing_dependencies["C"] == ["Missing Base"]
    assert "Unrelated" not in plan.missing_dependencies
    assert "Unrelated" in plan.ordered


def test_dependency_plan_detects_cycles() -> None:
    metrics = [
        _metric("A", "[B] + 1"),
        _metric("B", "[C] + 1"),
        _metric("C", "[A] + 1"),
    ]
    plan = MetricDependencyResolver(metrics=metrics, model=_model(metrics)).build_dependency_plan(metrics)

    assert plan.ordered == []
    assert plan.groups == [] or plan.groups == [[]]
    assert plan.cycles
    cycle_nodes = {name.casefold() for cycle in plan.cycles for name in cycle}
    assert cycle_nodes == {"a", "b", "c"}


def test_compile_metrics_orders_dependency_chain() -> None:
    metrics = [
        _metric("Base Sales", "SUM('Sales Fact'[Revenue])"),
        _metric("Margin", "[Base Sales] - SUM('Sales Fact'[Cost])"),
        _metric("Margin Pct", "DIVIDE([Margin], [Base Sales])"),
    ]
    model = _model(metrics)

    compiler = DAXCompiler()
    results = compiler.compile_metrics(metrics, model=model, table_alias="Sales Fact", dataset_name="Sales Fact")

    assert results["Base Sales"].is_success is True
    assert results["Margin"].is_success is True
    assert results["Margin Pct"].is_success is True
    assert model.metrics[0].sql_expression is not None
    assert model.metrics[1].sql_expression is not None
    assert model.metrics[2].sql_expression is not None
    assert "[" not in model.metrics[2].sql_expression
