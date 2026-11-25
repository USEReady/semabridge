"""Capability-probe evidence: storage and validity (Phase 3.5).

One append-only row per probe run (``databricks_probe_evidence``), keyed by
workspace host + warehouse id. The newest *started* run is the current
evidence, so an older run that finishes late never replaces a newer result.

Validity of a proof (all must hold):
* the run completed and its suite and writer revisions are the current ones;
* it is younger than PROOF_MAX_AGE (30 days);
* it is for this workspace host and warehouse.
Preview and re-plan use the last valid proof without querying the warehouse
(the plan discloses its version and date); deploy (3.8) re-reads the version
via SQL and refuses when it changed.

Effective optional capabilities = proven gates of the valid proof, provided the
baseline gate is proven, intersected with the admin allow-list. An empty
allow-list enables none; admin settings can disable a feature but never force
an unproven one. Proof is platform evidence, never permission to deploy.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Iterable, Optional, Tuple

PROOF_MAX_AGE = timedelta(days=30)
GATES = ("baseline", "one_to_many_joins", "mixed_root_views", "constant_coalesce")
OPTIONAL_GATES = GATES[1:]

_tables_ready: set = set()
_lock = threading.Lock()


def _db():
    from semabridge.repository.orm.session_factory import db_manager

    return db_manager


def _ensure_table() -> None:
    """Create the table on engines Alembic does not manage (local SQLite / DuckDB);
    a no-op on Postgres once sb0007 ran."""
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.project_models import DatabricksProbeEvidence

    engine = _db().get_engine()
    if id(engine) in _tables_ready:
        return
    with _lock:
        if id(engine) not in _tables_ready:
            Base.metadata.create_all(bind=engine, tables=[DatabricksProbeEvidence.__table__], checkfirst=True)
            _tables_ready.add(id(engine))


def _norm_host(host: str) -> str:
    h = str(host or "").strip().lower()
    for prefix in ("https://", "http://"):
        if h.startswith(prefix):
            h = h[len(prefix):]
    return h.rstrip("/")


def record(*, run_id: str, workspace_host: str, warehouse_id: str, probe_schema: str, dbsql_version: Optional[str],
           platform: Dict[str, Any], suite_revision: str, writer_revision: str, outcomes: Dict[str, Any],
           cleanup: Dict[str, Any], started_at: datetime, completed_at: Optional[datetime],
           created_by: Optional[str]) -> None:
    from semabridge.repository.orm.project_models import DatabricksProbeEvidence

    _ensure_table()
    with _db().get_session() as session:
        session.add(DatabricksProbeEvidence(
            run_id=run_id, workspace_host=_norm_host(workspace_host), warehouse_id=str(warehouse_id),
            probe_schema=probe_schema, dbsql_version=dbsql_version, platform_json=json.dumps(platform, default=str),
            suite_revision=suite_revision, writer_revision=writer_revision,
            outcomes_json=json.dumps(outcomes, default=str), cleanup_json=json.dumps(cleanup, default=str),
            started_at=started_at, completed_at=completed_at, created_by=created_by,
        ))


def latest(workspace_host: str, warehouse_id: str) -> Optional[Dict[str, Any]]:
    """The newest-started run for this workspace + warehouse (any outcome)."""
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import DatabricksProbeEvidence as E

    _ensure_table()
    with _db().get_session() as session:
        row = session.execute(
            select(E).where(E.workspace_host == _norm_host(workspace_host), E.warehouse_id == str(warehouse_id))
            .order_by(E.started_at.desc(), E.id.desc()).limit(1)
        ).scalars().first()
        if row is None:
            return None
        return {
            "run_id": row.run_id, "workspace_host": row.workspace_host, "warehouse_id": row.warehouse_id,
            "probe_schema": row.probe_schema, "dbsql_version": row.dbsql_version,
            "platform": json.loads(row.platform_json or "{}"), "suite_revision": row.suite_revision,
            "writer_revision": row.writer_revision, "outcomes": json.loads(row.outcomes_json or "{}"),
            "cleanup": json.loads(row.cleanup_json or "{}"),
            "started_at": _aware(row.started_at), "completed_at": _aware(row.completed_at),
        }


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def validity(ev: Optional[Dict[str, Any]], *, now: Optional[datetime] = None) -> Tuple[bool, str]:
    from semabridge.target_backends.databricks.probes.fixtures import SUITE_REVISION
    from semabridge.target_backends.databricks.writer import WRITER_REVISION

    if ev is None:
        return False, "no probe run recorded for this workspace and warehouse"
    if ev.get("completed_at") is None:
        return False, "the last probe run did not complete"
    if ev.get("suite_revision") != SUITE_REVISION or ev.get("writer_revision") != WRITER_REVISION:
        return False, "the probe suite or the writer changed since the last probe run"
    now = now or datetime.now(timezone.utc)
    if now - ev["completed_at"] > PROOF_MAX_AGE:
        return False, f"the last probe run is older than {PROOF_MAX_AGE.days} days"
    return True, ""


def effective_capabilities(workspace_host: Optional[str], warehouse_id: Optional[str],
                           allow_list: Iterable[str], *, now: Optional[datetime] = None
                           ) -> Tuple[FrozenSet[str], Dict[str, Any]]:
    """(capabilities the planner may use, evidence reference for the plan)."""
    allow = frozenset(str(a) for a in (allow_list or ()))
    ref: Dict[str, Any] = {"status": "none", "reason": "", "allow_list": sorted(allow)}
    if not workspace_host or not warehouse_id:
        ref.update(reason="no Databricks connection: no probe evidence applies")
        return frozenset(), ref
    try:
        ev = latest(workspace_host, warehouse_id)
    except Exception as exc:  # evidence store unavailable: nothing is proven
        ref.update(status="unavailable", reason=f"probe evidence unreadable: {type(exc).__name__}")
        return frozenset(), ref
    ok, why = validity(ev, now=now)
    if ev is not None:
        ref.update(run_id=ev["run_id"], completed_at=str(ev.get("completed_at")), dbsql_version=ev.get("dbsql_version"),
                   warehouse_id=ev["warehouse_id"], workspace_host=ev["workspace_host"],
                   suite_revision=ev["suite_revision"], writer_revision=ev["writer_revision"],
                   gates={g: (ev["outcomes"].get("gates", {}).get(g) or {}).get("outcome") for g in GATES})
    if not ok:
        ref.update(status="not_valid", reason=why)
        return frozenset(), ref
    gates = ev["outcomes"].get("gates", {})
    if (gates.get("baseline") or {}).get("outcome") != "proven":
        ref.update(status="baseline_not_proven", reason="the baseline suite is not proven on this warehouse")
        return frozenset(), ref
    proven = frozenset(g for g in OPTIONAL_GATES if (gates.get(g) or {}).get("outcome") == "proven")
    effective = proven & allow
    ref.update(status="valid", proven=sorted(proven), enabled=sorted(effective),
               not_enabled_by_admin=sorted(proven - allow))
    return effective, ref
