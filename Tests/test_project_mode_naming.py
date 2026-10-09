"""Per-model naming is driven by the project MODE chosen at creation and the
view names saved then -- never by how many models a run contains."""

import itertools

import pytest
import yaml

from semabridge.api.services import sync_execution_service as ses
from semabridge.api.services.project_projects_impl import _normalize_project_config_yaml
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.exceptions import ConnectorError
from semabridge.core.settings import SnowflakeConfig
from semabridge.utils.project_models import (
    assign_model_view_names,
    assign_physical_namespaces,
    project_mode,
)


def _files(tmp_path, *names):
    paths = []
    for name in names:
        path = tmp_path / f"{name}.pbix"
        path.write_bytes(b"x")
        paths.append(str(path))
    return paths


def _cfg(models, mode=None, model_views=None):
    source = {"type": "pbix", "models": list(models)}
    if mode:
        source["mode"] = mode
    if model_views is not None:
        source["model_views"] = dict(model_views)
    return {"source": source, "targets": [{"type": "snowflake"}]}


# --- single mode: unchanged behaviour ---------------------------------------

def test_projects_without_a_mode_are_single_mode():
    assert project_mode({}) == "single"
    assert project_mode({"mode": "MULTIPLE"}) == "multiple"
    assert project_mode({"mode": "anything"}) == "single"


def test_single_mode_project_with_several_models_gets_no_prefix_and_no_view_override(tmp_path):
    jobs, *_ = ses._build_sync_jobs(_cfg(_files(tmp_path, "model_a", "model_b")))
    assert [j["physical_namespace"] for j in jobs] == [None, None]
    assert [j["resolved_display_name"] for j in jobs] == [None, None]


# --- multi mode: saved names, stable across runs ----------------------------

def test_multi_mode_uses_saved_view_names_and_derives_prefixes_from_them(tmp_path):
    a, b = _files(tmp_path, "model_a", "model_b")
    cfg = _cfg([a, b], mode="multiple", model_views={a: "VIEW_ONE", b: "VIEW_TWO"})
    jobs, *_ = ses._build_sync_jobs(cfg)
    assert [j["resolved_display_name"] for j in jobs] == ["VIEW_ONE", "VIEW_TWO"]
    assert [j["physical_namespace"] for j in jobs] == ["VIEW_ONE", "VIEW_TWO"]


def test_removing_a_model_never_renames_the_remaining_ones(tmp_path):
    a, b, c = _files(tmp_path, "model_a", "model_b", "model_c")
    views = assign_model_view_names({"type": "pbix", "models": [a, b, c]})
    full, *_ = ses._build_sync_jobs(_cfg([a, b, c], "multiple", views))
    reduced, *_ = ses._build_sync_jobs(_cfg([a, c], "multiple", views))
    by_path_full = {j["pbix_path"]: (j["resolved_display_name"], j["physical_namespace"]) for j in full}
    for job in reduced:
        assert (job["resolved_display_name"], job["physical_namespace"]) == by_path_full[job["pbix_path"]]


def test_a_multi_mode_project_edited_down_to_one_model_keeps_its_prefix(tmp_path):
    (a,) = _files(tmp_path, "model_a")
    jobs, *_ = ses._build_sync_jobs(_cfg([a], "multiple", {a: "MODEL_A"}))
    assert jobs[0]["physical_namespace"] == "MODEL_A"


def test_view_names_are_assigned_once_and_new_models_avoid_existing_names():
    first = assign_model_view_names({"type": "pbix", "models": ["C:/x/model one.pbix", "C:/y/model one.pbix"]})
    assert list(first.values()) == ["MODEL_ONE", "MODEL_ONE_2"]
    later = assign_model_view_names({
        "type": "pbix",
        "models": ["C:/x/model one.pbix", "C:/y/model one.pbix", "C:/z/model one.pbix"],
        "model_views": first,
    })
    assert later["C:/x/model one.pbix"] == "MODEL_ONE"
    assert later["C:/y/model one.pbix"] == "MODEL_ONE_2"
    assert later["C:/z/model one.pbix"] == "MODEL_ONE_3"


def test_saving_the_project_config_records_view_names_only_for_multi_mode():
    multi = yaml.safe_dump({"source": {"type": "pbix", "mode": "multiple", "models": ["C:/a/model_a.pbix"]}})
    single = yaml.safe_dump({"source": {"type": "pbix", "models": ["C:/a/model_a.pbix"]}})
    saved_multi = yaml.safe_load(_normalize_project_config_yaml("proj-1", multi, "proj"))
    saved_single = yaml.safe_load(_normalize_project_config_yaml("proj-2", single, "proj"))
    assert saved_multi["source"]["model_views"] == {"C:/a/model_a.pbix": "MODEL_A"}
    assert "model_views" not in saved_single["source"]
    # Re-saving never changes an existing name.
    edited = dict(saved_multi)
    edited["source"]["model_views"] = {"C:/a/model_a.pbix": "CUSTOM_NAME"}
    resaved = yaml.safe_load(_normalize_project_config_yaml("proj-1", yaml.safe_dump(edited), "proj"))
    assert resaved["source"]["model_views"]["C:/a/model_a.pbix"] == "CUSTOM_NAME"


# --- generated physical names can never clash -------------------------------

@pytest.mark.parametrize("views", [
    ["A", "A_B"],
    ["A_B", "A"],
    ["X", "X_2", "X_2_3"],
    ["MODEL", "MODEL", "MODEL"],
    ["TABLE_SET", "TABLE", "TABLE_SET_2"],
])
def test_physical_prefixes_are_unique_and_never_prefix_each_other(views):
    namespaces = assign_physical_namespaces(views)
    assert len(set(n.upper() for n in namespaces)) == len(views)
    for left, right in itertools.permutations(namespaces, 2):
        assert not right.upper().startswith(f"{left.upper()}_")


def test_prefixed_object_names_from_different_models_cannot_collide():
    namespaces = assign_physical_namespaces(["A", "A_B"])
    tables = ["B_C", "C", "B", "C_D"]
    produced = [f"{ns}_{table}" for ns in namespaces for table in tables]
    assert len(set(produced)) == len(produced)


# --- emitter guards -----------------------------------------------------------

def _emitter(namespace):
    cfg = SnowflakeConfig(account="acc", user="usr", password="pwd", warehouse="wh",
                          database="db_x", schema_name="schema_x")
    return SnowflakeEmitter(config=cfg, behavior=ConnectorBehavior(), physical_namespace=namespace)


def test_single_mode_emitter_never_prefixes():
    assert _emitter(None)._namespaced_physical_name("table_x") == "table_x"


def test_prefixed_name_over_the_identifier_limit_fails_clearly():
    with pytest.raises(ConnectorError, match="identifier limit"):
        _emitter("MODEL_A")._namespaced_physical_name("T" * 250)


def test_physical_namespace_scope_preserves_source_table():
    from types import SimpleNamespace

    model = SimpleNamespace(datasets=[SimpleNamespace(source_table="MODEL_A_TABLE", unique_name="MODEL_A_TABLE")])
    emitter = _emitter("MODEL_A")
    with emitter._physical_namespace_scope(model):
        pass
    assert model.datasets[0].source_table == "MODEL_A_TABLE"
