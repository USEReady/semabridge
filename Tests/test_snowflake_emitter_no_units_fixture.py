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


def test_create_enriched_view_never_injects_a_units_specific_total_column():
    emitter = _build_emitter()
    cursor = MagicMock()
    model = _model_with_units_column()

    emitter._create_enriched_view(model, cursor, fact_table="SalesFact")

    executed_sql = cursor.execute.call_args[0][0]
    assert "TOTAL_UNITS_ALL" not in executed_sql
    assert 'SUM("UNITS")' not in executed_sql
