from __future__ import annotations

from types import SimpleNamespace

from semabridge.compiler.compiler import DAXCompiler


def test_totalytd_uses_dynamic_calendar_binding() -> None:
    model = SimpleNamespace(
        datasets=[
            SimpleNamespace(
                unique_name="Date Ledger",
                label="Date Ledger",
                source_table="Date Ledger",
                columns=[
                    SimpleNamespace(unique_name="Date"),
                    SimpleNamespace(unique_name="Year"),
                    SimpleNamespace(unique_name="Month"),
                    SimpleNamespace(unique_name="Quarter"),
                ],
                is_fact=False,
                is_hidden=False,
            ),
            SimpleNamespace(
                unique_name="Fact Ledger",
                label="Fact Ledger",
                source_table="Fact Ledger",
                columns=[SimpleNamespace(unique_name="Revenue")],
                is_fact=True,
                is_hidden=False,
            ),
        ],
        relationships=[],
        metrics=[
            SimpleNamespace(
                unique_name="Revenue",
                dataset="Fact Ledger",
                expression="SUM('Fact Ledger'[Revenue])",
                sql_expression='SUM("FACT LEDGER"."REVENUE")',
            )
        ],
    )

    result = DAXCompiler().compile_expression(
        "TOTALYTD([Revenue], 'Date Ledger'[Date])",
        model=model,
        metrics=model.metrics,
        metric_name="YTD Revenue",
        table_alias="Fact Ledger",
        dataset_name="Fact Ledger",
    )

    assert result.is_success is True
    assert result.sql is not None
    assert "OVER (" in result.sql.upper()
    assert 'PARTITION BY "DATE LEDGER"."YEAR"' in result.sql.upper()
    assert 'ORDER BY "DATE LEDGER"."DATE"' in result.sql.upper()
