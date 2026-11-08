"""Regression test: _create_enriched_view used to hardcode a check for a
column literally named "UNITS" in the fact table and unconditionally
inject SUM(UNITS) as a synthetic "TOTAL_UNITS_ALL" column — a fixture
built for one customer's fact table shape, with no other code ever
reading that column back out. Any other customer whose fact table
happens to have its own, unrelated "Units" column would silently get
this phantom column injected into their enriched view.

Synthetic placeholder names only.
"""
from unittest.mock import MagicMock

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, DataType


def _build_emitter() -> SnowflakeEmitter:
    config = SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",
        warehouse="test_wh",
        database="test_db",
        schema_name="test_schema",
        role="test_role",
    )
    return SnowflakeEmitter(config, ConnectorBehavior())


def _model_with_units_column() -> SMLModel:
    return SMLModel(
        unique_name="synthetic_model",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                is_fact=True,
                source_table="SalesFact",
                columns=[SMLColumn(unique_name="Units", data_type=DataType.INTEGER)],
            )
        ],
        metrics=[],
    )


def _create_view_sql(cursor):
    return [
        str(call[0][0]) for call in cursor.execute.call_args_list
        if "CREATE OR REPLACE VIEW" in str(call[0][0]).upper()
    ]


def test_create_enriched_view_never_injects_a_units_specific_total_column():
    """Nothing in this model needs enrichment, so no view is created at all
    -- and in particular no phantom SUM(UNITS) column is ever emitted."""
    emitter = _build_emitter()
    cursor = MagicMock()
    model = _model_with_units_column()

    result = emitter._create_enriched_view(model, cursor, fact_table="SalesFact")

    assert result is None
    for sql in _create_view_sql(cursor):  # none expected; guard either way
        assert "TOTAL_UNITS_ALL" not in sql
        assert 'SUM("UNITS")' not in sql


def test_enriched_view_built_for_a_real_need_still_has_no_units_specific_total_column():
    """Same fact table, but with a genuine reason to build the view (a
    metric/column name clash -> _COL_UNITS projection): the view is created
    and still carries no hardcoded UNITS total."""
    from semabridge.sml.models import SMLMetric

    emitter = _build_emitter()
    cursor = MagicMock()
    model = _model_with_units_column()
    model.metrics.append(SMLMetric(unique_name="Units", dataset="SalesFact", expression="SUM([Units])"))

    result = emitter._create_enriched_view(model, cursor, fact_table="SalesFact")

    assert result is not None
    create_view_sql = _create_view_sql(cursor)
    assert len(create_view_sql) == 1
    assert '"_COL_UNITS"' in create_view_sql[0]
    assert "TOTAL_UNITS_ALL" not in create_view_sql[0]
    assert 'SUM("UNITS")' not in create_view_sql[0]
