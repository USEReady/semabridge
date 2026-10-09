"""Models of one multi-model project run concurrently: each must stage its own
DDL/YAML (and get its own canonical output path), while a single-model project
keeps exactly its previous staging and output paths."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from semabridge.core.engine import targets as _targets_pkg  # noqa: F401  (package import)
from semabridge.core.engine.engine import ExecutionEngine
from semabridge.core.engine.targets import snowflake as tgt


class _FakeEmitter:
    def __init__(self, config, behavior=None, physical_namespace=None):
        self.physical_namespace = physical_namespace
        self.drop_ledger = None

    def generate_ddls(self, sml):
        return [f"-- ddl for {getattr(sml, 'unique_name', None) or self.physical_namespace or 'single'}"]

    def generate_cortex_yaml(self, sml):
        return f"name: {getattr(sml, 'unique_name', None) or self.physical_namespace or 'single'}\n"


class _FakeModel:
    def __init__(self, name="model_x"):
        self.unique_name = name
        self.label = name


def _run(monkeypatch, namespace):
    import semabridge.connectors.snowflake_emitter as emitter_mod

    monkeypatch.setattr(emitter_mod, "SnowflakeEmitter", _FakeEmitter)
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._context = None
    context = SimpleNamespace(
        config=SimpleNamespace(snowflake=None),
        behavior=None,
        physical_namespace=namespace,
        source_type="pbix",
        sml_model=_FakeModel(namespace),
        project_id="proj_x",
        drop_ledger=None,
    )
    tgt._convert_to_snowflake_target(engine, context)
    return context.staged_target_artifact_paths


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_each_model_of_a_project_stages_to_its_own_directory(monkeypatch, in_tmp):
    paths_a = _run(monkeypatch, "MODEL_A")
    paths_b = _run(monkeypatch, "MODEL_B")

    assert paths_a["ddl"] != paths_b["ddl"]
    assert paths_a["yaml"] != paths_b["yaml"]
    assert paths_a["final_ddl"] != paths_b["final_ddl"]
    # Each staged file still holds its own model's content after the sibling ran.
    assert "MODEL_A" in Path(paths_a["ddl"]).read_text(encoding="utf-8")
    assert "MODEL_B" in Path(paths_b["ddl"]).read_text(encoding="utf-8")


def test_single_model_project_keeps_its_previous_paths(monkeypatch, in_tmp):
    paths = _run(monkeypatch, None)
    assert Path(paths["ddl"]) == Path("output/reverse/.staging/proj_x/semantic_view.sql")
    assert Path(paths["final_ddl"]) == Path("output/reverse/proj_x/semantic_view.sql")
