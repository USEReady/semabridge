from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import (
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
)


def test_generate_sql_statements_creates_single_model_table_with_rows():
    cfg = DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )

    model = SMLModel(
        unique_name="annual",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="region", data_type=DataType.STRING),
                    SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="revenue", dataset="Sales"),
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "SEMABRIDGE_MODEL_SNAPSHOT" not in sql
    assert "CREATE TABLE IF NOT EXISTS `main`.`public`.`annual`" in sql
    assert "'Sales', 'region', 'dimension'" in sql
    assert "'Sales', 'revenue', 'measure'" in sql


def test_generate_sql_statements_batches_row_inserts():
    cfg = DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )

    model = SMLModel(
        unique_name="batch_model",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="region", data_type=DataType.STRING),
                    SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="revenue", dataset="Sales"),
            SMLMetric(unique_name="margin", dataset="Sales"),
        ],
    )

    statements = DatabricksPublisher(cfg).generate_sql_statements(model)
    insert_statements = [s for s in statements if s.startswith("INSERT INTO `main`.`public`.`batch_model`")]

    # One DELETE plus one batched INSERT keeps API calls low.
    assert len(insert_statements) == 1
    assert insert_statements[0].count("current_timestamp()") == 4


def test_generate_sql_statements_uses_int_for_calendar_dimensions():
    cfg = DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )

    model = SMLModel(
        unique_name="continent",
        datasets=[
            SMLDataset(
                unique_name="Date",
                columns=[
                    SMLColumn(unique_name="Year", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Quarter", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Month", data_type=DataType.INTEGER),
                ],
            )
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "'Year', 'dimension', 'INT'" in sql
    assert "'Quarter', 'dimension', 'INT'" in sql
    assert "'Month', 'dimension', 'INT'" in sql


def test_generate_sql_statements_sanitizes_invalid_identifiers():
    cfg = DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )

    model = SMLModel(
        unique_name="continent",
        datasets=[
            SMLDataset(
                unique_name="continent 1",
                columns=[
                    SMLColumn(unique_name="country.name", data_type=DataType.STRING),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="total sales$", dataset="continent 1"),
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "CREATE TABLE IF NOT EXISTS `main`.`public`.`continent`" in sql
    assert "'continent_1', 'country_name', 'dimension'" in sql
    assert "'continent_1', 'total_sales', 'measure'" in sql
