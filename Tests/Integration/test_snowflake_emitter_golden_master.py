from pathlib import Path

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.formats.sml.models import (
    SMLAttribute,
    SMLColumn,
    SMLDataset,
    SMLDimension,
    SMLMetric,
    SMLModel,
    SMLRelationship,
    AggregationType,
    Cardinality,
    DataType,
)
from semabridge.intermediate.models import (
    OSIAttribute,
    OSIColumn,
    OSIDataset,
    OSIDimension,
    OSIMetric,
    OSIModel,
    OSIRelationship,
    OSIAggregationType,
    OSICardinality,
    OSIDataType,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "snowflake_emitter"


def build_emitter() -> SnowflakeEmitter:
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


def build_sml_model() -> SMLModel:
    sales_fact = SMLDataset(
        unique_name="SALES_FACT",
        source_table="SALES_FACT",
        columns=[
            SMLColumn(unique_name="CUSTOMER_ID", data_type=DataType.STRING, is_key=True),
            SMLColumn(unique_name="DATE_ID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="STATUS", data_type=DataType.STRING),
            SMLColumn(unique_name="REVENUE", data_type=DataType.FLOAT, is_measure_candidate=True),
            SMLColumn(unique_name="QUANTITY", data_type=DataType.INTEGER, is_measure_candidate=True),
        ],
        is_fact=True,
    )
    date_dim = SMLDataset(
        unique_name="DATE_DIM",
        source_table="DATE_DIM",
        columns=[
            SMLColumn(unique_name="DATE_ID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="YEAR", data_type=DataType.INTEGER),
        ],
        is_fact=False,
    )
    quote_history = SMLDataset(
        unique_name="REP_SFDC_SBQQ_QUOTE_HISTORY",
        source_table="REP_SFDC_SBQQ_QUOTE_HISTORY",
        columns=[
            SMLColumn(unique_name="PARENT_ID", data_type=DataType.STRING),
            SMLColumn(unique_name="CREATED_DATE", data_type=DataType.DATETIME),
            SMLColumn(unique_name="NEWVALUE", data_type=DataType.STRING),
        ],
        is_fact=False,
    )
    return SMLModel(
        unique_name="Client Data",
        label="Client Data",
        datasets=[sales_fact, date_dim, quote_history],
        dimensions=[
            SMLDimension(
                unique_name="Sales Status",
                label="Sales Status",
                attributes=[SMLAttribute(unique_name="Status", dataset="SALES_FACT", dataset_column="STATUS")],
            ),
            SMLDimension(
                unique_name="Date",
                label="Date",
                attributes=[SMLAttribute(unique_name="Year", dataset="DATE_DIM", dataset_column="YEAR")],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Revenue",
                label="Total Revenue",
                dataset="SALES_FACT",
                source_column="REVENUE",
                aggregation=AggregationType.SUM,
            ),
            SMLMetric(
                unique_name="Average Quantity",
                label="Average Quantity",
                dataset="SALES_FACT",
                source_column="QUANTITY",
                aggregation=AggregationType.AVG,
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="REL_SALES_FACT_DATE_ID__DATE_DIM_DATE_ID",
                from_dataset="SALES_FACT",
                from_columns=["DATE_ID"],
                to_dataset="DATE_DIM",
                to_columns=["DATE_ID"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            )
        ],
    )


def build_osi_model() -> OSIModel:
    return OSIModel(
        unique_name="Client Data",
        label="Client Data",
        datasets=[
            OSIDataset(
                unique_name="SALES_FACT",
                source_table="SALES_FACT",
                columns=[
                    OSIColumn(unique_name="CUSTOMER_ID", data_type=OSIDataType.STRING, is_key=True),
                    OSIColumn(unique_name="DATE_ID", data_type=OSIDataType.INTEGER, is_key=True),
                    OSIColumn(unique_name="STATUS", data_type=OSIDataType.STRING),
                    OSIColumn(unique_name="REVENUE", data_type=OSIDataType.FLOAT),
                    OSIColumn(unique_name="QUANTITY", data_type=OSIDataType.INTEGER),
                ],
                is_fact=True,
            ),
            OSIDataset(
                unique_name="DATE_DIM",
                source_table="DATE_DIM",
                columns=[
                    OSIColumn(unique_name="DATE_ID", data_type=OSIDataType.INTEGER, is_key=True),
                    OSIColumn(unique_name="YEAR", data_type=OSIDataType.INTEGER),
                ],
                is_fact=False,
            ),
            OSIDataset(
                unique_name="REP_SFDC_SBQQ_QUOTE_HISTORY",
                source_table="REP_SFDC_SBQQ_QUOTE_HISTORY",
                columns=[
                    OSIColumn(unique_name="PARENT_ID", data_type=OSIDataType.STRING),
                    OSIColumn(unique_name="CREATED_DATE", data_type=OSIDataType.DATETIME),
                    OSIColumn(unique_name="NEWVALUE", data_type=OSIDataType.STRING),
                ],
                is_fact=False,
            ),
        ],
        dimensions=[
            OSIDimension(
                unique_name="Sales Status",
                label="Sales Status",
                dataset="SALES_FACT",
                attributes=[OSIAttribute(unique_name="Status", dataset="SALES_FACT", source_column="STATUS")],
            ),
            OSIDimension(
                unique_name="Date",
                label="Date",
                dataset="DATE_DIM",
                attributes=[OSIAttribute(unique_name="Year", dataset="DATE_DIM", source_column="YEAR")],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="Total Revenue",
                label="Total Revenue",
                dataset="SALES_FACT",
                source_column="REVENUE",
                aggregation=OSIAggregationType.SUM,
            ),
            OSIMetric(
                unique_name="Average Quantity",
                label="Average Quantity",
                dataset="SALES_FACT",
                source_column="QUANTITY",
                aggregation=OSIAggregationType.AVG,
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="REL_SALES_FACT_DATE_ID__DATE_DIM_DATE_ID",
                from_dataset="SALES_FACT",
                from_columns=["DATE_ID"],
                to_dataset="DATE_DIM",
                to_columns=["DATE_ID"],
                cardinality=OSICardinality.MANY_TO_ONE,
                is_active=True,
            )
        ],
    )


def test_generate_ddls_matches_golden_master_sml():
    emitter = build_emitter()
    actual = "\n\n".join(emitter.generate_ddls(build_sml_model()))
    expected = (FIXTURES / "expected_sml_ddl.sql").read_text(encoding="utf-8")
    assert actual == expected


def test_generate_ddls_matches_golden_master_osi():
    emitter = build_emitter()
    actual = "\n\n".join(emitter.generate_ddls_from_osi(build_osi_model()))
    expected = (FIXTURES / "expected_osi_ddl.sql").read_text(encoding="utf-8")
    assert actual == expected
