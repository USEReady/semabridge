"""Row-level shared state in the ``state_documents`` table.

The compat layer (api/services/project_shared.py) keeps its in-memory dicts
as a cache and persists through this module: only rows whose content digest
changed are written, deletes become tombstones, and ``changed_since`` lets a
process pull what other processes wrote. Last writer wins per row.

All functions return None / False on a database error instead of raising,
so a transient outage degrades to "stale cache" rather than a failed request;
callers that must report a failed save check the return value.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# (collection, key) -> value; a value of None means "deleted".
Change = Tuple[str, str, Any]


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def digest_of(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _worker_id() -> str:
    try:
        from semabridge.core.run_coordination import WORKER_ID

        return WORKER_ID
    except Exception:  # noqa: BLE001
        return "unknown"


def _session():
    from semabridge.repository.orm.session_factory import db_manager

    return db_manager.get_session()


def ensure_table() -> bool:
    """Create the table when missing (SQLite/DuckDB and first start)."""
    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.state_models import StateDocument

        StateDocument.__table__.create(bind=db_manager.get_engine(), checkfirst=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("state_documents table unavailable: %s", exc)
        return False


def is_empty() -> Optional[bool]:
    from sqlalchemy import select

    from semabridge.repository.orm.state_models import StateDocument

    try:
        with _session() as session:
            return session.execute(select(StateDocument.key).limit(1)).first() is None
    except Exception as exc:  # noqa: BLE001
        logger.warning("state_documents not readable: %s", exc)
        return None


def _rows_to_changes(rows: Iterable[Any]) -> List[Tuple[str, str, Any, str, datetime]]:
    out = []
    for row in rows:
        value: Any = None
        if not row.deleted and row.data_json is not None:
            try:
                value = json.loads(row.data_json)
            except (TypeError, ValueError):
                logger.warning("Unreadable state row %s/%s skipped", row.collection, row.key)
                continue
        updated_at = row.updated_at
        if updated_at is not None and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        out.append((row.collection, row.key, value, row.digest, updated_at))
    return out


def load_all() -> Optional[List[Tuple[str, str, Any, str, datetime]]]:
    """Every live row as (collection, key, value, digest, updated_at)."""
    from sqlalchemy import select

    from semabridge.repository.orm.state_models import StateDocument

    try:
        with _session() as session:
            rows = session.execute(select(StateDocument).where(StateDocument.deleted.is_(False))).scalars().all()
            return _rows_to_changes(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load shared state: %s", exc)
        return None


def changed_since(since: datetime, *, exclude_worker: Optional[str] = None) -> Optional[List[Tuple[str, str, Any, str, datetime]]]:
    """Rows (tombstones included, value None) updated at or after ``since``."""
    from sqlalchemy import select

    from semabridge.repository.orm.state_models import StateDocument

    try:
        with _session() as session:
            stmt = select(StateDocument).where(StateDocument.updated_at >= since)
            if exclude_worker:
                stmt = stmt.where((StateDocument.updated_by.is_(None)) | (StateDocument.updated_by != exclude_worker))
            return _rows_to_changes(session.execute(stmt).scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read shared state changes: %s", exc)
        return None


def load_collection(collection: str) -> Optional[Dict[str, Any]]:
    """Every live value of ``collection`` by key (None on error)."""
    from sqlalchemy import select

    from semabridge.repository.orm.state_models import StateDocument

    try:
        with _session() as session:
            rows = session.execute(
                select(StateDocument).where(StateDocument.collection == collection, StateDocument.deleted.is_(False))
            ).scalars().all()
            return {key: value for _c, key, value, _d, _u in _rows_to_changes(rows)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read %s state: %s", collection, exc)
        return None


def load_keys(collection: str, keys: Iterable[str]) -> Dict[str, Any]:
    """Live values of ``keys`` in ``collection`` ({} on error)."""
    from sqlalchemy import select

    from semabridge.repository.orm.state_models import StateDocument

    wanted = [str(k) for k in keys if k]
    if not wanted:
        return {}
    try:
        with _session() as session:
            rows = session.execute(
                select(StateDocument).where(
                    StateDocument.collection == collection,
                    StateDocument.key.in_(wanted),
                    StateDocument.deleted.is_(False),
                )
            ).scalars().all()
            return {key: value for _c, key, value, _d, _u in _rows_to_changes(rows)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read %s state: %s", collection, exc)
        return {}


def apply_changes(changes: Iterable[Change]) -> Optional[Dict[Tuple[str, str], str]]:
    """Upsert values / write tombstones in one transaction.

    Returns {(collection, key): digest} of what was written ("" for a
    tombstone), or None when the transaction failed (nothing was written).
    """
    from semabridge.repository.orm.state_models import StateDocument

    now = datetime.now(timezone.utc)
    worker = _worker_id()
    written: Dict[Tuple[str, str], str] = {}
    batch = list(changes)
    if not batch:
        return written
    try:
        with _session() as session:
            for collection, key, value in batch:
                row = session.get(StateDocument, (collection, str(key)))
                if row is None:
                    row = StateDocument(collection=collection, key=str(key))
                    session.add(row)
                if value is None:
                    row.data_json = None
                    row.digest = ""
                    row.deleted = True
                else:
                    encoded = encode(value)
                    row.data_json = encoded
                    row.digest = digest_of(encoded)
                    row.deleted = False
                row.updated_at = now
                row.updated_by = worker
                written[(collection, str(key))] = row.digest
            session.commit()
        return written
    except Exception as exc:  # noqa: BLE001
        logger.error("Shared state not saved (%d change(s)): %s", len(batch), exc)
        return None
