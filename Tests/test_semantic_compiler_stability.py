from __future__ import annotations

from types import SimpleNamespace

from semabridge.compiler.compiler import DAXCompiler
from semabridge.compiler.metric_classifier import MetricClassifier
from semabridge.compiler.schema_resolver import SemanticSchemaResolver
from semabridge.compiler.sql_validator import SQLValidator


def _dataset(name: str, columns: list[str], *, is_fact: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        unique_name=name,
        label=name,
        source_table=name,
        columns=[SimpleNamespace(unique_name=column) for column in columns],
        is_fact=is_fact,
        is_hidden=False,
    )


def _metric(name: str, dataset: str, expression: str) -> SimpleNamespace:
    return SimpleNamespace(
        unique_name=name,
        label=name,
        dataset=dataset,
        expression=expression,
        sql_expression=None,
    )


def _build_model() -> SimpleNamespace:
    datasets = [
        _dataset("Finance Fact", ["Revenue", "Cost"], is_fact=True),
        _dataset("Manufacturing Fact", ["Units", "Scrap"], is_fact=True),
        _dataset("HR Fact", ["Employee Count", "Headcount"], is_fact=True),
        _dataset("Sales Fact", ["Revenue", "Date", "Product ID"], is_fact=True),
        _dataset("Marketing Fact", ["Spend", "Clicks"], is_fact=True),
        _dataset("Fiscal Calendar", ["Date", "Year", "Quarter", "Month"], is_fact=False),
    ]
    metrics = [
        _metric("Base Revenue", "Finance Fact", "SUM('Finance Fact'[Revenue])"),
        _metric("Base Units", "Manufacturing Fact", "SUM('Manufacturing Fact'[Units])"),
        _metric("Headcount", "HR Fact", "SUM('HR Fact'[Employee Count])"),
        _metric("Sales Revenue", "Sales Fact", "[Base Revenue] + [Base Units]"),
        _metric("Margin Rate", "Sales Fact", "DIVIDE([Base Revenue], [Base Units])"),
        _metric("Trend", "Sales Fact", "CALCULATE([Sales Revenue], FILTER(ALL('Sales Fact'), 'Sales Fact'[Revenue] > 0))"),
        _metric("YTD Sales", "Sales Fact", "TOTALYTD([Sales Revenue], 'Fiscal Calendar'[Date])"),
        _metric("SPLY Sales", "Sales Fact", "SAMEPERIODLASTYEAR([Sales Revenue])"),
        _metric("@Indicator01", "Sales Fact", '"Active"'),
        _metric("#Space01", "Sales Fact", '" "'),
        _metric("KPI01", "Sales Fact", '"KPI"'),
    ]
    return SimpleNamespace(unique_name="Stable Semantic Model", label="Stable Semantic Model", datasets=datasets, relationships=[], metrics=metrics)


def test_semantic_compiler_stability_across_model_shapes() -> None:
    model = _build_model()
    compiler = DAXCompiler()
    results = compiler.compile_metrics(model.metrics, model=model, table_alias="Sales Fact", dataset_name="Sales Fact")

    validator = SQLValidator()

    deployable = [metric for metric in model.metrics if MetricClassifier().classify(metric).deploy]
    assert deployable

    for metric in deployable:
        result = results[metric.unique_name]
        assert result.is_success is True
        assert result.sql is not None
        assert "CALCULATE(" not in result.sql.upper()
        assert "FILTER(" not in result.sql.upper()
        assert "ALL(" not in result.sql.upper()
        assert "IF(" not in result.sql.upper()
        assert "ISBLANK(" not in result.sql.upper()
        assert "CONCATENATE(" not in result.sql.upper()
        assert "COUNTBLANK(" not in result.sql.upper()
        assert "SAMEPERIODLASTYEAR(" not in result.sql.upper()
        assert validator.validate(result.sql).is_valid is True

    for metric in [m for m in model.metrics if not MetricClassifier().classify(m).deploy]:
        assert results[metric.unique_name].is_success is False

    resolver = SemanticSchemaResolver(model=model, metrics=model.metrics)
    alias_a = resolver.stable_alias("Category Product", scope="Finance Fact")
    alias_b = resolver.stable_alias("Category Product", scope="Finance Fact")
    alias_c = resolver.stable_alias("Category Product", scope="Manufacturing Fact")

    assert alias_a == alias_b
    assert alias_a != alias_c
