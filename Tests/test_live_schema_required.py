"""A run or dry run whose live Snowflake schema can't be loaded stops with a
clear error. It never falls back to a schema-less build, which would emit
NULL placeholders and drop live-only columns."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from semabridge.connectors.snowflake_emitter import (
    LIVE_SCHEMA_UNAVAILABLE,
    LiveSchemaUnavailableError,
    SnowflakeEmitter,
)
from semabridge.core.drop_ledger import DropLedger
from semabridge.core.engine.deployment import snowflake as deployment
from semabridge.core.engine.exceptions import ConversionError
from semabridge.core.engine.targets.base import _step8_convert_to_target
from semabridge.core.engine.targets.snowflake import _convert_to_snowflake_target
from semabridge.core.settings import SnowflakeConfig


def _emitter(database: str = None):
    db = database or f"DB_{uuid.uuid4().hex[:8]}"
    return SnowflakeEmitter(SnowflakeConfig(account="acct", user="u", warehouse="WH", database=db))


def _model(name: str = None):
    unique = name or f"model_{uuid.uuid4().hex[:8]}"
    return SimpleNamespace(unique_name=unique, label=unique, datasets=[], metrics=[], relationships=[])


def _no_connection(*_a, **_k):
    raise RuntimeError("Password auth selected but SNOWFLAKE_PASSWORD is not set.")


def _placeholders(records):
    return [r for r in records if "placeholder" in str(r.get("reason") or "")]


def _forbid_ddl_build(emitter, monkeypatch):
    def _fail(model):
        raise AssertionError("DDL must not be built without the live schema")

    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls", _fail)


def test_no_connection_stops_ddl_build(monkeypatch):
    emitter = _emitter()
    monkeypatch.setattr(emitter.connection_manager, "get_connection", _no_connection)
    _forbid_ddl_build(emitter, monkeypatch)

    with pytest.raises(LiveSchemaUnavailableError, match=LIVE_SCHEMA_UNAVAILABLE):
        emitter.generate_ddls_live(_model())
    assert _placeholders(emitter.drop_ledger.to_json()) == []
    assert emitter.drop_ledger.to_json() == []


def test_empty_live_schema_stops_ddl_build(monkeypatch):
    db_name = f"DB_{uuid.uuid4().hex[:8]}"
    emitter = _emitter(database=db_name)
    conn = MagicMock()
    monkeypatch.setattr(emitter.connection_manager, "get_connection", lambda: (conn, True))
    monkeypatch.setattr(emitter.schema_manager, "refresh_schema_cache", lambda: None)
    monkeypatch.setattr(emitter.schema_manager, "_fetch_schema_metadata", lambda cur: {})
    monkeypatch.setattr(emitter.schema_manager, "_fetch_model_table_metadata", lambda cur, ds: {})
    _forbid_ddl_build(emitter, monkeypatch)

    with pytest.raises(LiveSchemaUnavailableError, match="no tables found in DB"):
        emitter.generate_ddls_live(_model())
    assert emitter.drop_ledger.to_json() == []
    conn.close.assert_called_once()


def _context(project_id: str = None, database: str = None):
    pid = project_id or f"proj_{uuid.uuid4().hex[:8]}"
    db = database or f"DB_{uuid.uuid4().hex[:8]}"
    return SimpleNamespace(
        target_type="snowflake",
        config=SimpleNamespace(
            snowflake=SnowflakeConfig(account="acct", user="u", warehouse="WH", database=db),
            targets=[],
            target=None,
        ),
        behavior=None,
        sml_model=_model(),
        drop_ledger=DropLedger(),
        config_payload={},
        project_id=pid,
        physical_namespace=None,
        dry_run=False,
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_run_without_live_schema_fails_step8_with_no_placeholders(monkeypatch, dry_run):
    """Sync and dry run (Copy or Upsert alike) share Step 8: it fails, and
    nothing is recorded as a placeholder or a dropped column."""
    monkeypatch.setattr(
        "semabridge.connectors.connection_manager.SnowflakeConnectionManager.get_connection",
        _no_connection,
    )
    steps = []
    engine = SimpleNamespace(
        _current_step=0,
        _record_step=lambda n, status, msg=None: steps.append((n, str(status), msg)),
    )
    engine._convert_to_snowflake_target = lambda ctx: _convert_to_snowflake_target(engine, ctx)
    context = _context()
    context.dry_run = dry_run

    with pytest.raises(ConversionError, match=LIVE_SCHEMA_UNAVAILABLE):
        _step8_convert_to_target(engine, context)

    assert steps and steps[-1][0] == 8 and "FAILED" in steps[-1][1].upper()
    assert LIVE_SCHEMA_UNAVAILABLE in steps[-1][2]
    assert context.drop_ledger.to_json() == []
    assert not getattr(context, "staged_target_artifact_paths", None)


def test_dry_run_binding_preview_raises_instead_of_reporting(monkeypatch):
    monkeypatch.setattr(
        "semabridge.connectors.connection_manager.SnowflakeConnectionManager.get_connection",
        _no_connection,
    )
    context = _context()
    with pytest.raises(LiveSchemaUnavailableError, match=LIVE_SCHEMA_UNAVAILABLE):
        deployment.preview_table_bindings(context)
