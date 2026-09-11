"""Regression test: ExecutionEngine.execute() must not crash when no
on-disk config file is available (config_path=None), a real, reachable
case for a pure API/DB-config-driven run (see
api/services/sync_execution_service.py's get_default_config_path(),
which returns None whenever no project YAML resolves from CWD).

Step 6 used to call `Path(config_path)` unconditionally right after SML
conversion — every other call site of the same override helper
(core/engine/conversion/{snowflake,fabric,pbix}.py) already guards this
with `if config_path is not None`, but this one direct call in
execute() didn't. Since execute()'s outer try/except swallows any
exception into a FAILED RunSummary rather than propagating it, the bug
didn't look like a crash to callers — it looked like an opaque failed
run for a reason unrelated to the customer's actual model/config.
"""
from types import SimpleNamespace

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.context import RunContext
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.run_summary import RunStatus


def _stub_engine(monkeypatch, *, config_path):
    engine = ExecutionEngine()

    context = RunContext(
        project_id="demo-project",
        run_id="run-1",
        config=SimpleNamespace(),
        start_time=0.0,
        source_type="fabric",
        target_type=None,
        behavior=ConnectorBehavior(),
    )

    monkeypatch.setattr(
        ExecutionEngine, "_step1_load_config", lambda self, *a, **kw: SimpleNamespace()
    )
    monkeypatch.setattr(
        ExecutionEngine, "_step2_init_identifiers", lambda self, *a, **kw: context
    )
    monkeypatch.setattr(ExecutionEngine, "_step3_resolve_auth", lambda self, *a, **kw: None)
    monkeypatch.setattr(
        ExecutionEngine, "_step4_extract", lambda self, *a, **kw: SimpleNamespace()
    )
    monkeypatch.setattr(ExecutionEngine, "_step5_validate_source", lambda self, *a, **kw: None)
    monkeypatch.setattr(
        ExecutionEngine,
        "_step6_convert_to_sml",
        lambda self, *a, **kw: SimpleNamespace(datasets=[], metrics=[]),
    )
    monkeypatch.setattr(
        ExecutionEngine, "_step7_persist_artifacts", lambda self, *a, **kw: None
    )
    monkeypatch.setattr(
        ExecutionEngine,
        "_step10_finalize",
        lambda self, ctx, status: SimpleNamespace(status=status, context=ctx),
    )
    return engine


def test_execute_does_not_crash_on_step6_when_config_path_is_none(monkeypatch):
    """The regression: config_path=None (no on-disk project YAML) must
    not produce a FAILED run due to Path(None) — a purely-internal
    TypeError with nothing to do with the customer's model/config."""
    engine = _stub_engine(monkeypatch, config_path=None)

    result = engine.execute(
        source="fabric",
        target=None,
        config_path=None,
        config_dict={"mappings_overrides": []},
        dry_run=True,
    )

    assert result.status == RunStatus.SUCCESS


def test_execute_still_applies_overrides_when_config_path_is_provided(monkeypatch):
    """The fix must not skip applying overrides when a real config_path
    IS available — only guard the None case."""
    engine = _stub_engine(monkeypatch, config_path="nonexistent-config.yaml")

    applied = []
    monkeypatch.setattr(
        ExecutionEngine,
        "_apply_mapping_overrides_from_config",
        staticmethod(lambda sml_model, path, config_payload=None, allow_column_rename=True: applied.append(path)),
    )

    result = engine.execute(
        source="fabric",
        target=None,
        config_path="nonexistent-config.yaml",
        config_dict={"mappings_overrides": []},
        dry_run=True,
    )

    assert result.status == RunStatus.SUCCESS
    assert len(applied) == 1
    assert str(applied[0]) == "nonexistent-config.yaml"
