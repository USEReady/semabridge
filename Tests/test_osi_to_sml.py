from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIColumn,
    OSIDataType,
    OSIDataset,
    OSIModel,
    OSIRelationship,
    OSICardinality,
    OSICrossFilterDirection,
    OSIMetric,
    OSIAggregationType,
)


def test_from_osi_auto_detects_metrics_when_source_has_none():
    converter = OSIToSMLConverter()
    osi_model = OSIModel(
        unique_name="client-data",
        label="Client Data",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="FactSales",
                columns=[
                    OSIColumn(unique_name="SaleId", data_type=OSIDataType.INTEGER, is_key=True),
                    OSIColumn(unique_name="CustomerId", data_type=OSIDataType.INTEGER),
                    OSIColumn(unique_name="Revenue", data_type=OSIDataType.FLOAT),
                    OSIColumn(unique_name="Quantity", data_type=OSIDataType.INTEGER),
                ],
            ),
            OSIDataset(
                unique_name="DimCustomer",
                columns=[
                    OSIColumn(unique_name="CustomerId", data_type=OSIDataType.INTEGER, is_key=True),
                    OSIColumn(unique_name="CustomerName", data_type=OSIDataType.STRING),
                ],
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="REL_FACTSALES_CUSTOMERID__DIMCUSTOMER_CUSTOMERID",
                from_dataset="FactSales",
                from_columns=["CustomerId"],
                to_dataset="DimCustomer",
                to_columns=["CustomerId"],
                cardinality=OSICardinality.MANY_TO_ONE,
                cross_filter_direction=OSICrossFilterDirection.SINGLE,
            )
        ],
    )

    sml_model = converter.from_osi(osi_model)

    metric_names = {metric.unique_name for metric in sml_model.metrics}
    assert sml_model.metric_count >= 2
    assert "Factsales - Sum of Revenue" in metric_names
    assert "Factsales - Sum of Quantity" in metric_names


def test_from_osi_keeps_explicit_metrics_without_auto_generating_duplicates():
    converter = OSIToSMLConverter()
    osi_model = OSIModel(
        unique_name="sales-model",
        label="Sales Model",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="Sales",
                columns=[
                    OSIColumn(unique_name="Revenue", data_type=OSIDataType.FLOAT),
                ],
            )
        ],
        metrics=[
            OSIMetric(
                unique_name="Total Revenue",
                label="Total Revenue",
                dataset="Sales",
                expression="SUM([Revenue])",
                aggregation=OSIAggregationType.NONE,
            )
        ],
    )

    sml_model = converter.from_osi(osi_model)

    assert sml_model.metric_count == 1
    assert sml_model.metrics[0].unique_name == "Total Revenue"
