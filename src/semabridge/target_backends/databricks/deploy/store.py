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
LEASE_TTL = timedelta(minutes=5)

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
                m.DatabricksDeployLease.__table__])
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
    """{normalized fqn: newest record} over this model's successful runs."""
    from sqlalchemy import select

    m = _models()
    O = m.DatabricksDeployedObject
    _ensure_tables()
    out: Dict[str, Dict[str, Any]] = {}
    with _db().get_session() as s:
        runs = _successful_runs(s, project_id, model_key)
        if not runs:
            return out
        rows = s.execute(select(O).where(O.project_id == str(project_id), O.model_key == str(model_key or ""),
                                         O.run_id.in_(runs)).order_by(O.recorded_at.desc(), O.id.desc())).scalars()
        for r in rows:
            if r.fqn in out:
                continue
            out[r.fqn] = {"kind": r.kind, "fqn": r.fqn, "source": r.source, "uc_object_id": r.uc_object_id,
                          "body_sha256": r.body_sha256, "definition_hash": r.definition_hash,
                          "readiness": r.readiness, "run_id": r.run_id, "outcome": r.outcome,
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
    """The names of the newest successful run that recorded any (one run's set,
    so a view that is no longer deployed does not keep reserving names)."""
    from sqlalchemy import select

    m = _models()
    N = m.DatabricksDeployedName
    _ensure_tables()
    with _db().get_session() as s:
        for run in _successful_runs(s, project_id, model_key):
            rows = s.execute(select(N).where(N.run_id == run).order_by(N.id)).scalars().all()
            if rows:
                return [{"view_name": r.view_name, "view_source": r.view_source, "kind": r.kind,
                         "source_key": json.loads(r.source_key_json or "{}"), "target_name": r.target_name}
                        for r in rows]
    return []


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
                "catalog": r.catalog, "schema": r.schema_name, "plan_revision": r.plan_revision}


def journal(run_id: str) -> List[Dict[str, Any]]:
    from sqlalchemy import select

    m = _models()
    J = m.DatabricksDeployJournal
    _ensure_tables()
    with _db().get_session() as s:
        rows = s.execute(select(J).where(J.run_id == run_id).order_by(J.seq)).scalars().all()
        return [{"seq": r.seq, "kind": r.kind, "target_fqn": r.target_fqn, "state": r.state,
                 "statement_id": r.statement_id, "error": r.error} for r in rows]


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
