from __future__ import annotations

from types import SimpleNamespace

from semabridge.compiler.compiler import DAXCompiler
from semabridge.connectors.sql_validator import validate_sql_expression


def _calendar_model() -> SimpleNamespace:
    return SimpleNamespace(
        datasets=[
            SimpleNamespace(
                unique_name="Date",
                label="Date",
                source_table="Date",
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
                unique_name="Sales Fact",
                label="Sales Fact",
                source_table="Sales Fact",
                columns=[SimpleNamespace(unique_name="Units")],
                is_fact=True,
                is_hidden=False,
            ),
        ],
        relationships=[],
        metrics=[SimpleNamespace(unique_name="Units", dataset="Sales Fact", expression="SUM('Sales Fact'[Units])", sql_expression='SUM("SALES FACT"."UNITS")')],
    )


def test_totalytd_lowers_to_valid_window_sql() -> None:
    model = _calendar_model()
    result = DAXCompiler().compile_expression(
        "TOTALYTD(IF([Units] > 0, [Units], 0), 'Date'[Date])",
        model=model,
        metrics=model.metrics,
        metric_name="YTD Units",
        table_alias="Sales Fact",
        dataset_name="Sales Fact",
    )

    assert result.is_success is True
    assert result.sql is not None
    assert "CASE WHEN" in result.sql.upper()
    assert "OVER ()" not in result.sql.upper()
    assert validate_sql_expression(result.sql).is_valid is True


def test_sameperiodlastyear_lowers_without_raw_dax() -> None:
    model = _calendar_model()
    result = DAXCompiler().compile_expression(
        "SAMEPERIODLASTYEAR([Units])",
        model=model,
        metrics=model.metrics,
        metric_name="SPLY Units",
        table_alias="Sales Fact",
        dataset_name="Sales Fact",
    )

    assert result.is_success is True
    assert result.sql is not None
    assert "SAMEPERIODLASTYEAR(" not in result.sql.upper()
    assert "RELATED(" not in result.sql.upper()
    assert validate_sql_expression(result.sql).is_valid is True
