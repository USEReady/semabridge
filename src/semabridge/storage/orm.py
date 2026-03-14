"""
Storage ORM Bridge — semabridge-merged compatibility shim.

All ORM models and session management have been consolidated into
``semabridge.repository.orm`` (the mature SQLAlchemy 2.0 layer from
semabridge-pbix with DatabaseManager rotation support, Alembic migrations,
and full type annotations).

This module re-exports the names that semabridge-qt code used from
``semabridge.storage.orm`` so that:
  - ``orchestration/temporal/activities.py``
  - ``distributed/ray/actor_diff.py``
  - Any external callers

...all continue to work with **zero import changes**.

Usage (unchanged from semabridge-qt):
    from semabridge.storage.orm import get_session, SnapshotRow, SyncRunRow

    with get_session() as session:
        row = SyncRunRow(tenant_id="acme", model_id="sales")
        session.add(row)
        session.commit()
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Re-export all ORM models from the canonical repository layer
# ---------------------------------------------------------------------------
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import (
    SnapshotRow,
    SyncRunRow,
    ModelVersion as VersionRow,   # qt called this VersionRow; pbix calls it ModelVersion
    Project,
    Run,
)

# ---------------------------------------------------------------------------
# Re-export session helpers via the rotation-aware DatabaseManager
# ---------------------------------------------------------------------------
from semabridge.repository.orm.session_factory import (
    db_manager,
    get_engine,
    get_session_factory,
    reset_engine,
)

from contextlib import contextmanager
from typing import Generator, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@contextmanager
def get_session(database_url: Optional[str] = None) -> Generator["Session", None, None]:
    """Context-managed ORM session (backward-compat shim for semabridge-qt).

    Delegates to :meth:`~semabridge.repository.orm.session_factory.DatabaseManager.get_session`
    on the process-wide ``db_manager`` singleton.  All rotation detection,
    pool management, and SSL configuration from ``DatabaseManager`` applies.

    Args:
        database_url: Optional URL override (for tests or one-off connections).
            When ``None``, the standard resolution chain is used.

    Yields:
        An open SQLAlchemy ``Session``.
    """
    with db_manager.get_session(url_override=database_url) as session:
        yield session


__all__ = [
    # Base class
    "Base",
    # ORM models
    "SnapshotRow",
    "SyncRunRow",
    "VersionRow",
    "Project",
    "Run",
    # Session helpers
    "get_session",
    "get_engine",
    "get_session_factory",
    "reset_engine",
    "db_manager",
]
