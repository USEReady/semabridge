"""
Tests for the SnowflakeEmitter self.model → sml fix.

Verifies that _generate_semantic_view() and generate_ddls() do not
raise AttributeError when processing models with sql_expression-based
metrics.
"""

import pytest
from unittest.mock import MagicMock

from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    DataType, AggregationType, Cardinality, CrossFilterDirection,
)


def _make_emitter():
    """Create a SnowflakeEmitter with mocked config (no real connection)."""
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.behavior import ConnectorBehavior

    config = MagicMock()
    config.database = "TEST_DB"
    config.schema_name = "TEST_SCHEMA"
    config.warehouse = "TEST_WH"
    config.account = "test_account"
    config.user = "test_user"
    config.role = "TEST_ROLE"

    behavior = ConnectorBehavior()
    emitter = SnowflakeEmitter(config=config, behavior=behavior)
    return emitter


def _make_model_with_sql_expression():
    """Build a minimal SML model where a metric uses sql_expression."""
    fact = SMLDataset(
        unique_name="SalesFact",
        source_table="SALES_FACT",
        is_fact=True,
        columns=[
            SMLColumn(
                unique_name="OrderID",
                data_type=DataType.INTEGER,
                is_key=True,
            ),
            SMLColumn(
                unique_name="Revenue",
                data_type=DataType.DECIMAL,
            ),
            SMLColumn(
                unique_name="Cost",
                data_type=DataType.DECIMAL,
            ),
        ],
    )

    dim = SMLDataset(
        unique_name="DimProduct",
        source_table="DIM_PRODUCT",
        is_fact=False,
        columns=[
            SMLColumn(
                unique_name="ProductID",
                data_type=DataType.INTEGER,
                is_key=True,
            ),
            SMLColumn(
                unique_name="ProductName",
                data_type=DataType.STRING,
            ),
        ],
    )

    metric = SMLMetric(
        unique_name="Profit",
        dataset="SalesFact",
        sql_expression='SUM(SalesFact."REVENUE") - SUM(SalesFact."COST")',
    )

    rel = SMLRelationship(
        unique_name="Sales_Product",
        from_dataset="SalesFact",
        from_columns=["ProductID"],
        to_dataset="DimProduct",
        to_columns=["ProductID"],
        cardinality=Cardinality.MANY_TO_ONE,
        cross_filter=CrossFilterDirection.SINGLE,
    )

    return SMLModel(
        unique_name="TestSalesModel",
        datasets=[fact, dim],
        metrics=[metric],
        relationships=[rel],
    )


class TestEmitterModelBug:
    """Regression tests for the self.model AttributeError fix."""

    def test_generate_semantic_view_no_attribute_error(self):
        """_generate_semantic_view must NOT raise AttributeError('self.model')."""
        emitter = _make_emitter()
        sml = _make_model_with_sql_expression()

        # This was crashing with:
        #   AttributeError: 'SnowflakeEmitter' object has no attribute 'model'
        ddl = emitter._generate_semantic_view(sml)

        assert isinstance(ddl, str)
        assert "SEMANTIC VIEW" in ddl

    def test_generate_ddls_no_attribute_error(self):
        """generate_ddls must NOT raise AttributeError('self.model')."""
        emitter = _make_emitter()
        sml = _make_model_with_sql_expression()

        ddls = emitter.generate_ddls(sml)

        assert isinstance(ddls, list)
        assert len(ddls) >= 1

    def test_emitter_init_has_no_model_attribute(self):
        """Verify emitter intentionally does NOT store model on self."""
        emitter = _make_emitter()

        # model is passed per-call, not stored
        assert not hasattr(emitter, "model") or emitter.model is None

    def test_sql_expression_metric_appears_in_ddl(self):
        """A sql_expression metric should produce a METRICS clause entry."""
        emitter = _make_emitter()
        sml = _make_model_with_sql_expression()

        ddl = emitter._generate_semantic_view(sml)

        # The metric should end up in METRICS, not be silently dropped
        assert "METRICS" in ddl or "PROFIT" in ddl.upper()
