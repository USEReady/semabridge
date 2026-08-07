"""Unit tests for semabridge.core.reconciliation's pure computation layer.

These exercise compute_reconciliation() directly with synthetic inputs (no
DB, no emitter, no network) — the same function reconcile_run() delegates
to after gathering snapshot/DDL/ledger data from a real run.
"""
from __future__ import annotations

from semabridge.core.reconciliation import compute_reconciliation


def _ddl(metrics_block: str) -> str:
    return (
        'CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."MODEL"\n'
        "METRICS (\n" + metrics_block + "\n)"
    )


def test_clean_run_has_no_unaccounted_metrics():
    ddl = _ddl('  FACT."REVENUE" AS SUM(FACT."REVENUE"),\n  FACT."UNITS" AS SUM(FACT."UNITS")')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue", "Units"],
        deployed_ddl_text=ddl,
        drop_records=[],
    )
    assert report.is_clean()
    assert report.unaccounted == {}
    assert report.snapshot_metrics == {"REVENUE", "UNITS"}
    assert report.deployed_live_metrics == {"REVENUE", "UNITS"}


def test_missing_metric_with_no_ddl_and_no_ledger_entry_is_unaccounted():
    """The core invariant: a metric absent from both DDL and the ledger is a bug."""
    ddl = _ddl('  FACT."REVENUE" AS SUM(FACT."REVENUE")')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue", "Units"],
        deployed_ddl_text=ddl,
        drop_records=[],
    )
    assert not report.is_clean()
    assert report.unaccounted == {"UNITS": 1}


def test_dropped_metric_recorded_in_ledger_is_not_unaccounted():
    ddl = _ddl('  FACT."REVENUE" AS SUM(FACT."REVENUE")')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue", "Units"],
        deployed_ddl_text=ddl,
        drop_records=[
            {"entity_kind": "metric", "entity_name": "Units", "stage": "dax_translation", "reason": "..."},
        ],
    )
    assert report.is_clean()
    assert report.unaccounted == {}
    assert report.dropped_metrics == {"UNITS"}


def test_declared_dead_metric_with_no_ledger_record_is_unaccounted():
    """CAST(NULL AS DOUBLE) metrics are visible (present in DDL, tracked in a
    distinct 'dead' bucket since they aren't working metrics) but are NOT
    automatically 'accounted for' just by being textually present -- some
    mechanism nulled this metric out, and if it never called
    DropLedger.record(...), that is exactly the silent-drop bug this module
    exists to catch, not a clean run."""
    ddl = _ddl('  FACT."REVENUE" AS SUM(FACT."REVENUE"),\n  FACT."UNITS" AS CAST(NULL AS DOUBLE)')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue", "Units"],
        deployed_ddl_text=ddl,
        drop_records=[],
    )
    assert not report.is_clean()
    assert report.unaccounted == {"UNITS": 1}
    assert report.deployed_dead_metrics == {"UNITS"}
    assert "UNITS" not in report.deployed_live_metrics


def test_declared_dead_metric_with_matching_ledger_record_is_accounted():
    """The same CAST(NULL AS DOUBLE) DDL entry IS accounted for once the
    mechanism that nulled it out recorded why via DropLedger -- the ledger
    record, not mere DDL presence, is what satisfies the invariant."""
    ddl = _ddl('  FACT."REVENUE" AS SUM(FACT."REVENUE"),\n  FACT."UNITS" AS CAST(NULL AS DOUBLE)')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue", "Units"],
        deployed_ddl_text=ddl,
        drop_records=[
            {"entity_kind": "metric", "entity_name": "Units", "stage": "dax_translation", "reason": "..."},
        ],
    )
    assert report.is_clean()
    assert report.unaccounted == {}
    assert report.deployed_dead_metrics == {"UNITS"}


def test_dedup_suffixed_name_still_reconciles_against_base_snapshot_name():
    """A metric whose alias collided with another gets a '_2'/'_<hash>'
    suffix in the emitted DDL name — this is still 'deployed', not vanished."""
    ddl = _ddl('  FACT."REVENUE" AS SUM(FACT."A"),\n  FACT."REVENUE_2" AS SUM(FACT."B")')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue", "Revenue"],  # two distinct source metrics, same normalized base
        deployed_ddl_text=ddl,
        drop_records=[],
    )
    # Both collapse to one normalized snapshot key ("REVENUE"), which is
    # matched by the base deployed name -- nothing left unaccounted.
    assert report.is_clean()


def test_ddl_generation_error_still_reports_whatever_ledger_was_populated():
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue"],
        deployed_ddl_text="",
        drop_records=[
            {"entity_kind": "metric", "entity_name": "Revenue", "stage": "ddl_emission", "reason": "..."},
        ],
        ddl_error="Invalid semantic DDL: ...",
    )
    assert report.is_clean()
    assert report.ddl_error is not None


def test_name_collision_blind_spot_is_now_caught():
    """Regression test for the collision blind spot: two DISTINCT source
    metrics ("Total Units YTD Var %" and "TOTAL_UNITS_YTD_VAR_PCT" in the
    real bug) normalize to the identical base name. A presence-only (set)
    check is satisfied the moment ANY one of them is deployed or dropped,
    even if the other vanished with zero record. Counting occurrences per
    base is what catches this."""
    # Only ONE deployed line for a base that TWO distinct snapshot metrics
    # share, and no ledger record at all for the second one.
    ddl = _ddl('  FACT."REVENUE_PCT" AS SUM(FACT."A")')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue Pct", "REVENUE_PCT"],  # distinct raw names, same normalized base
        deployed_ddl_text=ddl,
        drop_records=[],
    )
    assert not report.is_clean()
    assert report.unaccounted == {"REVENUE_PCT": 1}


def test_name_collision_fully_accounted_when_both_survive():
    """Same collision, but both metrics are legitimately disambiguated in
    the DDL (the correct, already-handled emission-time behavior) -- fully
    reconciled, no false positive from the collision itself."""
    ddl = _ddl('  FACT."REVENUE_PCT_1" AS SUM(FACT."A"),\n  FACT."REVENUE_PCT_2" AS SUM(FACT."B")')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue Pct", "REVENUE_PCT"],
        deployed_ddl_text=ddl,
        drop_records=[],
    )
    assert report.is_clean()


def test_name_collision_one_deployed_one_properly_dropped_is_clean():
    """Same collision; one metric deploys, the other legitimately fails
    translation and gets a real ledger record -- fully accounted, count
    balances (1 deployed + 1 dropped == 2 snapshot occurrences of the base)."""
    ddl = _ddl('  FACT."REVENUE_PCT" AS SUM(FACT."A")')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue Pct", "REVENUE_PCT"],
        deployed_ddl_text=ddl,
        drop_records=[
            {"entity_kind": "metric", "entity_name": "REVENUE_PCT", "stage": "dax_translation", "reason": "..."},
        ],
    )
    assert report.is_clean()


def test_non_metric_drop_records_are_ignored():
    ddl = _ddl('  FACT."REVENUE" AS SUM(FACT."REVENUE")')
    report = compute_reconciliation(
        run_id="r1", project_id="p1", snapshot_id="s1",
        snapshot_metric_names=["Revenue"],
        deployed_ddl_text=ddl,
        drop_records=[
            {"entity_kind": "column", "entity_name": "SomeColumn", "stage": "schema_validation", "reason": "..."},
        ],
    )
    assert report.dropped_metrics == set()
    assert report.is_clean()
