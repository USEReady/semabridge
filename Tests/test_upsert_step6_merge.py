from types import SimpleNamespace

import pytest

from semabridge.core.engine.conversion.base import _step6_convert_to_sml, _step6a_apply_upsert_delta
from semabridge.core.engine.exceptions import ConversionError
from semabridge.repository.schemas import Snapshot
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
    def __init__(self, source_model: SMLModel, snapshot=None, lookup_error=None):
        self._current_step = 0
        self.source_model = source_model
        self.db_manager = SimpleNamespace(get_last_deployed_snapshot=self._lookup)
        self._snapshot = snapshot
        self._lookup_error = lookup_error
        self.lookups = []

    def _lookup(self, project_id, model_names):
        self.lookups.append((project_id, list(model_names)))
        if self._lookup_error:
            raise self._lookup_error
        return self._snapshot

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


def _snapshot(model: SMLModel) -> Snapshot:
    return Snapshot(
        snapshot_id="snap-1",
        project_id="proj-1",
        timestamp="2026-10-15 10:00:00",
        sml_blob=model.model_dump(mode="json"),
    )


def _context(sync_mode, model, payload=None):
    return SimpleNamespace(
        source_type="fabric",
        sync_mode=sync_mode,
        project_id="proj-1",
        config_payload=payload,
        sml_model=model,
        upsert_delta=None,
    )


def test_step6_does_not_merge_anything_itself():
    source_model = _model(
        "FabricModel",
        datasets=[_dataset("COL_DATE")],
        metrics=[SMLMetric(unique_name="MEASURE", dataset="COL_DATE", expression="COUNT(*)")],
    )
    context = SimpleNamespace(source_type="fabric", sync_mode="copy")

    converted = _step6_convert_to_sml(Step6Harness(source_model), context, None, None)

    assert [dataset.unique_name for dataset in converted.datasets] == ["COL_DATE"]
    assert [metric.unique_name for metric in converted.metrics] == ["MEASURE"]


def test_step6a_upsert_merges_last_deployed_snapshot_and_preserves_target_only_items():
    source_model = _model(
        "FabricModel",
        datasets=[_dataset("COL_DATE")],
        metrics=[SMLMetric(unique_name="MEASURE", dataset="COL_DATE", expression="COUNT(*)")],
    )
    deployed = _model(
        "FabricModel",
        datasets=[_dataset("COL_DATE"), _dataset("CONTINENT_1")],
        metrics=[
            SMLMetric(unique_name="MEASURE", dataset="CONTINENT_1", expression="COUNT(*)"),
            SMLMetric(unique_name="CUSTOM_TEST_METRIC", dataset="CONTINENT_1", expression="COUNT(*)"),
        ],
        dimensions=[
            SMLDimension(
                unique_name="CUSTOM_DIMENSION",
                dataset="CONTINENT_1",
                attributes=[
                    SMLAttribute(unique_name="CUSTOM_ATTR", dataset="CONTINENT_1", dataset_column="COLUMN1")
                ],
            )
        ],
    )
    harness = Step6Harness(source_model, snapshot=_snapshot(deployed))
    context = _context("upsert", source_model)

    _step6a_apply_upsert_delta(harness, context)

    merged = context.sml_model
    assert {d.unique_name for d in merged.datasets} == {"COL_DATE", "CONTINENT_1"}
    assert {m.unique_name for m in merged.metrics} == {"MEASURE", "CUSTOM_TEST_METRIC"}
    assert merged.get_metric("MEASURE").dataset == "COL_DATE"  # source wins
    assert merged.get_dimension("CUSTOM_DIMENSION") is not None
    delta = context.upsert_delta
    assert delta["has_changes"] is True
    assert [m["name"] for m in delta["kinds"]["metrics"]["modified"]] == ["MEASURE"]
    assert harness.lookups == [("proj-1", ["FabricModel", "FabricModel"])]


def test_step6a_upsert_uses_dry_run_baseline_project():
    source_model = _model("FabricModel", datasets=[_dataset("COL_DATE")])
    harness = Step6Harness(source_model, snapshot=None)
    context = _context("upsert", source_model, payload={"delta_baseline_project_id": "proj-real"})

    _step6a_apply_upsert_delta(harness, context)

    assert harness.lookups[0][0] == "proj-real"
    assert context.upsert_delta["first_deployment"] is True
    assert context.upsert_delta["has_changes"] is True


def test_step6a_upsert_unreadable_snapshot_stops_the_run():
    source_model = _model("FabricModel", datasets=[_dataset("COL_DATE")])
    harness = Step6Harness(source_model, lookup_error=ValueError("Snapshot snap-9 could not be read"))
    context = _context("upsert", source_model)

    with pytest.raises(ConversionError, match="could not be read"):
        _step6a_apply_upsert_delta(harness, context)
    assert context.sml_model is source_model
