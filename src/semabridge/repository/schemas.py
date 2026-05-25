"""
Shared Pydantic schemas for the SemaBridge repository layer.

These models represent data that crosses the boundary between the raw
database layer and the rest of the application.  Keeping them in a
dedicated module avoids circular imports between ``duckdb_manager``,
``model_repository``, and other modules that need to type-hint these
objects without importing database implementation details.

Canonical location for:
- :class:`Snapshot` — point-in-time semantic model snapshot
- :class:`ModelChange` — granular diff record
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


class Snapshot(BaseModel):
    """A point-in-time snapshot of a semantic model.

    Stored in the ``snapshots`` table and returned by
    :meth:`~semabridge.repository.model_repository.ModelRepository.get_snapshot`.
    """

    snapshot_id: str
    project_id: str
    timestamp: str
    version_tag: Optional[str] = None
    sml_blob: Dict[str, Any]
    status: str = "success"  # success | failed | pending
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    run_id: Optional[str] = None
    sync_mode: str = "copy"  # copy | upsert | etc. - v4.3 rollback metadata


class ModelChange(BaseModel):
    """A granular change record within a snapshot.

    Stored in the ``changes`` table and used by diff/rollback operations.
    """

    object_type: str
    object_name: str
    diff_type: str  # ADDED | MODIFIED | DELETED
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
