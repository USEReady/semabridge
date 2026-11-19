"""Measure-only tables are dropped from the TABLES clause only when the model
has other tables to re-home their measures to, and never when they are facts."""

from __future__ import annotations

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import (
    AggregationType,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
)


def _make_config():
    return SnowflakeConfig(
        account="test_acct", user="test_user", database="TEST_DB", schema_name="TEST_SCHEMA"
    )


def _model(*, with_sales: bool, measures_is_fact: bool = False) -> SMLModel:
    datasets = []
    if with_sales:
        datasets.append(
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="SalesID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Amount", data_type=DataType.FLOAT),
                ],
            )
        )
    # Power BI measure container: hosts measures, no data columns.
    datasets.append(
        SMLDataset(
            unique_name="KeyMeasures",
            is_fact=measures_is_fact,
            columns=[SMLColumn(unique_name="_", data_type=DataType.INTEGER, is_hidden=True)],
        )
    )
    expr = 'SUM(SALES."AMOUNT")' if with_sales else "COUNT(*)"
    return SMLModel(
        unique_name="MeasureOnlyModel",
        datasets=datasets,
        relationships=[],
        metrics=[
            SMLMetric(
                unique_name="TotalAmount",
                dataset="KeyMeasures",
                expression="SUM('Sales'[Amount])" if with_sales else "COUNTROWS('KeyMeasures')",
                sql_expression=expr,
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            )
        ],
    )


def _tables_clause(model: SMLModel) -> str:
    ddl = SnowflakeEmitter(_make_config()).generate_ddls(model)[0]
    return ddl.split("TABLES (", 1)[1].split(")\n", 1)[0]


def test_measure_container_dropped_when_other_tables_exist():
    tables = _tables_clause(_model(with_sales=True))
    assert "SALES AS" in tables
    assert "KEYMEASURES AS" not in tables


def test_measure_container_kept_when_it_is_the_only_table():
    tables = _tables_clause(_model(with_sales=False))
    assert "KEYMEASURES AS" in tables


def test_measure_container_kept_when_it_is_a_fact():
    tables = _tables_clause(_model(with_sales=True, measures_is_fact=True))
    assert "KEYMEASURES AS" in tables
