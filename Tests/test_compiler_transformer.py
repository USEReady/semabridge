from __future__ import annotations

from types import SimpleNamespace

from semabridge.compiler.ast import SqlFunctionNode, SqlLiteralNode, SqlRawNode
from semabridge.compiler.compiler import DAXCompiler
from semabridge.compiler.registry import FunctionRegistry


def _model() -> SimpleNamespace:
    fact = SimpleNamespace(
        unique_name="Sales Fact",
        label="Sales Fact",
        source_table="Sales Fact",
        columns=[
            SimpleNamespace(unique_name="Revenue"),
            SimpleNamespace(unique_name="Cost"),
        ],
        is_fact=True,
        is_hidden=False,
    )
    metric = SimpleNamespace(
        unique_name="Revenue",
        dataset="Sales Fact",
        expression="SUM('Sales Fact'[Revenue])",
        sql_expression='SUM("SALES FACT"."REVENUE")',
    )
    return SimpleNamespace(datasets=[fact], metrics=[metric], relationships=[])


def test_compiler_generates_case_sql_for_if_expression() -> None:
    model = _model()
    compiler = DAXCompiler()
    result = compiler.compile_expression(
        'IF([Revenue] > 0, [Revenue], 0)',
        model=model,
        metrics=model.metrics,
        metric_name="TestMetric",
        table_alias="Sales Fact",
        dataset_name="Sales Fact",
    )

    assert result.is_success is True
    assert result.sql is not None
    assert "CASE WHEN" in result.sql.upper()
    assert "REVENUE" in result.sql.upper()
    assert "CALCULATE" not in result.sql.upper()


def test_function_registry_supports_custom_transformers() -> None:
    registry = FunctionRegistry()

    def _custom_handler(*, node, context, transformer):
        return SqlFunctionNode(name="COALESCE", args=[transformer.transform(node.args[0], context), SqlLiteralNode(value=0)])

    registry.register_function("DOUBLEUP", _custom_handler)

    model = _model()
    compiler = DAXCompiler(registry=registry)
    result = compiler.compile_expression(
        'DOUBLEUP([Revenue])',
        model=model,
        metrics=model.metrics,
        metric_name="TestMetric",
        table_alias="Sales Fact",
        dataset_name="Sales Fact",
    )

    assert result.is_success is True
    assert result.sql is not None
    assert result.sql.upper().startswith("COALESCE(")
    assert "DOUBLEUP" not in result.sql.upper()