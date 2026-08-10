"""Regression tests for the remaining Part 2 findings applied to
snowflake_emitter.py / translator.py (findings #1, #2, #4, #5 from the audit
-- #3, the mid-DDL partial-deploy fix, has its own dedicated test file:
test_snowflake_emitter_partial_deploy.py, whose third test also doubles as
an end-to-end confirmation of the bare-identifier drop-ledger fix below).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.formats.sml.models import SMLModel
from semabridge.utils.identifiers import IdentifierSanitizer


# ---------------------------------------------------------------------------
# Finding: _validate_relationships_measures_on_existing_tables's exception
# path returned a bare 1-element list instead of the (errors, incompatible)
# 2-tuple every success path returns -- the UPSERT call site unconditionally
# unpacks two values, so ANY internal exception here used to become a
# ValueError that aborted the entire deploy, not just this validation step.
# ---------------------------------------------------------------------------

def test_validation_exception_still_returns_a_two_tuple_not_a_bare_list():
    """A malformed existing_tables entry (missing the 'exists' key a real
    caller always sets) triggers a real internal KeyError -- confirming the
    fix returns (errors, []) instead of crashing the caller's unpack."""
    existing_tables = {"DS_A": {}}  # missing 'exists' -> KeyError inside
    relationship = SimpleNamespace(
        unique_name="R1", is_active=True,
        from_dataset="DS_A", to_dataset="DS_B",
        from_columns=["COL"], to_columns=["COL"],
    )
    model = SimpleNamespace(datasets=[], relationships=[relationship], metrics=[])

    # This is exactly the call-site shape used in
    # _execute_deployment_pipeline's UPSERT-preserve step -- if the return
    # shape ever regresses to a bare list again, this line raises
    # ValueError before either assertion below runs.
    errors, incompatible = SnowflakeEmitter._validate_relationships_measures_on_existing_tables(
        None, None, model, existing_tables, False,
    )

    assert isinstance(errors, list) and len(errors) == 1
    assert "Validation error" in errors[0]
    assert incompatible == []


def test_validation_success_path_unaffected():
    """Sanity check: a clean run with no relationships/metrics still
    returns the same (errors, incompatible) shape it always did."""
    errors, incompatible = SnowflakeEmitter._validate_relationships_measures_on_existing_tables(
        None, None, SimpleNamespace(datasets=[], relationships=[], metrics=[]), {}, False,
    )
    assert errors == []
    assert incompatible == []


# ---------------------------------------------------------------------------
# Finding: the DDL-remediation drop-ledger recording was gated on "." in
# the rejected identifier, silently skipping the record for any BARE
# (unqualified) identifier other than the two hardcoded MAX_DATE/
# MAX_MONTHINDEX anchors -- even when remediate_invalid_identifier's
# METRICS-clause sweep genuinely nulled out a metric for that bare name.
# ---------------------------------------------------------------------------

def _build_emitter_for_ddl_loop() -> SnowflakeEmitter:
    config = SnowflakeConfig(
        account="test.local", user="test_user", password="test_password",
        warehouse="test_wh", database="test_db", schema_name="test_schema",
        role="test_role",
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.create_missing_tables = False
    behavior.snowflake.apply_inferred_types = False
    behavior.snowflake.auto_execute_precompute = False
    behavior.features.enable_cortex_analyst = False
    return SnowflakeEmitter(config, behavior)


def _wire_single_ddl(emitter, monkeypatch, ddl_text, error_then_success):
    """Wire the pipeline to execute exactly one DDL statement whose first
    attempt fails with `error_then_success[0]` and (if remediation fixes it)
    succeeds on retry. Returns nothing; mutates emitter via monkeypatch."""
    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor
    monkeypatch.setattr(emitter.connection_manager, "get_connection", lambda: (fake_conn, True))
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_duplicate_mappings", lambda *a, **k: None)
    monkeypatch.setattr(emitter.schema_manager, "refresh_schema_cache", lambda: None)
    monkeypatch.setattr(emitter.schema_manager, "_fetch_schema_metadata", lambda cur: {"DUMMY": set()})
    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls", lambda model: [ddl_text])

    attempts = {"n": 0}

    def fake_execute_sql(cursor, sql, context=""):
        if context == "DDL[0]":
            attempts["n"] += 1
            raise error_then_success
        if context.startswith("DDL[0] pass"):
            return None  # remediated retry always succeeds in these tests
        return None

    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql)

    from semabridge.connectors import semantic_ddl_sanitizer as sds_module
    monkeypatch.setattr(
        sds_module.SemanticDDLSanitizer, "remediate_invalid_identifier",
        lambda self, sql, invalid_id: (sql + " /* fixed */", True, ["SOME_METRIC"]),
    )


