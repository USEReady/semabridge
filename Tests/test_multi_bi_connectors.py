"""Multi-BI and multi-warehouse connector tests.

Tests dynamic BI source ingestion (Tableau .tds / .tdsx / .twb) and target warehouse
emission (Databricks, Snowflake), dynamic source/target configurations, and factory instantiation.
Ensures unsupported formats (like dbt) still refuse with clear, user-readable reasons.
"""

import zipfile
import pytest
from pydantic import ValidationError

from semabridge.connectors.dbt_connector import DBT_UNSUPPORTED_REASON, DbtSemanticConnector
from semabridge.connectors.factory import make_source_extractor, make_target_emitter
from semabridge.connectors.tableau_connector import (
    TABLEAU_UNSUPPORTED_REASON,
    SourceNotSupportedError,
    TableauConnector,
)
from semabridge.core.project import SourceConfig, SourceType, TargetConfig, TargetType
from semabridge.intermediate.models import OSIAggregationType, OSIDataType


SAMPLE_TABLEAU_TDS = """<?xml version='1.0' encoding='utf-8' ?>
<datasource formatted-name='Federated_Sales' inline='true' version='18.1'>
  <connection class='federated'>
    <cols>
      <map key='[Orders].[OrderID]' value='[Orders].[OrderID]' />
      <map key='[Orders].[Sales]' value='[Orders].[Sales]' />
      <map key='[Orders].[CustomerID]' value='[Orders].[CustomerID]' />
      <map key='[Customers].[CustomerID]' value='[Customers].[CustomerID]' />
      <map key='[Customers].[Region]' value='[Customers].[Region]' />
    </cols>
    <relation join='inner' type='join'>
      <clause type='join'>
        <expression op='='>
          <expression op='[Orders].[CustomerID]' />
          <expression op='[Customers].[CustomerID]' />
        </expression>
      </clause>
      <relation name='Orders' table='ORDERS_FACT' type='table' />
      <relation name='Customers' table='CUSTOMERS_DIM' type='table' />
    </relation>
  </connection>
  <column name='[OrderID]' datatype='integer' role='dimension' />
  <column name='[CustomerID]' datatype='integer' role='dimension' />
  <column name='[Sales]' datatype='real' role='measure' />
  <column name='[Region]' datatype='string' role='dimension' />
  <column caption='Total Revenue' name='[Calculation_Rev]' datatype='real' role='measure'>
    <calculation class='tableau' formula='SUM([Sales])' />
  </column>
  <column caption='Fixed Regional Revenue' name='[Calculation_LOD]' datatype='real' role='measure'>
    <calculation class='tableau' formula='{ FIXED [Region] : SUM([Sales]) }' />
  </column>
  <column caption='Distinct Customers' name='[Calculation_Cust]' datatype='integer' role='measure'>
    <calculation class='tableau' formula='COUNTD([CustomerID])' />
  </column>
</datasource>
"""


def test_dbt_connector_refuses_with_reason():
    """Unsupported sources like dbt must refuse with a clear reason."""
    with pytest.raises(SourceNotSupportedError) as exc:
        DbtSemanticConnector({"semantic_models": [], "metrics": []}).extract_osi_model("M")
    assert exc.value.reason == DBT_UNSUPPORTED_REASON
    assert isinstance(exc.value, NotImplementedError)


def test_source_config_rejects_dbt_clearly():
    """SourceConfig still rejects dbt."""
    with pytest.raises(ValidationError) as exc:
        SourceConfig(type="dbt")
    assert "not supported yet" in str(exc.value)


