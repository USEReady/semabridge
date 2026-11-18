"""Verified queries must round-trip through the stored SML snapshot.

Regression: both SML serializers dropped ``verified_queries``, so
core/upsert_delta.compute_delta saw every verified query as "added" against
the model's own snapshot and an unchanged model redeployed on every sync.
"""

import pytest

from semabridge.core.upsert_delta import compute_delta, parse_baseline_blob
from semabridge.formats.sml.serializer import SMLSerializer as FormatsSMLSerializer
from semabridge.repository.model_repository import _deserialize_sml_blob, _serialize_sml_blob
from semabridge.sml.models import DataType, SMLColumn, SMLDataset, SMLMetric, SMLModel
from semabridge.sml.serializer import SMLSerializer


VERIFIED_QUERIES = [
    {
        "name": "total_units_by_year",
        "question": "What were total units by year?",
        "sql": "SELECT YEAR, SUM(UNITS) FROM SALES GROUP BY YEAR",
        "is_onboarding": True,
    },
    {
        "name": "revenue_total",
        "question": "What is total revenue?",
        "sql": "SELECT SUM(REVENUE) FROM SALES",
    },
]


def _model() -> SMLModel:
    return SMLModel(
        unique_name="Sales",
        datasets=[
            SMLDataset(unique_name="SalesFact", columns=[
                SMLColumn(unique_name="Units", data_type=DataType.INTEGER),
                SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
            ]),
        ],
        metrics=[SMLMetric(unique_name="Total Units", dataset="SalesFact", expression="SUM([Units])")],
        verified_queries=[dict(vq) for vq in VERIFIED_QUERIES],
    )


@pytest.mark.parametrize("serializer", [SMLSerializer, FormatsSMLSerializer])
def test_yaml_round_trip_keeps_verified_queries(serializer):
    loaded = serializer.from_yaml(serializer.to_yaml(_model()))
    assert loaded.verified_queries == VERIFIED_QUERIES


@pytest.mark.parametrize("serializer", [SMLSerializer, FormatsSMLSerializer])
def test_folder_round_trip_keeps_verified_queries(serializer, tmp_path):
    folder = tmp_path / "model_folder"
    serializer._save_folder(_model(), folder)
    loaded = serializer._load_folder(folder)
    assert loaded.verified_queries == VERIFIED_QUERIES


def test_unchanged_model_with_verified_queries_has_no_changes_against_own_snapshot():
    model = _model()
    stored = _serialize_sml_blob(model.model_dump(mode="json"))
    baseline = parse_baseline_blob(_deserialize_sml_blob(stored), "snap-1")

    delta = compute_delta(_model(), baseline, baseline_snapshot_id="snap-1")

    assert delta["has_changes"] is False
    assert delta["kinds"]["verified_queries"]["added"] == []
