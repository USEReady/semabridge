from __future__ import annotations

from types import SimpleNamespace

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.extraction import pbix as pbix_extraction


class _FakeEngine:
    """Stands in for ExecutionEngine's self in the unbound _extract_pbix
    function -- only needs the one method _extract_pbix actually calls.
    """

    def _record_step(self, step_number, status, message):
        pass


def _stub_connector(monkeypatch, tmsl):
    class _FakeConnector:
        def __init__(self, cfg, *args, **kwargs):
            self.cfg = cfg

        def discover(self):
            return {"raw_tmsl": tmsl, "field_aliases": []}

        def extract(self):
            return tmsl

    # _extract_pbix imports LocalPBIXConnector locally inside the function
    # body, not as a module-level attribute of pbix_extraction -- patch it
    # at its actual source module instead.
    import semabridge.connectors.local_pbix_connector as local_pbix_connector_module

    monkeypatch.setattr(local_pbix_connector_module, "LocalPBIXConnector", _FakeConnector)
    monkeypatch.setattr(
        pbix_extraction, "from_pbix_tmsl", lambda **kwargs: SimpleNamespace(**kwargs)
    )


def _make_context(config):
    return RunContext(
        project_id="proj-multi",
        run_id="run-1",
        config=config,
        start_time=0.0,
        source_type="pbix",
        target_type=None,
        behavior=ConnectorBehavior(),
    )


def test_extract_pbix_writes_resolved_path_back_onto_context_config_source(monkeypatch, tmp_path):
    """Multi-file dispatch (sync_execution_service._run_single_job) calls
    engine.execute() once per PBIX file, each with its own resolved
    pbix_path -- but context.config.source is built once from the shared,
    project-level config at Step 1, so before this fix it still named
    whichever file (or none) that shared config's source.pbix_path pointed
    at. Later in this same run, deployment/snowflake.py's opt-in
    data-backfill reads context.config.source.pbix_path to decide what to
    load from -- so every model in a multi-file first sync would backfill
    from that one shared/wrong path instead of its own file.
    _extract_pbix must write the actually-resolved path back so later
    steps of THIS run see the right file, even when the shared config
    still names a different one.
    """
    pbix_file = tmp_path / "SalesModel.pbix"
    pbix_file.write_bytes(b"stub")
    _stub_connector(monkeypatch, {"model": {"tables": [{"name": "Sales", "measures": []}]}})

    # Shared project config's source names a DIFFERENT file, as it would
    # for a persisted multi-file project -- must not win over the explicit
    # per-job pbix_path.
    other_file = tmp_path / "OtherModel.pbix"
    config = SimpleNamespace(source=SimpleNamespace(pbix_path=str(other_file)))
    context = _make_context(config)

    pbix_extraction._extract_pbix(_FakeEngine(), context, str(pbix_file))

    assert context.config.source.pbix_path == str(pbix_file)


def test_extract_pbix_creates_source_namespace_when_missing(monkeypatch, tmp_path):
    """A run whose config has no source.* at all must still end up with
    the resolved path recorded on context.config.source afterward, not
    skip the write-back because there was nothing there to update.
    """
    pbix_file = tmp_path / "OnlyModel.pbix"
    pbix_file.write_bytes(b"stub")
    _stub_connector(monkeypatch, {"model": {"tables": []}})

    config = SimpleNamespace()  # no .source attribute at all
    context = _make_context(config)

    pbix_extraction._extract_pbix(_FakeEngine(), context, str(pbix_file))

    assert context.config.source.pbix_path == str(pbix_file)


def test_extract_pbix_falls_back_to_existing_source_path_when_no_explicit_arg(monkeypatch, tmp_path):
    """Single-file / legacy callers that never pass an explicit pbix_path
    still resolve from context.config.source as before, and the
    write-back is then just a harmless no-op (same value read back).
    """
    pbix_file = tmp_path / "LegacyModel.pbix"
    pbix_file.write_bytes(b"stub")
    _stub_connector(monkeypatch, {"model": {"tables": []}})

    config = SimpleNamespace(source=SimpleNamespace(pbix_path=str(pbix_file)))
    context = _make_context(config)

    pbix_extraction._extract_pbix(_FakeEngine(), context, None)

    assert context.config.source.pbix_path == str(pbix_file)
