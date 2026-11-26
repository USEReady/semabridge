"""Deploy state in the app database (Phase 3.8): runs, deployed objects,
deployed names, the operation journal and the per-schema lease.

* The record is the primary ownership evidence: an object is SemaBridge's own
  only if a successful run recorded it (with its Unity Catalog id when one was
  available). Comments and table properties are supporting evidence only.
* The journal is written BEFORE each Databricks write. Anything left
  ``intended`` / ``submitted`` / ``uncertain`` must be reconciled against
  Databricks before another deploy touches that schema.
* The lease is renewable and keyed by metastore + catalog + schema.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

OPEN_STATES = ("intended", "submitted", "uncertain")
STALE_RUN_AFTER = timedelta(minutes=30)
# Object outcomes that are verified deployment states (read back after the write,
# or confirmed by reconciliation with statement evidence).
VERIFIED_OUTCOMES = ("created", "updated", "verified", "reconciled")
LEASE_TTL = timedelta(minutes=15)       # > the 300 s write deadline

_tables_ready: set = set()
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _db():
    from semabridge.repository.orm.session_factory import db_manager

    return db_manager


def _models():
    from semabridge.repository.orm import project_models as m

    return m


def _ensure_tables() -> None:
    """Create the tables on engines Alembic does not manage (local SQLite /
    DuckDB); a no-op on Postgres once sb0008 ran."""
    from semabridge.repository.orm.base import Base

    m = _models()
    engine = _db().get_engine()
    if id(engine) in _tables_ready:
        return
    with _lock:
        if id(engine) not in _tables_ready:
            Base.metadata.create_all(bind=engine, checkfirst=True, tables=[
                m.DatabricksDeployment.__table__, m.DatabricksDeployedObject.__table__,
                m.DatabricksDeployedName.__table__, m.DatabricksDeployJournal.__table__,
                m.DatabricksDeployLease.__table__, m.DatabricksDeployIntent.__table__])
            _tables_ready.add(id(engine))


def lease_key(metastore_id: Optional[str], catalog: str, schema: str) -> str:
    return f"{(metastore_id or 'unknown-metastore').lower()}|{str(catalog).lower()}|{str(schema).lower()}"


# --- runs ----------------------------------------------------------------------------------

def start_run(*, run_id: str, project_id: str, model_key: str, plan_revision: int, writer_revision: str,
              catalog: str, schema: str, stub_form: str, dbsql_version: Optional[str] = None,
              metastore_id: Optional[str] = None, workspace_host: Optional[str] = None,
              warehouse_id: Optional[str] = None, identity: Optional[str] = None) -> None:
    m = _models()
    _ensure_tables()
    with _db().get_session() as s:
        s.add(m.DatabricksDeployment(
            run_id=run_id, project_id=str(project_id), model_key=str(model_key or ""), plan_revision=int(plan_revision),
            writer_revision=str(writer_revision), dbsql_version=dbsql_version, metastore_id=metastore_id,
            catalog=catalog, schema_name=schema, workspace_host=workspace_host, warehouse_id=warehouse_id,
            identity=identity, stub_form=stub_form, status="running", started_at=_now()))


def finish_run(run_id: str, status: str, summary: Dict[str, Any]) -> None:
    from sqlalchemy import select

    m = _models()
    _ensure_tables()
    with _db().get_session() as s:
        row = s.execute(select(m.DatabricksDeployment).where(m.DatabricksDeployment.run_id == run_id)).scalars().first()
        if row is not None:
            row.status = status
            row.summary_json = json.dumps(summary, default=str)
            row.finished_at = _now()


def _successful_runs(s, project_id: str, model_key: str) -> List[str]:
    from sqlalchemy import select

    m = _models()
    D = m.DatabricksDeployment
    rows = s.execute(select(D.run_id).where(D.project_id == str(project_id), D.model_key == str(model_key or ""),
                                           D.status.in_(("succeeded", "partial", "no_op")))
                     .order_by(D.started_at.desc(), D.id.desc())).all()
    return [r[0] for r in rows]


# --- deployed objects -------------------------------------------------------------------------

def record_object(*, run_id: str, project_id: str, model_key: str, kind: str, fqn: str, outcome: str,
                  source: Optional[str] = None, uc_object_id: Optional[str] = None,
                  body_sha256: Optional[str] = None, definition_hash: Optional[str] = None,
                  readiness: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    from semabridge.target_backends.databricks.guard import normalize_name

    m = _models()
    _ensure_tables()
    with _db().get_session() as s:
        s.add(m.DatabricksDeployedObject(
            run_id=run_id, project_id=str(project_id), model_key=str(model_key or ""), kind=kind,
            fqn=normalize_name(fqn), source=source, uc_object_id=uc_object_id, body_sha256=body_sha256,
            definition_hash=definition_hash, readiness=readiness, outcome=outcome,
            details_json=json.dumps(details or {}, default=str), recorded_at=_now()))


def owned_objects(project_id: str, model_key: str) -> Dict[str, Dict[str, Any]]:
    """{normalized fqn: the object's LATEST state} -- per object, whatever the run's
    overall status (a partial run's successful objects count; unchanged objects are
    re-recorded as ``verified``). ``verified`` is False when the latest state is not
    a verified one (e.g. ``applied_unverified``): such an object is never mutated
    automatically. Raises when the store cannot be read."""
    from sqlalchemy import select

    m = _models()
    O = m.DatabricksDeployedObject
    _ensure_tables()
    out: Dict[str, Dict[str, Any]] = {}
    with _db().get_session() as s:
        rows = s.execute(select(O).where(O.project_id == str(project_id), O.model_key == str(model_key or ""))
                         .order_by(O.recorded_at.desc(), O.id.desc())).scalars()
        for r in rows:
            if r.fqn in out:
                continue
            out[r.fqn] = {"kind": r.kind, "fqn": r.fqn, "source": r.source, "uc_object_id": r.uc_object_id,
                          "body_sha256": r.body_sha256, "definition_hash": r.definition_hash,
                          "readiness": r.readiness, "run_id": r.run_id, "outcome": r.outcome,
                          "verified": r.outcome in VERIFIED_OUTCOMES,
                          "details": json.loads(r.details_json or "{}")}
    return out


# --- deployed names (A5) ------------------------------------------------------------------------

def record_names(*, run_id: str, project_id: str, model_key: str, names: Iterable[Dict[str, Any]]) -> None:
    m = _models()
    _ensure_tables()
    now = _now()
    with _db().get_session() as s:
        for n in names:
            s.add(m.DatabricksDeployedName(
                run_id=run_id, project_id=str(project_id), model_key=str(model_key or ""),
                view_name=n.get("view_name"), view_source=n.get("view_source"), kind=n["kind"],
                source_key_json=json.dumps(n.get("source_key") or {}, sort_keys=True, default=str),
                target_name=str(n["target_name"]), recorded_at=now))


def deployed_names(project_id: str, model_key: str) -> List[Dict[str, Any]]:
    """EVERY name ever deployed for this model, oldest first (rows are written only
    for verified deployments). Cumulative: names of views or measures that were
    later removed stay in the history and stay reserved. Raises when unreadable."""
    from sqlalchemy import select

    m = _models()
    N = m.DatabricksDeployedName
    _ensure_tables()
    with _db().get_session() as s:
        rows = s.execute(select(N).where(N.project_id == str(project_id), N.model_key == str(model_key or ""))
                         .order_by(N.recorded_at, N.id)).scalars().all()
        return [{"view_name": r.view_name, "view_source": r.view_source, "kind": r.kind, "run_id": r.run_id,
                 "source_key": json.loads(r.source_key_json or "{}"), "target_name": r.target_name}
                for r in rows]


# --- journal ------------------------------------------------------------------------------------

def journal_intend(*, run_id: str, seq: int, destination: str, kind: str, target_fqn: str, statement_sha256: str,
                   definition_hash: Optional[str] = None, previous_hash: Optional[str] = None) -> None:
    m = _models()
    _ensure_tables()
    with _db().get_session() as s:
        s.add(m.DatabricksDeployJournal(
            run_id=run_id, seq=int(seq), destination=destination, kind=kind, target_fqn=target_fqn,
            statement_sha256=statement_sha256, definition_hash=definition_hash, previous_hash=previous_hash,
            state="intended", updated_at=_now()))


def journal_update(run_id: str, seq: int, state: str, *, statement_id: Optional[str] = None,
                   error: Optional[str] = None) -> None:
    from sqlalchemy import select

    m = _models()
    J = m.DatabricksDeployJournal
    _ensure_tables()
    with _db().get_session() as s:
        row = s.execute(select(J).where(J.run_id == run_id, J.seq == int(seq))).scalars().first()
        if row is None:
            raise KeyError(f"no journal entry {run_id}#{seq}")
        row.state = state
        if statement_id:
            row.statement_id = statement_id
        if error is not None:
            row.error = error[:4000]
        row.updated_at = _now()


def open_operations(destination: str) -> List[Dict[str, Any]]:
    """Journal entries for this schema whose outcome is not known."""
    from sqlalchemy import select

    m = _models()
    J = m.DatabricksDeployJournal
    _ensure_tables()
    with _db().get_session() as s:
        rows = s.execute(select(J).where(J.destination == destination, J.state.in_(OPEN_STATES))
                         .order_by(J.updated_at, J.id)).scalars().all()
        return [{"run_id": r.run_id, "seq": r.seq, "kind": r.kind, "target_fqn": r.target_fqn,
                 "statement_sha256": r.statement_sha256, "definition_hash": r.definition_hash,
                 "previous_hash": r.previous_hash, "state": r.state, "statement_id": r.statement_id}
                for r in rows]


def leftover_staging(destination: str) -> List[Dict[str, Any]]:
    """Staging copies whose CREATE definitely succeeded (statement evidence in the
    journal) but whose DROP never succeeded -- left behind by a stopped run."""
    from sqlalchemy import select

    m = _models()
    J = m.DatabricksDeployJournal
    _ensure_tables()
    with _db().get_session() as s:
        rows = s.execute(select(J).where(J.destination == destination,
                                         J.kind.in_(("create_staging_view", "drop_staging_view")))
                         .order_by(J.updated_at, J.id)).scalars().all()
        created: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            if r.kind == "create_staging_view" and r.state == "succeeded" and r.statement_id:
                created[r.target_fqn] = {"run_id": r.run_id, "seq": r.seq, "target_fqn": r.target_fqn,
                                         "definition_hash": r.definition_hash, "statement_id": r.statement_id}
            elif r.kind == "drop_staging_view" and r.state == "succeeded":
                created.pop(r.target_fqn, None)
        return list(created.values())


def intent_for_run(run_id: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select

    m = _models()
    I = m.DatabricksDeployIntent
    _ensure_tables()
    with _db().get_session() as s:
        iid = s.execute(select(I.intent_id).where(I.run_id == run_id)).scalars().first()
    return get_intent(iid) if iid else None


def sweep_stale_runs(project_id: str, model_key: str, *, now: Optional[datetime] = None) -> List[str]:
    """Runs left 'running' by a process that stopped (no live lease, started long
    ago) become 'uncertain'; their open journal entries then require Reconcile."""
    from sqlalchemy import select

    m = _models()
    D, L = m.DatabricksDeployment, m.DatabricksDeployLease
    _ensure_tables()
    now = now or _now()
    swept: List[str] = []
    with _db().get_session() as s:
        rows = s.execute(select(D).where(D.project_id == str(project_id), D.model_key == str(model_key or ""),
                                         D.status == "running")).scalars().all()
        for r in rows:
            held = s.execute(select(L).where(L.holder_run_id == r.run_id)).scalars().all()
            if any(_aware(x.expires_at) >= now for x in held):
                continue
            if now - _aware(r.started_at) < STALE_RUN_AFTER:
                continue
            r.status = "uncertain"
            r.summary_json = json.dumps({"reason": "the deploy process stopped before finishing: reconcile"})
            r.finished_at = now
            swept.append(r.run_id)
    return swept


def run_info(run_id: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select

    m = _models()
    D = m.DatabricksDeployment
    _ensure_tables()
    with _db().get_session() as s:
        r = s.execute(select(D).where(D.run_id == run_id)).scalars().first()
        if r is None:
            return None
        return {"run_id": r.run_id, "project_id": r.project_id, "model_key": r.model_key, "status": r.status,
                "catalog": r.catalog, "schema": r.schema_name, "plan_revision": r.plan_revision,
                "identity": r.identity, "warehouse_id": r.warehouse_id,
                "started_at": _aware(r.started_at), "finished_at": _aware(r.finished_at),
                "summary": json.loads(r.summary_json) if r.summary_json else None}


def journal(run_id: str) -> List[Dict[str, Any]]:
    from sqlalchemy import select

    m = _models()
    J = m.DatabricksDeployJournal
    _ensure_tables()
    with _db().get_session() as s:
        rows = s.execute(select(J).where(J.run_id == run_id).order_by(J.seq)).scalars().all()
        return [{"seq": r.seq, "kind": r.kind, "target_fqn": r.target_fqn, "state": r.state,
                 "statement_id": r.statement_id, "error": r.error} for r in rows]


# --- deployment intents (3.8b) ------------------------------------------------------------------

INTENT_TTL = timedelta(minutes=30)


def save_intent(*, intent_id: str, run_id: str, project_id: str, model_key: str, plan_revision: int,
                fingerprint: str, intent: Dict[str, Any], created_by: Optional[str],
                now: Optional[datetime] = None, ttl: timedelta = INTENT_TTL) -> Dict[str, Any]:
    m = _models()
    _ensure_tables()
    now = now or _now()
    with _db().get_session() as s:
        s.add(m.DatabricksDeployIntent(
            intent_id=intent_id, run_id=run_id, project_id=str(project_id), model_key=str(model_key or ""),
            plan_revision=int(plan_revision), fingerprint=fingerprint,
            intent_json=json.dumps(intent, default=str, sort_keys=True), status="open",
            created_by=created_by, created_at=now, expires_at=now + ttl))
    return {"intent_id": intent_id, "run_id": run_id, "expires_at": (now + ttl).isoformat()}


def get_intent(intent_id: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select

    m = _models()
    I = m.DatabricksDeployIntent
    _ensure_tables()
    with _db().get_session() as s:
        r = s.execute(select(I).where(I.intent_id == intent_id)).scalars().first()
        if r is None:
            return None
        return {"intent_id": r.intent_id, "run_id": r.run_id, "project_id": r.project_id, "model_key": r.model_key,
                "plan_revision": r.plan_revision, "fingerprint": r.fingerprint,
                "intent": json.loads(r.intent_json or "{}"), "status": r.status, "created_by": r.created_by,
                "created_at": _aware(r.created_at), "expires_at": _aware(r.expires_at),
                "used_at": _aware(r.used_at), "used_by": r.used_by}


def set_intent_status(intent_id: str, status: str) -> None:
    from sqlalchemy import update

    m = _models()
    I = m.DatabricksDeployIntent
    _ensure_tables()
    with _db().get_session() as s:
        s.execute(update(I).where(I.intent_id == intent_id, I.status == "open").values(status=status))


def consume_intent(intent_id: str, *, used_by: Optional[str], run: Dict[str, Any],
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """Atomically: mark the intent used AND create its run row (one transaction).

    Returns {"outcome": "started" | "existing" | "expired" | "invalidated" | "missing", "run_id"}.
    A repeated confirmation of a used intent returns the existing run id."""
    from sqlalchemy import select, update

    m = _models()
    I, Dp = m.DatabricksDeployIntent, m.DatabricksDeployment
    _ensure_tables()
    now = now or _now()
    with _db().get_session() as s:
        row = s.execute(select(I).where(I.intent_id == intent_id)).scalars().first()
        if row is None:
            return {"outcome": "missing", "run_id": None}
        if row.status == "used":
            return {"outcome": "existing", "run_id": row.run_id}
        if row.status != "open":
            return {"outcome": row.status, "run_id": None}
        if _aware(row.expires_at) < now:
            row.status = "expired"
            return {"outcome": "expired", "run_id": None}
        res = s.execute(update(I).where(I.intent_id == intent_id, I.status == "open")
                        .values(status="used", used_at=now, used_by=used_by))
        if (res.rowcount or 0) != 1:                      # someone else consumed it in between
            s.rollback()
            again = s.execute(select(I.run_id, I.status).where(I.intent_id == intent_id)).first()
            return {"outcome": "existing" if again and again[1] == "used" else "invalidated",
                    "run_id": again[0] if again else None}
        s.add(Dp(run_id=row.run_id, project_id=row.project_id, model_key=row.model_key,
                 plan_revision=row.plan_revision, writer_revision=str(run.get("writer_revision") or ""),
                 dbsql_version=run.get("dbsql_version"), metastore_id=run.get("metastore_id"),
                 catalog=str(run["catalog"]), schema_name=str(run["schema"]),
                 workspace_host=run.get("workspace_host"), warehouse_id=run.get("warehouse_id"),
                 identity=run.get("identity"), stub_form=str(run.get("stub_form") or "empty_table"),
                 status="running", started_at=now))
        return {"outcome": "started", "run_id": row.run_id}


# --- lease --------------------------------------------------------------------------------------

def acquire_lease(key: str, run_id: str, *, ttl: timedelta = LEASE_TTL, now: Optional[datetime] = None) -> bool:
    """Take the lease if it is free, expired, or already ours. Atomic: the row is
    the lock (primary key); an expired holder is replaced with a conditional update."""
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    m = _models()
    L = m.DatabricksDeployLease
    _ensure_tables()
    now = now or _now()
    try:
        with _db().get_session() as s:
            s.add(L(lease_key=key, holder_run_id=run_id, acquired_at=now, expires_at=now + ttl))
        return True
    except IntegrityError:
        pass
    with _db().get_session() as s:
        res = s.execute(update(L).where(L.lease_key == key)
                        .where((L.holder_run_id == run_id) | (L.expires_at < now))
                        .values(holder_run_id=run_id, acquired_at=now, expires_at=now + ttl))
        return (res.rowcount or 0) == 1


def renew_lease(key: str, run_id: str, *, ttl: timedelta = LEASE_TTL, now: Optional[datetime] = None) -> bool:
    """Extend our lease; False when it expired and someone else took it (stop writing)."""
    from sqlalchemy import update

    m = _models()
    L = m.DatabricksDeployLease
    _ensure_tables()
    now = now or _now()
    with _db().get_session() as s:
        res = s.execute(update(L).where(L.lease_key == key, L.holder_run_id == run_id, L.expires_at >= now)
                        .values(expires_at=now + ttl))
        return (res.rowcount or 0) == 1


def holds_lease(key: str, run_id: str, *, now: Optional[datetime] = None) -> bool:
    from sqlalchemy import select

    m = _models()
    L = m.DatabricksDeployLease
    _ensure_tables()
    now = now or _now()
    with _db().get_session() as s:
        row = s.execute(select(L).where(L.lease_key == key)).scalars().first()
        return bool(row is not None and row.holder_run_id == run_id and _aware(row.expires_at) >= now)


def release_lease(key: str, run_id: str) -> None:
    from sqlalchemy import delete

    m = _models()
    L = m.DatabricksDeployLease
    _ensure_tables()
    with _db().get_session() as s:
        s.execute(delete(L).where(L.lease_key == key, L.holder_run_id == run_id))