def test_tableau_connector_extracts_osi_model(tmp_path):
    """TableauConnector parses TDS XML and produces a valid, rich OSIModel."""
    tds = tmp_path / "sample.tds"
    tds.write_text(SAMPLE_TABLEAU_TDS)

    connector = TableauConnector(str(tds))
    osi = connector.extract_osi_model()

    assert osi.unique_name == "Federated_Sales"
    assert osi.source_platform == "tableau"

    # Datasets
    ds_names = {d.unique_name for d in osi.datasets}
    assert "Orders" in ds_names
    assert "Customers" in ds_names

    orders_ds = next(d for d in osi.datasets if d.unique_name == "Orders")
    assert orders_ds.source_table == "ORDERS_FACT"
    order_cols = {c.unique_name: c.data_type for c in orders_ds.columns}
    assert order_cols["OrderID"] == OSIDataType.INTEGER
    assert order_cols["CustomerID"] == OSIDataType.INTEGER

    # Relationships
    assert len(osi.relationships) == 1
    rel = osi.relationships[0]
    assert rel.from_dataset == "Orders"
    assert rel.from_columns == ["CustomerID"]
    assert rel.to_dataset == "Customers"
    assert rel.to_columns == ["CustomerID"]

    # Metrics & Calculations transpiled
    metrics_by_name = {m.label: m for m in osi.metrics}
    assert "Total Revenue" in metrics_by_name
    assert "SUM" in metrics_by_name["Total Revenue"].expression
    assert metrics_by_name["Total Revenue"].aggregation == OSIAggregationType.SUM

    assert "Fixed Regional Revenue" in metrics_by_name
    lod_expr = metrics_by_name["Fixed Regional Revenue"].expression
    assert "OVER (PARTITION BY" in lod_expr
    assert '"Region"' in lod_expr

    assert "Distinct Customers" in metrics_by_name
    distinct_expr = metrics_by_name["Distinct Customers"].expression
    assert "COUNT(DISTINCT" in distinct_expr
    assert metrics_by_name["Distinct Customers"].aggregation == OSIAggregationType.COUNT_DISTINCT


def test_tableau_connector_extracts_from_packaged_archive(tmp_path):
    """TableauConnector reads inside packaged .tdsx / .twbx zip archives."""
    tdsx_file = tmp_path / "sales.tdsx"
    with zipfile.ZipFile(tdsx_file, "w") as z:
        z.writestr("packaged_data.tds", SAMPLE_TABLEAU_TDS)

    connector = TableauConnector(str(tdsx_file))
    osi = connector.extract_osi_model("PackagedSales")
    assert osi.unique_name == "PackagedSales"
    assert len(osi.datasets) >= 2


def test_tableau_connector_contract_methods():
    """TableauConnector implements BaseExtractor methods (extract_to_osi, extract)."""
    connector = TableauConnector(SAMPLE_TABLEAU_TDS)
    osi = connector.extract_to_osi("DirectXML")
    assert osi.unique_name == "DirectXML"

    dump = connector.extract("DirectXML")
    assert isinstance(dump, dict)
    assert dump["unique_name"] == "DirectXML"
    assert len(dump["datasets"]) >= 1


def test_source_and_target_configs_dynamic():
    """SourceConfig and TargetConfig accept dynamic BI and warehouse targets."""
    # Tableau source
    src = SourceConfig(type="tableau", path="/path/to/workbook.twb", model="*Sales*")
    assert src.type == SourceType.TABLEAU
    assert src.matches_model("GlobalSales")

    # Databricks targets
    tgt_dbx = TargetConfig(type="databricks", catalog="main", schema="analytics")
    assert tgt_dbx.type == TargetType.DATABRICKS
    assert tgt_dbx.catalog == "main"

    tgt_mv = TargetConfig(type="databricks_metric_view", catalog="hive_metastore", schema="metrics")
    assert tgt_mv.type == TargetType.DATABRICKS_METRIC_VIEW


def test_factory_connector_instantiation():
    """Factory instantiates TableauExtractor and DatabricksPublisher dynamically."""
    # Source: Tableau
    src_extractor = make_source_extractor("tableau", "/path/to/test.tds")
    assert isinstance(src_extractor, TableauConnector)

    # Target: Databricks
    tgt_emitter = make_target_emitter(
        "databricks",
        {"host": "test.cloud.databricks.com", "warehouse_id": "wh_123", "catalog": "dev", "schema": "test_schema"},
    )
    from semabridge.connectors.databricks_publisher import DatabricksPublisher
    assert isinstance(tgt_emitter, DatabricksPublisher)
    assert tgt_emitter.config.catalog == "dev"
