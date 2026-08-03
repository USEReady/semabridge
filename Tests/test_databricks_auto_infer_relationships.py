"""Regression tests for DatabricksPublisher._auto_infer_missing_relationships.

Two independent fixes:
1. Opt-in gate: this fabricates NEW relationships from a pure naming
   heuristic (no independent structural signal available at this layer)
   and used to run unconditionally on every Databricks preflight — now
   gated behind `enable_auto_relationship_inference` (default off),
   matching the sibling `_auto_bridge_relationship_join_keys`'s existing
   opt-in pattern.
2. Whole-word join-key matching: the join-key acceptance check used to
   test substring containment against a fully separator-stripped column
   name (e.g. "Community" -> "community" contains "unit" mid-word),
   which could fabricate a phantom relationship between two unrelated
   datasets that happen to share a coincidentally-named column.

Synthetic placeholder names only.
"""
from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.core.settings import DatabricksConfig
from semabridge.core.behavior import ConnectorBehavior
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, DataType


def _cfg():
    return DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )


def _model_with_shared_community_column():
    """Two unrelated dimension tables that both happen to have a column
    named 'Community' — normalizes to 'community', which contains 'unit'
    as a mid-word substring (comm-UNIT-y), the exact bug shape."""
    return SMLModel(
        unique_name="TestModel",
        datasets=[
            SMLDataset(
                unique_name="Neighborhoods",
                is_fact=True,
                columns=[SMLColumn(unique_name="Community", data_type=DataType.STRING)],
            ),
            SMLDataset(
                unique_name="Developments",
                columns=[SMLColumn(unique_name="Community", data_type=DataType.STRING)],
            ),
        ],
        metrics=[],
    )


def _model_with_real_join_key():
    return SMLModel(
        unique_name="TestModel",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                is_fact=True,
                columns=[SMLColumn(unique_name="CustomerID", data_type=DataType.STRING)],
            ),
            SMLDataset(
                unique_name="Customers",
                columns=[SMLColumn(unique_name="CustomerID", data_type=DataType.STRING)],
            ),
        ],
        metrics=[],
    )


def test_disabled_by_default_no_relationships_fabricated():
    publisher = DatabricksPublisher(_cfg())
    model = _model_with_real_join_key()
    publisher._auto_infer_missing_relationships(model)
    assert model.relationships == []


def test_enabled_still_creates_a_relationship_for_a_real_shared_join_key():
    publisher = DatabricksPublisher(_cfg(), behavior=ConnectorBehavior(
        databricks={"enable_auto_relationship_inference": True}
    ))
    model = _model_with_real_join_key()
    publisher._auto_infer_missing_relationships(model)
    assert len(model.relationships) == 1
    assert model.relationships[0].from_columns == ["CustomerID"]


def test_enabled_does_not_fabricate_relationship_on_coincidental_mid_word_match():
    """The exact bug: 'Community' contains 'unit' as a substring once all
    separators are stripped, but is not a real join key."""
    publisher = DatabricksPublisher(_cfg(), behavior=ConnectorBehavior(
        databricks={"enable_auto_relationship_inference": True}
    ))
    model = _model_with_shared_community_column()
    publisher._auto_infer_missing_relationships(model)
    assert model.relationships == []
