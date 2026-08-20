"""Regression tests for core/engine/finalize.py's Step 10 drop-ledger
reconciliation.

Reproduces the class of bug found in proj-1942-test's run: Step 8's
schema-less preview emitter records a DAX_TRANSLATION/DDL_EMISSION drop for
a metric that Step 9's real, connected deploy later resolves successfully.
Nothing retracted Step 8's stale record, so it survived into
RunSummary.dropped_entities even though the metric is live in the actually
deployed DDL. _reconcile_dropped_entities() is the fix: it re-derives ground
truth from context.deployed_ddl_text (the real GET_DDL output) and drops any
metric-kind ledger entry reconciliation confirms is genuinely live.

All synthetic -- no DB, no emitter, no live Snowflake connection. Mirrors
the offline style of test_reconciliation.py, which this module's fix reuses
via compute_reconciliation()/is_metric_live().
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.finalize import _reconcile_dropped_entities


def _ddl(metrics_block: str) -> str:
    return (
        'CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."MODEL"\n'
        "METRICS (\n" + metrics_block + "\n)"
    )


def _make_context(*, metric_names: list[str], deployed_ddl_text: str | None) -> RunContext:
    context = RunContext(
        project_id="proj-test",
        run_id="run-test",
        config=SimpleNamespace(),
        start_time=0.0,
        source_type="pbix",
        target_type="snowflake",
    )
    context.sml_model = SimpleNamespace(
        metrics=[SimpleNamespace(unique_name=name) for name in metric_names]
    )
    context.sml_snapshot_id = "snap-1"
    context.deployed_ddl_text = deployed_ddl_text
    return context


def test_metric_that_failed_early_but_succeeded_in_real_deploy_is_removed():
    """The core proj-1942-test bug: a metric dropped by an earlier,
    incomplete pass (e.g. Step 8's schema-less preview) but present with
    real SQL in the actually-deployed DDL must not appear in the final
    dropped_entities."""
    ledger = DropLedger()
    ledger.record(
        "metric", "Total Units R12Ms", DropStage.DAX_TRANSLATION,
        "DAX expression could not be translated to SQL at DDL-emission time.",
    )
    context = _make_context(
        metric_names=["Total Units R12Ms"],
        deployed_ddl_text=_ddl('  FACT."TOTAL_UNITS_R12MS" AS SUM(FACT."UNITS")'),
    )
    context.drop_ledger.extend(ledger)

    reconciled = _reconcile_dropped_entities(context)

    assert reconciled == []


def test_metric_genuinely_absent_from_live_ddl_still_appears():
    """A metric that really is absent from the deployed DDL (or only
    present as a declared-dead CAST(NULL AS DOUBLE) placeholder) must still
    be reported -- reconciliation only removes entries PROVEN live."""
    ledger = DropLedger()
    ledger.record(
        "metric", "KPI01", DropStage.DDL_DEPLOYMENT,
        "Snowflake rejected this identifier when executing the compiled "
        "semantic-view DDL, and it could not be automatically remediated.",
    )
    context = _make_context(
        metric_names=["KPI01"],
        deployed_ddl_text=_ddl('  KPI."KPI01" AS CAST(NULL AS DOUBLE)'),
    )
    context.drop_ledger.extend(ledger)

    reconciled = _reconcile_dropped_entities(context)

    assert len(reconciled) == 1
    assert reconciled[0].entity_name == "KPI01"


def test_mixed_run_only_removes_the_confirmed_live_ones():
    """Reproduces proj-1942-test's actual shape: some drop records are
    stale (their metric deployed live for real), others are genuine (their
    metric is dead/absent) -- only the stale ones should be filtered out."""
    ledger = DropLedger()
    ledger.record(
        "metric", "PCT_UNITS_MARKET_SHARE_R12M", DropStage.DAX_TRANSLATION,
        "DAX expression could not be translated to SQL at DDL-emission time.",
    )
    ledger.record(
        "metric", "TOTAL_UNITS_YTD", DropStage.DDL_EMISSION,
        "SQL expression references a column that could not be resolved.",
    )
    ledger.record(
        "metric", "KPI02", DropStage.DDL_DEPLOYMENT,
        "Snowflake rejected this identifier and it could not be remediated.",
    )
    ledger.record(
        "table", "DateTableTemplate_abc", DropStage.EXTRACTION,
        "Auto-generated Power BI date-table shadow.", by_design=True,
    )
    context = _make_context(
        metric_names=["PCT_UNITS_MARKET_SHARE_R12M", "TOTAL_UNITS_YTD", "KPI02"],
        deployed_ddl_text=_ddl(
            '  FACT."PCT_UNITS_MARKET_SHARE_R12M" AS SUM(FACT."UNITS"),\n'
            '  FACT."TOTAL_UNITS_YTD" AS SUM(FACT."UNITS"),\n'
            '  KPI."KPI02" AS CAST(NULL AS DOUBLE)'
        ),
    )
    context.drop_ledger.extend(ledger)

    reconciled = _reconcile_dropped_entities(context)

    kept_names = {r.entity_name for r in reconciled}
    assert kept_names == {"KPI02", "DateTableTemplate_abc"}


def test_non_metric_non_column_entity_kinds_pass_through_untouched():
    """reconciliation.py only knows how to check metric and column names
    against DDL text (METRICS/DIMENSIONS clauses respectively) -- table and
    relationship drops must never be filtered, no matter what the deployed
    DDL contains. A column drop with no DIMENSIONS clause at all in the
    deployed DDL is genuinely absent too, so it also survives."""
    ledger = DropLedger()
    ledger.record("column", "MonthIndex", DropStage.SCHEMA_VALIDATION, "No live schema to confirm.")
    ledger.record("relationship", "KPI -> Date", DropStage.DDL_EMISSION, "Relationship dropped.")
    context = _make_context(
        metric_names=[],
        deployed_ddl_text=_ddl('  FACT."SOMETHING" AS SUM(FACT."X")'),
    )
    context.drop_ledger.extend(ledger)

    reconciled = _reconcile_dropped_entities(context)

    assert {r.entity_name for r in reconciled} == {"MonthIndex", "KPI -> Date"}


def test_column_that_failed_early_but_is_live_in_deployed_dimensions_is_removed():
    """Reproduces the real proj-1942-test/customer bug: an earlier,
    schema-less pass (Step 8) records a column drop (e.g. MonthIndex, only
    confirmed live via a later live-schema fetch) before the real, connected
    deploy (Step 9) actually emits it in the DIMENSIONS clause. That earlier
    record must be retracted once reconciliation proves the column is live,
    exactly like the existing metric-level behavior."""
    ledger = DropLedger()
    ledger.record(
        "column", "MonthIndex", DropStage.SCHEMA_VALIDATION,
        "Column 'MONTHINDEX' is present in the model but not found in the "
        "live Snowflake schema for dataset 'Date'",
    )
    context = _make_context(
        metric_names=[],
        deployed_ddl_text=(
            'CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."MODEL"\n'
            "DIMENSIONS (\n"
            '  COL_DATE."MONTHINDEX" AS COL_DATE."MONTHINDEX"\n'
            ")"
        ),
    )
    context.drop_ledger.extend(ledger)

    reconciled = _reconcile_dropped_entities(context)

    assert reconciled == []


def test_column_genuinely_absent_from_deployed_dimensions_still_appears():
    """A column drop must survive reconciliation when the deployed DDL's
    DIMENSIONS clause genuinely does not contain it -- only PROVEN-live
    entries are removed."""
    ledger = DropLedger()
    ledger.record("column", "MonthIndex", DropStage.SCHEMA_VALIDATION, "No live schema to confirm.")
    context = _make_context(
        metric_names=[],
        deployed_ddl_text=(
            'CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."MODEL"\n'
            "DIMENSIONS (\n"
            '  COL_DATE."YEAR" AS COL_DATE."YEAR"\n'
            ")"
        ),
    )
    context.drop_ledger.extend(ledger)

    reconciled = _reconcile_dropped_entities(context)

    assert len(reconciled) == 1
    assert reconciled[0].entity_name == "MonthIndex"


def test_no_deployed_ddl_falls_back_to_unreconciled_ledger():
    """Dry run / no target / deploy itself failed -- there is no live DDL
    to reconcile against, so nothing should be filtered. This is the
    pre-existing behavior and must not regress."""
    ledger = DropLedger()
    ledger.record("metric", "Total Units R12Ms", DropStage.DAX_TRANSLATION, "...")
    context = _make_context(metric_names=["Total Units R12Ms"], deployed_ddl_text=None)
    context.drop_ledger.extend(ledger)

    reconciled = _reconcile_dropped_entities(context)

    assert len(reconciled) == 1
    assert reconciled[0].entity_name == "Total Units R12Ms"


def test_empty_ledger_short_circuits_without_touching_deployed_ddl_text():
    context = _make_context(metric_names=[], deployed_ddl_text=_ddl('  FACT."X" AS SUM(FACT."X")'))

    reconciled = _reconcile_dropped_entities(context)

    assert reconciled == []


def test_reconciliation_failure_is_non_fatal_and_falls_back(monkeypatch):
    """If compute_reconciliation() itself raises for any reason, Step 10
    must not crash the whole run over a reporting aid -- fall back to the
    unreconciled ledger, exactly like the no-DDL case."""
    import semabridge.core.engine.finalize as finalize_module

    def _boom(**_kwargs):
        raise RuntimeError("synthetic reconciliation failure")

    monkeypatch.setattr(finalize_module, "compute_reconciliation", _boom)

    ledger = DropLedger()
    ledger.record("metric", "Total Units R12Ms", DropStage.DAX_TRANSLATION, "...")
    context = _make_context(
        metric_names=["Total Units R12Ms"],
        deployed_ddl_text=_ddl('  FACT."TOTAL_UNITS_R12MS" AS SUM(FACT."UNITS")'),
    )
    context.drop_ledger.extend(ledger)

    reconciled = _reconcile_dropped_entities(context)

    assert len(reconciled) == 1
    assert reconciled[0].entity_name == "Total Units R12Ms"
