import os
import tempfile
import pytest
from semabridge.connectors.tableau_connector import TableauConnector
from semabridge.connectors.dbt_connector import DbtSemanticConnector
from semabridge.intermediate.models import OSIDataType, OSIAggregationType


def test_tableau_connector_extraction():
    tableau_xml = """<?xml version='1.0' encoding='utf-8' ?>
<datasource formatted-name='federated.123' inline='true' version='18.1' caption='Sales Model'>
  <connection class='federated'>
    <relation name='ORDERS' table='[ORDERS]' type='table' />
  </connection>
  <column datatype='integer' name='[ORDER_ID]' role='dimension' type='ordinal' caption='Order ID' />
  <column datatype='string' name='[CUSTOMER_NAME]' role='dimension' type='nominal' caption='Customer' />
  <column datatype='real' name='[PROFIT]' role='measure' type='quantitative'>
    <calculation class='tableau' formula='SUM([SALES]) - SUM([EXPENSES])' />
  </column>
</datasource>"""

    with tempfile.NamedTemporaryFile("w", suffix=".tds", delete=False) as f:
        f.write(tableau_xml)
        tmp_path = f.name

    try:
        connector = TableauConnector(tmp_path)
        osi = connector.extract_osi_model("TABLEAU_SALES")

        assert osi.unique_name == "TABLEAU_SALES"
        assert osi.source_platform == "Tableau"
        assert len(osi.datasets) == 1
        assert osi.datasets[0].unique_name == "ORDERS"
        assert len(osi.datasets[0].columns) == 2
        assert len(osi.metrics) == 1
        assert osi.metrics[0].unique_name == "PROFIT"
        assert osi.metrics[0].expression == "SUM([SALES]) - SUM([EXPENSES])"
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_dbt_semantic_connector_extraction():
    dbt_manifest = {
        "semantic_models": [
            {
                "name": "orders",
                "label": "Orders Model",
                "node_relation": {"relation_name": "ANALYTICS.FACT_ORDERS"},
                "entities": [
                    {"name": "order_id", "type": "primary"},
                    {"name": "customer_id", "type": "foreign"}
                ],
                "dimensions": [
                    {"name": "order_status", "type": "categorical", "label": "Status"},
                    {"name": "created_at", "type": "time", "label": "Order Date"}
                ],
                "measures": [
                    {"name": "gross_revenue", "agg": "sum", "expr": "order_amount"}
                ]
            }
        ],
        "metrics": [
            {
                "name": "total_gross_revenue",
                "label": "Total Gross Revenue",
                "description": "Total booked orders in USD",
                "type": "simple",
                "type_params": {
                    "measure": "gross_revenue"
                }
            }
        ]
    }

    connector = DbtSemanticConnector(dbt_manifest)
    osi = connector.extract_osi_model("DBT_ECOMMERCE")

    assert osi.unique_name == "DBT_ECOMMERCE"
    assert osi.source_platform == "dbt"
    assert len(osi.datasets) == 1
    assert osi.datasets[0].unique_name == "ORDERS"
    assert len(osi.datasets[0].columns) == 4
    assert len(osi.metrics) == 1
    assert osi.metrics[0].unique_name == "TOTAL_GROSS_REVENUE"
    assert osi.metrics[0].aggregation == OSIAggregationType.SUM
    assert osi.metrics[0].expression == "SUM(order_amount)"
