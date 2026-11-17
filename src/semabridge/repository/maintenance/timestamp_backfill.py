"""One-off repair of timestamps that were written naive into timestamptz columns.

Until this fix several writers used ``datetime.utcnow()`` (naive) for
``timestamp with time zone`` columns. PostgreSQL interprets a naive value in
the *session* time zone, so on a server whose ``TimeZone`` is not UTC (here
Asia/Kolkata, +05:30) those instants were stored 5h30m too early -- e.g. a
run's ``completed_at`` before its (correct, tz-aware) ``started_at``.

Repair: re-read the stored local wall-clock time as UTC,
``(col AT TIME ZONE current_setting('TimeZone')) AT TIME ZONE 'UTC'``
(exact inverse of the bad write, DST-safe). Only rows that are PROVABLY
naive-written are touched -- each rule cross-checks against a tz-aware
timestamp of the same run:

* ``runs.completed_at``     -- completed before started, and the repaired
  value is not before started.
* ``snapshots.timestamp``   -- snapshot of a run that is earlier than the
  run's start, and the repaired value is not.
* ``snapshots.deployed_at`` -- same rule as ``timestamp``.
* ``model_versions.created_at`` -- version written by "Sync run <run_id>"
  that is earlier than that run's start, and the repaired value is not.

Rows that cannot be proven (e.g. ``projects.last_updated``, model versions of
runs no longer in ``runs``) are left alone. Idempotent: after a repair no
rule matches. A UTC server matches nothing. PostgreSQL only.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

_FIX = "((({col}) AT TIME ZONE current_setting('TimeZone')) AT TIME ZONE 'UTC')"

# name -> (count sql, update sql)
_RULES: Dict[str, tuple] = {
    "runs.completed_at": (
        "SELECT count(*) FROM runs WHERE completed_at < started_at AND {fix} >= started_at",
        "UPDATE runs SET completed_at = {fix} WHERE completed_at < started_at AND {fix} >= started_at",
        "completed_at",
    ),
    "snapshots.timestamp": (
        "SELECT count(*) FROM snapshots s JOIN runs r ON r.run_id = s.run_id "
        "WHERE s.timestamp < r.started_at AND {fix} >= r.started_at",
        "UPDATE snapshots s SET timestamp = {fix} FROM runs r WHERE r.run_id = s.run_id "
        "AND s.timestamp < r.started_at AND {fix} >= r.started_at",
        "s.timestamp",
    ),
    "snapshots.deployed_at": (
        "SELECT count(*) FROM snapshots s JOIN runs r ON r.run_id = s.run_id "
        "WHERE s.deployed_at < r.started_at AND {fix} >= r.started_at",
        "UPDATE snapshots s SET deployed_at = {fix} FROM runs r WHERE r.run_id = s.run_id "
        "AND s.deployed_at < r.started_at AND {fix} >= r.started_at",
        "s.deployed_at",
    ),
    "model_versions.created_at": (
        "SELECT count(*) FROM model_versions mv JOIN runs r ON mv.change_summary = 'Sync run ' || r.run_id "
        "WHERE mv.created_at < r.started_at AND {fix} >= r.started_at",
        "UPDATE model_versions mv SET created_at = {fix} FROM runs r "
        "WHERE mv.change_summary = 'Sync run ' || r.run_id "
        "AND mv.created_at < r.started_at AND {fix} >= r.started_at",
        "mv.created_at",
    ),
}


def backfill_naive_timestamps(conn: Any, *, apply: bool = False) -> Dict[str, Any]:
    """Count (``apply=False``) or repair (``apply=True``) naive-written rows.

    ``conn`` is a SQLAlchemy Connection. Returns
    ``{"dialect", "timezone", "apply", "rows": {rule: n}}``.
    """
    from sqlalchemy import text

    report: Dict[str, Any] = {"dialect": conn.dialect.name, "apply": apply, "rows": {}}
    if conn.dialect.name != "postgresql":
        return report
    report["timezone"] = conn.execute(text("SELECT current_setting('TimeZone')")).scalar()
    existing = {
        r[0] for r in conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        ))
    }
    # Order matters only in that every rule is evaluated against runs.started_at,
    # which no rule modifies.
    for name, (count_sql, update_sql, col) in _RULES.items():
        needed = {"runs"} | ({name.split(".")[0]} if not name.startswith("runs.") else set())
        if not needed <= existing:
            report["rows"][name] = 0
            continue
        fix = _FIX.format(col=col)
        sql = (update_sql if apply else count_sql).format(fix=fix)
        result = conn.execute(text(sql))
        report["rows"][name] = int(result.rowcount if apply else result.scalar() or 0)
    if apply and any(report["rows"].values()):
        logger.warning("Repaired naive-written timestamps: %s (server TimeZone=%s)", report["rows"], report["timezone"])
    return report