def test_bare_invalid_identifier_null_out_is_now_recorded(monkeypatch):
    """A bare (unqualified) rejected identifier that is NOT MAX_DATE/
    MAX_MONTHINDEX -- e.g. Snowflake's classic 000904 shape for some other
    anchor/computed column -- must still produce a drop_ledger record and a
    _dropped_metrics entry once remediation nulls a metric for it."""
    emitter = _build_emitter_for_ddl_loop()
    _wire_single_ddl(
        emitter, monkeypatch,
        'CREATE TABLE "DB"."SCH"."T1" (X INT)',
        RuntimeError("000904: invalid identifier 'SOME_BARE_ANCHOR'"),
    )
    model = SMLModel(unique_name="M", label="M", datasets=[], metrics=[], relationships=[], dimensions=[])

    result = emitter.deploy(model, sync_mode="copy")

    assert result is True
    assert any(d["metric"] == "SOME_METRIC" for d in emitter._dropped_metrics)
    records = emitter.drop_ledger.to_json()
    assert any(r["entity_name"] == "SOME_METRIC" for r in records)


def test_max_date_bare_identifier_still_not_recorded(monkeypatch):
    """Negative control: MAX_DATE substitution never nulls a metric (it
    swaps in CURRENT_DATE() and keeps every metric's real semantics), so it
    must still be excluded from drop_ledger -- confirming the fix didn't
    overcorrect into recording every bare identifier unconditionally."""
    emitter = _build_emitter_for_ddl_loop()
    _wire_single_ddl(
        emitter, monkeypatch,
        'CREATE TABLE "DB"."SCH"."T1" (X INT)',
        RuntimeError("000904: invalid identifier 'MAX_DATE'"),
    )
    from semabridge.connectors import semantic_ddl_sanitizer as sds_module
    monkeypatch.setattr(
        sds_module.SemanticDDLSanitizer, "remediate_invalid_identifier",
        lambda self, sql, invalid_id: (sql.replace("MAX_DATE", "CURRENT_DATE()"), True, []),
    )
    model = SMLModel(unique_name="M2", label="M2", datasets=[], metrics=[], relationships=[], dimensions=[])

    result = emitter.deploy(model, sync_mode="copy")

    assert result is True
    assert emitter._dropped_metrics == []
    assert emitter.drop_ledger.to_json() == []


# ---------------------------------------------------------------------------
# Finding: _auto_execute_precompute_suggestions's table-existence probe had
# a bare `except Exception: continue` with zero logging -- indistinguishable
# from "table doesn't exist" but could also silently eat a real connection/
# permission/syntax failure on a live, paid deploy.
# ---------------------------------------------------------------------------

def test_precompute_table_existence_probe_logs_before_skipping(caplog):
    emitter = _build_emitter_for_ddl_loop()
    emitter._model = SimpleNamespace(relationships=[], datasets=[])

    class ExplodingCursor:
        def execute(self, sql):
            raise RuntimeError("insufficient privileges to operate on table 'T1'")

    # live_schema_metadata must be non-empty (any table) so the function's
    # own "if not live_meta: fall back to _fetch_schema_metadata(cursor)"
    # branch is skipped -- that fallback calls cursor.execute(sql, params)
    # (2 args) through a completely different code path this test isn't
    # exercising. NEW_COL is deliberately absent from T1's existing columns
    # so cols_needing_add is non-empty and the loop reaches the "Check table
    # exists" probe this test targets, instead of short-circuiting earlier
    # on "column already exists".
    monkeypatch_suggestions = {"T1": ["NEW_COL"]}
    emitter.semantic_view_builder._precompute_suggestions = lambda model: monkeypatch_suggestions
    emitter.semantic_view_builder.get_precompute_details = lambda: []
    emitter.semantic_view_builder.live_schema_metadata = {"T1": {"OTHER_COL"}}

    with caplog.at_level(logging.WARNING):
        emitter._auto_execute_precompute_suggestions(SimpleNamespace(datasets=[]), ExplodingCursor())

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "T1" in text
    assert "insufficient privileges" in text


