"""The enriched-view existence check identifies a missing view's dataset with
the SAME naming function the view was created with -- no guessing from the
view name -- in single-mode and multi-mode (prefixed) projects alike."""

from types import SimpleNamespace

import pytest

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig

DATASETS = ["fact_x", "fact_x_y", "fact_x_y_z", "table_set_one"]


def _emitter(namespace=None):
    cfg = SnowflakeConfig(account="acc", user="usr", warehouse="wh", database="db_x", schema_name="schema_x")
    return SnowflakeEmitter(config=cfg, behavior=ConnectorBehavior(), physical_namespace=namespace)


def _model():
    return SimpleNamespace(datasets=[SimpleNamespace(unique_name=name) for name in DATASETS])


@pytest.mark.parametrize("namespace", [None, "MODEL_A"])
def test_check_maps_every_created_view_back_to_its_own_dataset(namespace):
    emitter, model = _emitter(namespace), _model()
    for dataset in DATASETS:
        created = emitter._enriched_view_name(dataset)
        assert emitter._dataset_for_enriched_view(model, created) == dataset
        # Case-insensitive, quoted or not -- as it appears in generated DDL.
        assert emitter._dataset_for_enriched_view(model, f'"{created.upper()}"') == dataset


def test_underscored_fact_names_are_not_truncated():
    # The removed heuristic cut at the last underscore ("fact_x_y" -> "fact_x"),
    # falling back the wrong dataset.
    emitter = _emitter()
    assert emitter._dataset_for_enriched_view(_model(), "FACT_X_Y_ENRICHED") == "fact_x_y"


def test_prefixed_views_are_recognised_in_multi_mode_projects():
    emitter = _emitter("MODEL_A")
    assert emitter._enriched_view_name("fact_x") == "MODEL_A_fact_x_ENRICHED"
    assert emitter._dataset_for_enriched_view(_model(), "MODEL_A_FACT_X_ENRICHED") == "fact_x"


def test_views_recorded_at_creation_are_used_first():
    emitter = _emitter()
    emitter._enriched_view_mapping["fact_x"] = "SOME_OTHER_NAME_ENRICHED"
    assert emitter._dataset_for_enriched_view(_model(), "some_other_name_enriched") == "fact_x"


def test_unknown_view_matches_no_dataset():
    assert _emitter()._dataset_for_enriched_view(_model(), "UNRELATED_ENRICHED") is None
