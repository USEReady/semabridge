"""Staged Snowflake deploy: nothing live changes until the new definition
has been created and validated under staging names; a failure leaves the
live objects untouched and the staging objects dropped.

Driven through SnowflakeEmitter.deploy with a fake connection whose
``_execute_sql`` records every statement (context + SQL) and can inject a
failure at any step.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pytest

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.staged_deploy import STAGING_MARK, StagedDeploy, with_copy_grants
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.formats.sml.models import SMLModel

LIVE_SV = "SALES_SV"
LIVE_ENRICHED = "SALES_ENRICHED"
ENRICHED_SQL = f"CREATE OR REPLACE VIEW {LIVE_ENRICHED} COMMENT = 'Created by SemaBridge' AS\nSELECT * FROM SALES"
SV_DDL = (
    f'CREATE OR REPLACE SEMANTIC VIEW "TEST_DB"."TEST_SCHEMA"."{LIVE_SV}"\n'
    "  TABLES (\n"
    f'    SALES AS "TEST_DB"."TEST_SCHEMA"."{LIVE_ENRICHED}" PRIMARY KEY ("ID")\n'
    "  )\n"
    "  DIMENSIONS (\n"
    '    SALES."REGION" AS SALES."REGION"\n'
    "  )\n"
    "  METRICS (\n"
    '    SALES."TOTAL" AS SUM(SALES."AMOUNT")\n'
    "  )"
)
PREVIOUS_ENRICHED_DDL = f"create or replace view {LIVE_ENRICHED} as select 'old' as v"


class FakeCursor:
    def __init__(self) -> None:
        self.description: List[Tuple[str]] = []
        self.direct: List[str] = []
        self._next: Optional[tuple] = None

    def execute(self, sql: str, params: Any = None) -> None:
        self.direct.append(sql)
        self._next = (PREVIOUS_ENRICHED_DDL,) if sql.startswith("SELECT GET_DDL('VIEW'") else None

    def fetchone(self):
        return self._next

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        pass


def _emitter(monkeypatch, fail_on: Optional[str] = None):
    config = SnowflakeConfig(
        account="test.local", user="u", password="p", warehouse="wh",
        database="TEST_DB", schema_name="TEST_SCHEMA", role="r",
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.create_missing_tables = False
    behavior.snowflake.apply_inferred_types = False
    behavior.snowflake.auto_execute_precompute = False
    behavior.snowflake.auto_create_enriched_view = True
    behavior.snowflake.use_enriched_view_for_metrics = True
    behavior.features.enable_cortex_analyst = False
    emitter = SnowflakeEmitter(config, behavior)

    cursor = FakeCursor()
    calls: List[Tuple[str, str]] = []

    def fake_execute_sql(cur, sql, params=None, *, context="SQL", expected_failure=False):
        calls.append((context, sql))
        if fail_on and context.startswith(fail_on):
            raise RuntimeError(f"002003: injected failure at {context}")
        return None

    def fake_create_enriched_view(model, cur, fact_table=None, dry_run=False):
        # The real method's write point: staged when a staged deploy is active.
        if emitter._staged is not None:
            emitter._staged.stage_enriched_view(cur, LIVE_ENRICHED, ENRICHED_SQL)
        else:
            emitter.connection_manager._execute_sql(cur, ENRICHED_SQL, context="create-enriched-view")
        return LIVE_ENRICHED

    monkeypatch.setattr(emitter.connection_manager, "get_connection", lambda: (FakeConn(cursor), True))
    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql)
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_duplicate_mappings", lambda *a, **k: None)
    monkeypatch.setattr(emitter.schema_manager, "refresh_schema_cache", lambda: None)
    monkeypatch.setattr(emitter.schema_manager, "_ensure_schema_exists", lambda cur: None)
    monkeypatch.setattr(emitter.schema_manager, "_fetch_schema_metadata", lambda cur: {"SALES": {"ID", "REGION", "AMOUNT"}})
    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls", lambda model: [SV_DDL])
    monkeypatch.setattr(emitter, "_get_fact_tables_needing_enrichment", lambda model: ["SALES"])
    monkeypatch.setattr(emitter, "_create_enriched_view", fake_create_enriched_view)
    monkeypatch.setattr(emitter, "_generate_deployment_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(emitter, "_run_cortex_safety_validation", lambda cur, name, ddl, path: ddl)
    monkeypatch.setattr(emitter, "_view_exists", lambda cur, fq: STAGING_MARK in fq or LIVE_SV in fq)
    return emitter, calls, cursor


def _model():
    return SMLModel(unique_name=LIVE_SV, label=LIVE_SV, datasets=[], metrics=[], relationships=[], dimensions=[])


def _writes(calls):
    return [(c, s) for c, s in calls if s.lstrip().upper().startswith(("CREATE", "DROP", "ALTER"))]


def test_success_builds_in_staging_then_promotes_identical_definitions_last(monkeypatch):
    emitter, calls, _ = _emitter(monkeypatch)
    assert emitter.deploy(_model(), sync_mode="copy") is True

    writes = _writes(calls)
    contexts = [c for c, _ in writes]
    # staging enriched -> staging semantic view -> promote enriched -> promote SV -> drop staging
    assert contexts[0] == "create-enriched-view-staging"
    assert contexts[1].startswith("DDL[0] (staging)")
    assert contexts[2:4] == ["promote-enriched-view", "promote-semantic-view"]
    assert contexts[4:] == ["drop-staging-object", "drop-staging-object"]

    staging_sv_sql = writes[1][1]
    assert f"{LIVE_SV}{STAGING_MARK}" in staging_sv_sql
    assert f"{LIVE_ENRICHED}{STAGING_MARK}" in staging_sv_sql  # validated against the NEW enriched view
    # The live objects get exactly the definitions an unstaged deploy creates.
    assert writes[2][1] == ENRICHED_SQL
    # ...plus COPY GRANTS, so replacing the live view keeps its grants.
    assert writes[3][1] == with_copy_grants(SV_DDL)
    assert writes[3][1].rstrip().endswith("COPY GRANTS")
    # Nothing live was written before validation finished.
    assert all(STAGING_MARK in s for c, s in writes[:2])
    actions = [(e["action"], e["object_type"]) for e in emitter.deploy_events]
    assert ("promoted", "SEMANTIC VIEW") in actions and ("cleaned", "VIEW") in actions


def test_failed_semantic_view_create_leaves_live_objects_untouched(monkeypatch):
    emitter, calls, _ = _emitter(monkeypatch, fail_on="DDL[0] (staging)")
    assert emitter.deploy(_model(), sync_mode="copy") is False

    for context, sql in _writes(calls):
        if context.startswith("DDL[0]") or context == "create-enriched-view-staging":
            assert STAGING_MARK in sql
        else:
            # only staging cleanup may run after the failure
            assert context == "drop-staging-object" and STAGING_MARK in sql
    assert not [c for c, _ in calls if c.startswith("promote")]
    dropped = [s for c, s in calls if c == "drop-staging-object"]
    assert any("SEMANTIC VIEW" in s for s in dropped) and any(s.startswith("DROP VIEW") for s in dropped)
    assert "PARTIAL DEPLOY" not in (emitter.last_deployment_error or "")


def test_failed_promotion_restores_enriched_views_and_keeps_live_semantic_view(monkeypatch):
    emitter, calls, cursor = _emitter(monkeypatch, fail_on="promote-semantic-view")
    assert emitter.deploy(_model(), sync_mode="copy") is False

    contexts = [c for c, _ in _writes(calls)]
    assert "restore-enriched-view" in contexts
    restored = [s for c, s in calls if c == "restore-enriched-view"]
    assert restored == [PREVIOUS_ENRICHED_DDL]  # captured with GET_DDL before promotion
    assert any(sql.startswith("SELECT GET_DDL('VIEW'") for sql in cursor.direct)
    assert contexts[-2:] == ["drop-staging-object", "drop-staging-object"]
    assert "left as it was" in (emitter.last_deployment_error or "")


def test_unbuildable_enriched_view_is_dropped_only_after_promotion():
    executed: List[Tuple[str, str]] = []
    staged = StagedDeploy(lambda cur, sql, context="": executed.append((context, sql)), "DB", "SCH", token="T1")
    staged.defer_drop('DROP VIEW IF EXISTS "OLD_ENRICHED"', "OLD_ENRICHED")
    assert executed == []
    staged.promote(object(), [])
    assert executed == [("deferred-drop-enriched-view", 'DROP VIEW IF EXISTS "OLD_ENRICHED"')]


def test_staging_rewrite_round_trips_exactly():
    staged = StagedDeploy(lambda *a, **k: None, "TEST_DB", "TEST_SCHEMA", token="AB12")
    staged.stage_enriched_view(object(), LIVE_ENRICHED, ENRICHED_SQL)
    staging = staged.to_staging(SV_DDL)
    assert f'"{LIVE_SV}__SBSTG_AB12"' in staging and f'"{LIVE_ENRICHED}__SBSTG_AB12"' in staging
    assert staged.to_live(staging) == SV_DDL
    cols = {"SALES_ENRICHED__SBSTG_AB12": {"A"}, "TEST_SCHEMA.SALES_ENRICHED__SBSTG_AB12": {"A"}}
    staged.alias_columns(cols)
    assert cols["SALES_ENRICHED"] == {"A"} and cols["TEST_SCHEMA.SALES_ENRICHED"] == {"A"}


def test_staged_deploy_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_STAGED_DEPLOY", "false")
    emitter, calls, _ = _emitter(monkeypatch)
    assert emitter.deploy(_model(), sync_mode="copy") is True
    assert not [s for _, s in _writes(calls) if STAGING_MARK in s]


@pytest.fixture(autouse=True)
def _allow_staged_names(monkeypatch):
    # The real _execute_sql runs the ownership guard; the fake above does
    # not, so nothing to set up -- kept explicit for readers.
    yield


def test_copy_grants_only_on_semantic_view_replace():
    sv = 'CREATE OR REPLACE SEMANTIC VIEW "D"."S"."V"\n  TABLES (T AS "D"."S"."T")\n  COMMENT = \'x\';'
    out = with_copy_grants(sv)
    assert out.endswith("COPY GRANTS;")             # last clause, before the terminator
    assert with_copy_grants(out) == out             # idempotent
    view = 'CREATE OR REPLACE VIEW "D"."S"."E" AS SELECT 1'
    assert with_copy_grants(view) == view           # other DDL untouched
    assert with_copy_grants("") == ""