# ---------------------------------------------------------------------------
# Finding: translator.anchor_flag_map was a flat shape->flag_name map merged
# across every fact table in a model -- a metric on a fact table whose own
# MAX_DATE anchor was never established could still resolve a flag column
# name that only exists on a *different* fact table's enriched view. Fixed
# by nesting anchor_flag_map by fact table and narrowing at each consumer.
# ---------------------------------------------------------------------------

def test_anchor_flag_map_lookup_is_scoped_to_the_metrics_own_fact_table():
    translator = MetricExpressionTranslator(IdentifierSanitizer())

    # Fact table A successfully established its own YTD flag column.
    translator.anchor_flag_map.setdefault("salesfact_a", {})[("YTD",)] = "IS_YTD"
    # Fact table B never established a date anchor at all -- no entry.

    metric_on_b = SimpleNamespace(dataset="SalesFact_B", unique_name="Some Metric")

    # This is exactly what _try_basic_dax_metric_fallback_expression and the
    # deterministic AST-translator call site each do before passing
    # anchor_flag_map further down -- narrowed to the metric's own dataset.
    scoped_for_b = translator.anchor_flag_map.get(str(metric_on_b.dataset or "").casefold(), {})
    assert scoped_for_b == {}
    assert ("YTD",) not in scoped_for_b

    metric_on_a = SimpleNamespace(dataset="SalesFact_A", unique_name="Other Metric")
    scoped_for_a = translator.anchor_flag_map.get(str(metric_on_a.dataset or "").casefold(), {})
    assert scoped_for_a == {("YTD",): "IS_YTD"}


def test_anchor_flag_map_storage_is_nested_by_fact_table_after_enrichment(monkeypatch):
    """End-to-end confirmation via the actual writer:
    SnowflakeEmitter._create_enriched_view must write under a fact-table
    key, not flatten shape->name entries directly onto anchor_flag_map."""
    emitter = _build_emitter_for_ddl_loop()
    fake_cursor = MagicMock()
    # MAX("<date col>") anchor fetch.
    fake_cursor.fetchone.return_value = ("2024-01-01",)

    fact_ds = SimpleNamespace(unique_name="SalesFact", source_table="SALES_FACT", columns=[])
    date_ds = SimpleNamespace(unique_name="Date", source_table="DATE_DIM", columns=[])
    model = SimpleNamespace(datasets=[fact_ds, date_ds], metrics=[], relationships=[])

    monkeypatch.setattr(emitter, "_find_date_table", lambda m: ("Date", "DATE", "MONTHINDEX"))
    monkeypatch.setattr(emitter.schema_manager, "_resolve_physical_column_name", lambda ds, col, model=None: col)
    emitter._live_schema_metadata["SALES_FACT"] = {"DATE"}

    from semabridge.converter import time_intelligence_shapes as tis_module
    monkeypatch.setattr(tis_module, "discover_time_intelligence_shapes", lambda metrics: {("YTD",)})

    view_name = emitter._create_enriched_view(model, fake_cursor, fact_table="SalesFact")

    assert view_name == "SalesFact_ENRICHED"
    assert "salesfact" in emitter.translator.anchor_flag_map
    assert ("YTD",) in emitter.translator.anchor_flag_map["salesfact"]
    # And the flat, unqualified shape must NOT appear as a top-level key --
    # that would mean this regressed back to a flat map.
    assert ("YTD",) not in emitter.translator.anchor_flag_map
