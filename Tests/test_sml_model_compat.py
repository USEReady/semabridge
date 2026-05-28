from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn
from semabridge.sml.models import SMLMetric
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import OSIModel, OSIDataset, OSIColumn


def test_sml_model_exposes_count_properties():
    model = SMLModel(
        unique_name="model",
        datasets=[
            SMLDataset(unique_name="orders", columns=[SMLColumn(unique_name="id")]),
            SMLDataset(unique_name="customers", columns=[]),
        ],
        metrics=[],
        dimensions=[],
        relationships=[],
    )

    assert model.dataset_count == 2
    assert model.metric_count == 0
    assert model.dimension_count == 0
    assert model.relationship_count == 0
    assert model.column_count == 1


def test_osi_to_sml_conversion_still_works_with_counts():
    osi = OSIModel(
        unique_name="model",
        datasets=[OSIDataset(unique_name="Date", columns=[OSIColumn(unique_name="id")])],
        metrics=[],
        dimensions=[],
        relationships=[],
    )

    sml = OSIToSMLConverter().from_osi(osi)
    assert sml.dataset_count == 1
    assert sml.metric_count == 0


def test_sml_metric_exposes_folder_for_deployment_paths():
    metric = SMLMetric(
        unique_name="total_revenue",
        dataset="sales",
        folder="Financials",
    )

    assert metric.folder == "Financials"

    model = SMLModel(unique_name="model", metrics=[metric])
    dumped = model.model_dump()

    assert dumped["metrics"][0]["folder"] == "Financials"
