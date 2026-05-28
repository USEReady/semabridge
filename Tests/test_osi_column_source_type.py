from semabridge.intermediate.models import OSIColumn, OSIAttribute, OSIDimension
from semabridge.transformers.official_payload import smlmodel_to_official_payload
from semabridge.sml.models import SMLModel, SMLDataset, SMLMetric


def test_osi_column_accepts_source_type():
    column = OSIColumn(unique_name="id", source_type="INTEGER")
    assert column.source_type == "INTEGER"


def test_official_payload_includes_column_source_type_when_present():
    model = SMLModel(
        unique_name="model",
        datasets=[
            SMLDataset(unique_name="orders", columns=[OSIColumn(unique_name="id", source_type="INTEGER")])
        ],
    )

    payload = smlmodel_to_official_payload(model)
    assert payload["datasets"][0]["columns"][0]["source_type"] == "INTEGER"


def test_official_payload_defaults_missing_metric_confidence():
    model = SMLModel(
        unique_name="model",
        metrics=[
            SMLMetric(unique_name="total_amount", dataset="orders", confidence=None),
        ],
    )

    payload = smlmodel_to_official_payload(model)
    assert payload["metrics"][0]["confidence"] == 1.0


def test_official_payload_dimension_attributes_use_official_source_column():
    model = SMLModel(
        unique_name="model",
        dimensions=[
            OSIDimension(
                unique_name="order_dim",
                dataset="orders",
                attributes=[
                    OSIAttribute(
                        unique_name="order_id",
                        dataset="orders",
                        source_column="ORDER_ID",
                    )
                ],
            )
        ],
    )

    payload = smlmodel_to_official_payload(model)
    attr = payload["dimensions"][0]["attributes"][0]
    assert attr["source_column"] == "ORDER_ID"
    assert "dataset_column" not in attr
