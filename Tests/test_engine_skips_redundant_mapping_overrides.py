"""Regression test: ExecutionEngine.execute() must not re-apply mapping
overrides a second time when Step 6's source-specific conversion
(fabric/pbix/snowflake-semantic-view) already applied them at the OSI
stage.

_apply_mapping_overrides_from_config's own rename guards (`!= target_name`)
already make a second call a no-op wherever it fires today, but it still
unconditionally re-parses the config payload and rebuilds the full
metric-name index over every metric on every run — pure wasted work for
the vast majority of conversions, which go through fabric/pbix/the
snowflake-semantic-view branch and therefore already applied overrides
once. RunContext.mapping_overrides_applied lets the final call in
execute() skip itself when that's already happened, while still firing for
the one path that needs it (the plain-metadata Snowflake fallback, which
never applies overrides at the OSI stage).
"""
from types import SimpleNamespace

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.context import RunContext
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.run_summary import RunStatus


def _stub_engine(monkeypatch, *, mapping_overrides_applied):
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
    context.mapping_overrides_applied = mapping_overrides_applied

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
        # Simulates fabric.py/pbix.py/snowflake.py's semantic-view branch:
        # applies overrides at the OSI stage and sets the flag, exactly
        # like the real conversion functions now do.
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
    return engine, context


def test_skips_final_apply_when_step6_already_applied_overrides(monkeypatch):
    engine, context = _stub_engine(monkeypatch, mapping_overrides_applied=True)

    applied = []
    monkeypatch.setattr(
        ExecutionEngine,
        "_apply_mapping_overrides_from_config",
        staticmethod(lambda sml_model, path, config_payload=None: applied.append(path)),
    )

    result = engine.execute(
        source="fabric",
        target=None,
        config_path="nonexistent-config.yaml",
        config_dict={"mappings_overrides": []},
        dry_run=True,
    )

    assert result.status == RunStatus.SUCCESS
    assert applied == [], (
        "Step 6 already applied overrides at the OSI stage (flag=True); "
        "execute() must not redundantly re-apply them a second time."
    )


def test_still_applies_when_step6_did_not_apply_overrides(monkeypatch):
    """Mirrors the plain-metadata Snowflake fallback path, which never
    applies overrides at the OSI stage — the final call must still fire,
    or overrides would silently stop working for that path entirely."""
    engine, context = _stub_engine(monkeypatch, mapping_overrides_applied=False)

    applied = []
    monkeypatch.setattr(
        ExecutionEngine,
        "_apply_mapping_overrides_from_config",
        staticmethod(
            lambda sml_model, path, config_payload=None, allow_column_rename=True: applied.append(
                (path, allow_column_rename)
            )
        ),
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
    # This is the late fallback (step 6 never set mapping_overrides_applied),
    # which fires after relationships/metrics may already be built against
    # a column's original name -- it must never be allowed to rename a
    # column's unique_name, only its display label. See
    # _apply_mapping_overrides_from_config's own docstring.
    _, allow_column_rename = applied[0]
    assert allow_column_rename is False
