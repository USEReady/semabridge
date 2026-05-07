from types import SimpleNamespace

from semabridge.core.engine.conversion.base import _step6_convert_to_sml
from semabridge.sml.models import (
    DataType,
    SMLAttribute,
    SMLColumn,
    SMLDataset,
    SMLDimension,
    SMLMetric,
    SMLModel,
)


class Step6Harness:
    def __init__(self, source_model: SMLModel):
        self._current_step = 0
        self.source_model = source_model

    def _convert_fabric_to_sml(self, context, workspace_id, dataset_id):
        return self.source_model

    def _normalize_relationships_for_target(self, model):
        return None

    def _record_step(self, *args, **kwargs):
        return None


def _model(name: str, datasets=None, metrics=None, dimensions=None) -> SMLModel:
    return SMLModel(
        unique_name=name,
        datasets=datasets or [],
        metrics=metrics or [],
        dimensions=dimensions or [],
    )


def _dataset(name: str, columns=None) -> SMLDataset:
    return SMLDataset(
        unique_name=name,
        columns=columns or [SMLColumn(unique_name="ID", data_type=DataType.INTEGER)],
    )


def test_step6_upsert_merges_live_target_sml_and_preserves_custom_metric():
    source_model = _model(
        "FabricModel",
        datasets=[_dataset("COL_DATE")],
        metrics=[SMLMetric(unique_name="MEASURE", dataset="COL_DATE", expression="COUNT(*)")],
    )
    target_model = _model(
        "FabricModel",
        datasets=[
            _dataset("COL_DATE"),
            _dataset("CONTINENT_1"),
            _dataset("DIM_DATE"),
        ],
        metrics=[
            SMLMetric(unique_name="MEASURE", dataset="CONTINENT_1", expression="COUNT(*)"),
            SMLMetric(unique_name="CUSTOM_TEST_METRIC", dataset="CONTINENT_1", expression="COUNT(*)"),
        ],
        dimensions=[
            SMLDimension(
                unique_name="CUSTOM_DIMENSION",
                dataset="CONTINENT_1",
                attributes=[
                    SMLAttribute(
                        unique_name="CUSTOM_ATTR",
                        dataset="CONTINENT_1",
                        dataset_column="COLUMN1",
                    )
                ],
            )
        ],
    )
    context = SimpleNamespace(
        source_type="fabric",
        sync_mode="upsert",
        target_sml_model=target_model,
    )

    merged = _step6_convert_to_sml(Step6Harness(source_model), context, None, None)

    assert {dataset.unique_name for dataset in merged.datasets} == {
        "COL_DATE",
        "CONTINENT_1",
        "DIM_DATE",
    }
    assert {metric.unique_name for metric in merged.metrics} == {
        "MEASURE",
        "CUSTOM_TEST_METRIC",
    }
    assert merged.get_metric("MEASURE").dataset == "COL_DATE"
    assert merged.get_dimension("CUSTOM_DIMENSION") is not None


def test_step6_copy_keeps_source_only_behavior():
    source_model = _model(
        "FabricModel",
        datasets=[_dataset("COL_DATE")],
        metrics=[SMLMetric(unique_name="MEASURE", dataset="COL_DATE", expression="COUNT(*)")],
    )
    target_model = _model(
        "FabricModel",
        datasets=[_dataset("COL_DATE"), _dataset("CONTINENT_1")],
        metrics=[
            SMLMetric(unique_name="CUSTOM_TEST_METRIC", dataset="CONTINENT_1", expression="COUNT(*)")
        ],
    )
    context = SimpleNamespace(
        source_type="fabric",
        sync_mode="copy",
        target_sml_model=target_model,
    )

    copied = _step6_convert_to_sml(Step6Harness(source_model), context, None, None)

    assert [dataset.unique_name for dataset in copied.datasets] == ["COL_DATE"]
    assert [metric.unique_name for metric in copied.metrics] == ["MEASURE"]
