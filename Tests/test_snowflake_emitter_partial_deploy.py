"""Regression test for the mid-DDL-loop partial-deploy-visibility fix.

Snowflake DDL in _execute_deployment_pipeline's Step 3 is not wrapped in a
single transaction: each CREATE/DROP/ALTER statement commits independently.
Before this fix, a failure partway through that loop (after >=1 statement
already succeeded) was reported through the exact same `last_deployment_error
= str(exc)` / plain "FAILED" path as a deploy that never touched Snowflake at
all -- there was no way for a caller (or a human) to tell "nothing happened,
safe to just retry" apart from "some objects now exist/changed in Snowflake,
verify before retrying".

This test injects a crash on the SECOND of two DDL statements (with an error
message that doesn't match the auto-remediation "invalid identifier" shape,
so remediation gives up immediately) and asserts that:
  - the first statement is still recorded as having executed,
  - the deploy is still reported as failed (return False),
  - last_deployment_error explicitly says "PARTIAL DEPLOY" with the correct
    executed/total counts,
  - a DropLedger record captures the same fact for reconciliation.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.drop_ledger import DropStage
from semabridge.formats.sml.models import SMLModel


def _build_emitter() -> SnowflakeEmitter:
    config = SnowflakeConfig(
        account="test.local", user="test_user", password="test_password",
        warehouse="test_wh", database="test_db", schema_name="test_schema",
        role="test_role",
    )
    behavior = ConnectorBehavior()
    # Disable every optional pipeline step this test doesn't exercise, so
    # only Step 1.5 (schema refresh) and Step 3 (DDL execution) need mocks.
    behavior.snowflake.create_missing_tables = False
    behavior.snowflake.apply_inferred_types = False
    behavior.snowflake.auto_execute_precompute = False
    behavior.features.enable_cortex_analyst = False
    return SnowflakeEmitter(config, behavior)


def _wire_for_two_ddl_run(emitter, monkeypatch, second_ddl_error):
    """Get _execute_deployment_pipeline from Step -1 through Step 1.5
    without touching a real Snowflake connection, then hand it exactly two
    DDL statements for Step 3 -- neither containing "SEMANTIC VIEW" or
    "_ENRICHED" text, so the pre-pass A/B refresh/drop scans are no-ops and
    the only _execute_sql calls made are the two DDL[idx] ones this test
    controls."""
    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    monkeypatch.setattr(emitter.connection_manager, "get_connection", lambda: (fake_conn, True))
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_duplicate_mappings", lambda *a, **k: None)
    monkeypatch.setattr(emitter.schema_manager, "refresh_schema_cache", lambda: None)
    monkeypatch.setattr(emitter.schema_manager, "_fetch_schema_metadata", lambda cur: {"DUMMY": set()})

    ddls = ['CREATE TABLE "DB"."SCH"."T1" (X INT)', 'CREATE TABLE "DB"."SCH"."T2" (Y INT)']
    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls", lambda model: ddls)

    def fake_execute_sql(cursor, sql, context=""):
        if context == "DDL[0]":
            return None  # first statement succeeds
        if context.startswith("DDL[1]"):
            raise second_ddl_error
        return None

    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql)
    return ddls


def test_partial_deploy_is_reported_honestly_after_one_ddl_already_succeeded(monkeypatch):
    emitter = _build_emitter()
    unrecoverable = RuntimeError(
        "002003: SQL compilation error: unexpected token -- no 'invalid identifier' "
        "shape here, so auto-remediation gives up on pass 1"
    )
    _wire_for_two_ddl_run(emitter, monkeypatch, unrecoverable)

    model = SMLModel(unique_name="TestModel", label="TestModel", datasets=[], metrics=[], relationships=[], dimensions=[])
    result = emitter.deploy(model, sync_mode="copy")

    assert result is False
    assert emitter.last_deployment_error is not None
    assert "PARTIAL DEPLOY" in emitter.last_deployment_error
    assert "1 of 2" in emitter.last_deployment_error
    assert "TestModel" in emitter.last_deployment_error

    records = emitter.drop_ledger.to_json()
    partial_records = [r for r in records if r["stage"] == DropStage.DDL_DEPLOYMENT.value]
    assert partial_records, "expected a DropLedger record marking the run as a partial deploy"
    assert any("partial" in r["reason"].lower() for r in partial_records)


def test_full_failure_before_any_ddl_executes_is_not_mislabeled_partial(monkeypatch):
    """The counterpart: if Snowflake rejects the very FIRST statement, zero
    statements have executed -- this must stay a plain failure, not get
    dressed up as "partial", since nothing was actually left behind."""
    emitter = _build_emitter()
    unrecoverable = RuntimeError("002003: SQL compilation error: rejected immediately")
    _wire_for_two_ddl_run(emitter, monkeypatch, unrecoverable)

    # Make the FIRST statement fail instead of the second.
    def fake_execute_sql(cursor, sql, context=""):
        if context.startswith("DDL[0]"):
            raise unrecoverable
        return None

    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql)

    model = SMLModel(unique_name="TestModel2", label="TestModel2", datasets=[], metrics=[], relationships=[], dimensions=[])
    result = emitter.deploy(model, sync_mode="copy")

    assert result is False
    assert emitter.last_deployment_error is not None
    assert "PARTIAL DEPLOY" not in emitter.last_deployment_error


def test_successful_remediation_does_not_count_as_partial(monkeypatch):
    """If the second statement fails but auto-remediation fixes and
    re-executes it successfully, this is a clean success -- no
    partial-deploy flag should ever be set."""
    emitter = _build_emitter()
    _wire_for_two_ddl_run(emitter, monkeypatch, RuntimeError("unused"))

    calls = {"ddl1_attempts": 0}

    def fake_execute_sql(cursor, sql, context=""):
        if context == "DDL[0]":
            return None
        if context.startswith("DDL[1]"):
            calls["ddl1_attempts"] += 1
            if calls["ddl1_attempts"] == 1:
                raise RuntimeError("000904: invalid identifier 'BOGUS_COL'")
            return None  # succeeds on the remediated retry
        return None

    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql)
    # remediate_invalid_identifier: force a trivial "fix" so the retry path runs.
    from semabridge.connectors import semantic_ddl_sanitizer as sds_module
    monkeypatch.setattr(
        sds_module.SemanticDDLSanitizer, "remediate_invalid_identifier",
        lambda self, sql, invalid_id: (sql.replace("Y INT", "Y INT /* fixed */"), True, []),
    )

    model = SMLModel(unique_name="TestModel3", label="TestModel3", datasets=[], metrics=[], relationships=[], dimensions=[])
    result = emitter.deploy(model, sync_mode="copy")

    assert result is True
    assert getattr(emitter, "_partial_deploy_info", None) is None
