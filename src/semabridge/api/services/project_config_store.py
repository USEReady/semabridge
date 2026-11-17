"""Authoritative project-config + mapping-override persistence (Postgres).

Before this module a project's config had four writable copies (the
``Config/projects/<id>.yaml`` file, the in-memory ``_compat_project_configs``
dict, the JSON compat store and Postgres) and mapping edits were split between
the JSON store and the YAML ``mappings_overrides`` list, so the copies drifted
(e.g. a mapping reset cleared only the store and the YAML brought the old
names back).

Now:

* ``project_configs`` (one row per saved version) is the only authoritative
  copy of a project's config. :func:`save_project_config` is the ONE writer;
  :func:`get_project_config_text` is the ONE reader.
* ``mapping_overrides`` is the only home of user-chosen target names. The
  ``mappings_overrides`` list a reader sees is always regenerated from it.
* ``_compat_project_configs`` is a read cache of the rendered config
  (stored version + current overrides); the YAML file is an export of the
  same text, written after every change.
* On startup :func:`import_project_config_files` imports any YAML file that
  has no DB row yet (first boot / a new file) and any file that was hand
  edited after the latest DB version (``reason="file_edit"``).

Transient ``preview-*`` (dry-run) projects never reach the DB: their config
lives in the cache only, exactly as before.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

logger = logging.getLogger("semabridge.api.project_config_store")

_tables_ready: set = set()
_tables_lock = threading.Lock()
# One writer at a time per process: version numbers are allocated as
# max(version)+1, the unique (project_id, version) index catches a second
# process racing us.
_write_lock = threading.RLock()


from semabridge.domain.exceptions import InternalError


class ConfigPersistenceError(InternalError):
    """A project config / mapping override could not be persisted (HTTP 500
    with the reason, so the UI reports the failed save)."""


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_transient_project(project_id: Any) -> bool:
    pid = str(project_id or "").strip()
    return not pid or pid == "preview" or pid.startswith("preview-")


def _session_manager():
    from semabridge.repository.orm.session_factory import db_manager

    return db_manager


def _ensure_tables() -> None:
    """Create the two tables on the current engine if missing (fresh SQLite
    test DBs, DuckDB). On Postgres Alembic owns them; this is a no-op there
    once they exist."""
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.project_models import MappingOverride, ProjectConfigVersion

    engine = _session_manager().get_engine()
    key = id(engine)
    if key in _tables_ready:
        return
    with _tables_lock:
        if key in _tables_ready:
            return
        Base.metadata.create_all(
            bind=engine,
            tables=[ProjectConfigVersion.__table__, MappingOverride.__table__],
            checkfirst=True,
        )
        _tables_ready.add(key)


def _sha(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _parse(text: str) -> Dict[str, Any]:
    try:
        parsed = yaml.safe_load(text or "") or {}
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def model_key_for(model_ref: Any) -> str:
    """Prefix-free, case-insensitive model identity ('' = untagged)."""
    text = str(model_ref or "").strip()
    if not text:
        return ""
    from semabridge.api.services.sync_execution_service import _model_match_key

    return _model_match_key(text) or text.lower()


def _shared():
    import semabridge.api.services.project_shared as project_shared

    return project_shared


# ---------------------------------------------------------------------------
# Mapping overrides
# ---------------------------------------------------------------------------

def _override_row_to_dict(row: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "source_path": row.source_path,
        "target_name": row.target_name,
    }
    if row.model_ref or row.model_key:
        out["model"] = row.model_ref or row.model_key
    if row.entity_kind:
        out["entity_kind"] = row.entity_kind
    if row.source_name:
        out["source_name"] = row.source_name
    return out


def list_override_rows(project_id: str) -> List[Any]:
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import MappingOverride

    _ensure_tables()
    with _session_manager().get_session() as session:
        rows = session.execute(
            select(MappingOverride)
            .where(MappingOverride.project_id == str(project_id))
            .order_by(MappingOverride.position.is_(None), MappingOverride.position, MappingOverride.id)
        ).scalars().all()
        session.expunge_all()
    return list(rows)


def list_overrides(project_id: str) -> List[Dict[str, Any]]:
    """The project's overrides in ``mappings_overrides`` row shape."""
    if is_transient_project(project_id):
        return []
    return [_override_row_to_dict(row) for row in list_override_rows(project_id)]


def _normalize_override_input(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    source_path = str(row.get("source_path") or "").strip()
    target_name = str(row.get("target_name") or "").strip()
    if not source_path or not target_name:
        return None
    model_ref = str(row.get("model") or "").strip()
    return {
        "model_key": model_key_for(model_ref),
        "model_ref": model_ref or None,
        "source_path": source_path,
        "target_name": target_name,
        "entity_kind": str(row.get("entity_kind") or "").strip().lower() or None,
        "source_name": str(row.get("source_name") or "").strip() or None,
    }


def upsert_override(
    project_id: str,
    *,
    source_path: str,
    target_name: str,
    model: Any = None,
    entity_kind: Any = None,
    source_name: Any = None,
    user_id: Any = None,
    refresh: bool = True,
) -> Dict[str, Any]:
    """Insert or update one override (identity: project, model_key, source_path)."""
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import MappingOverride

    pid = str(project_id or "").strip()
    norm = _normalize_override_input({
        "source_path": source_path, "target_name": target_name, "model": model,
        "entity_kind": entity_kind, "source_name": source_name,
    })
    if is_transient_project(pid) or norm is None:
        return {}
    _ensure_tables()
    try:
        with _write_lock, _session_manager().get_session() as session:
            existing = session.execute(
                select(MappingOverride).where(
                    MappingOverride.project_id == pid,
                    MappingOverride.model_key == norm["model_key"],
                    MappingOverride.source_path == norm["source_path"],
                )
            ).scalars().first()
            if existing is None:
                existing = MappingOverride(project_id=pid, **norm)
                session.add(existing)
            else:
                existing.target_name = norm["target_name"]
                if norm["model_ref"]:
                    existing.model_ref = norm["model_ref"]
                if norm["entity_kind"]:
                    existing.entity_kind = norm["entity_kind"]
                if norm["source_name"]:
                    existing.source_name = norm["source_name"]
            existing.updated_at = _now()
            existing.updated_by = str(user_id).strip() if user_id else existing.updated_by
    except Exception as exc:
        logger.error("Failed to persist mapping override for %s (%s): %s", pid, norm["source_path"], exc)
        raise ConfigPersistenceError(f"Mapping edit for project '{pid}' could not be saved: {exc}") from exc
    if refresh:
        refresh_project_config(pid)
    return norm


def delete_overrides(project_id: str, *, refresh: bool = True) -> int:
    """Remove every override of a project (mapping reset)."""
    from sqlalchemy import delete
    from semabridge.repository.orm.project_models import MappingOverride

    pid = str(project_id or "").strip()
    if is_transient_project(pid):
        return 0
    _ensure_tables()
    try:
        with _write_lock, _session_manager().get_session() as session:
            result = session.execute(delete(MappingOverride).where(MappingOverride.project_id == pid))
            removed = int(result.rowcount or 0)
    except Exception as exc:
        logger.error("Failed to reset mapping overrides for %s: %s", pid, exc)
        raise ConfigPersistenceError(f"Mapping reset for project '{pid}' could not be saved: {exc}") from exc
    if refresh:
        refresh_project_config(pid)
    return removed


def replace_overrides(
    project_id: str,
    rows: Iterable[Dict[str, Any]],
    *,
    user_id: Any = None,
    session: Any = None,
) -> int:
    """Make the table hold exactly ``rows`` (a YAML ``mappings_overrides``
    list). Unchanged rows keep their ``updated_at``. Later duplicates of one
    identity win (same rule the engine applies to the list)."""
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import MappingOverride

    pid = str(project_id or "").strip()
    wanted: Dict[Tuple[str, str], Dict[str, Any]] = {}
    order: Dict[Tuple[str, str], int] = {}
    for idx, raw in enumerate(rows or []):
        norm = _normalize_override_input(raw)
        if norm is None:
            continue
        key = (norm["model_key"], norm["source_path"])
        wanted[key] = norm
        order[key] = idx

    def _apply(sess: Any) -> int:
        existing = {
            (row.model_key, row.source_path): row
            for row in sess.execute(select(MappingOverride).where(MappingOverride.project_id == pid)).scalars().all()
        }
        changed = 0
        for key, row in existing.items():
            if key not in wanted:
                sess.delete(row)
                changed += 1
        sess.flush()
        now = _now()
        for key, norm in wanted.items():
            row = existing.get(key)
            if row is None:
                sess.add(MappingOverride(
                    project_id=pid, position=order[key], updated_at=now,
                    updated_by=str(user_id).strip() if user_id else None, **norm,
                ))
                changed += 1
                continue
            row.position = order[key]
            dirty = False
            for field in ("target_name", "model_ref", "entity_kind", "source_name"):
                if norm[field] is not None and getattr(row, field) != norm[field]:
                    setattr(row, field, norm[field])
                    dirty = True
            if dirty:
                row.updated_at = now
                if user_id:
                    row.updated_by = str(user_id).strip()
                changed += 1
        return changed

    if session is not None:
        return _apply(session)
    _ensure_tables()
    with _write_lock, _session_manager().get_session() as sess:
        return _apply(sess)


# ---------------------------------------------------------------------------
# Config versions
# ---------------------------------------------------------------------------

def latest_config_row(project_id: str) -> Optional[Any]:
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import ProjectConfigVersion

    _ensure_tables()
    with _session_manager().get_session() as session:
        row = session.execute(
            select(ProjectConfigVersion)
            .where(ProjectConfigVersion.project_id == str(project_id))
            .order_by(ProjectConfigVersion.version.desc())
            .limit(1)
        ).scalars().first()
        if row is not None:
            session.expunge(row)
    return row


def list_config_versions(project_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import ProjectConfigVersion

    _ensure_tables()
    with _session_manager().get_session() as session:
        rows = session.execute(
            select(
                ProjectConfigVersion.version,
                ProjectConfigVersion.created_at,
                ProjectConfigVersion.created_by,
                ProjectConfigVersion.reason,
                ProjectConfigVersion.content_sha256,
            )
            .where(ProjectConfigVersion.project_id == str(project_id))
            .order_by(ProjectConfigVersion.version.desc())
            .limit(limit)
        ).all()
    return [
        {
            "version": r.version,
            "created_at": _as_aware(r.created_at).isoformat() if r.created_at else None,
            "created_by": r.created_by,
            "reason": r.reason,
            "sha256": r.content_sha256,
        }
        for r in rows
    ]


def config_text_at(project_id: str, when: Any) -> str:
    """The config text that was live at ``when`` (datetime or ISO string;
    naive = UTC). '' when no version existed yet."""
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import ProjectConfigVersion

    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if not isinstance(when, datetime):
        return ""
    when = _as_aware(when)
    _ensure_tables()
    with _session_manager().get_session() as session:
        row = session.execute(
            select(ProjectConfigVersion.config_yaml)
            .where(ProjectConfigVersion.project_id == str(project_id))
            .where(ProjectConfigVersion.created_at <= when)
            .order_by(ProjectConfigVersion.version.desc())
            .limit(1)
        ).first()
    return str(row[0]) if row else ""


def project_ids_with_config() -> List[str]:
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import ProjectConfigVersion

    _ensure_tables()
    with _session_manager().get_session() as session:
        rows = session.execute(select(ProjectConfigVersion.project_id).distinct()).all()
    return sorted(str(r[0]) for r in rows if r[0])


def render_config_text(stored_text: str, overrides: List[Dict[str, Any]]) -> str:
    """The stored config with ``mappings_overrides`` regenerated from the
    override table. Rows already in the stored list keep their order; rows
    added later follow. No overrides -> the key is removed (a reset must not
    let the stored list bring old names back)."""
    parsed = _parse(stored_text)
    if not parsed:
        return stored_text
    had_key = "mappings_overrides" in parsed
    if not overrides and not had_key:
        return stored_text
    if overrides:
        parsed["mappings_overrides"] = list(overrides)
    else:
        parsed.pop("mappings_overrides", None)
    return yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)


def _render_for(project_id: str, stored_text: str) -> str:
    try:
        overrides = list_overrides(project_id)
    except Exception as exc:
        logger.error("Could not read mapping overrides of %s: %s", project_id, exc)
        raise ConfigPersistenceError(f"Mapping overrides of '{project_id}' unavailable: {exc}") from exc
    return render_config_text(stored_text, overrides)


def _export_yaml(project_id: str, text: str) -> None:
    """Write the YAML export file. Export only -- a failure is logged at
    ERROR but never fails the save (the DB already holds the version)."""
    import os

    try:
        ps = _shared()
        if "PYTEST_CURRENT_TEST" in os.environ:
            # A test must never export into the real repository Config/ dir
            # (same guard as the compat store path).
            target = ps._compat_project_yaml_path(project_id).resolve()
            repo_config = (ps._compat_repo_root() / "Config").resolve()
            # samefile: "config" and "Config" are one directory on a
            # case-insensitive filesystem.
            if any(p.exists() and repo_config.exists() and os.path.samefile(p, repo_config) for p in target.parents):
                logger.debug("pytest: skipping YAML export of %s into %s", project_id, target)
                return
        ps._compat_save_project_yaml_text(project_id, text)
    except Exception as exc:
        logger.error("Project %s: config saved to the database but the YAML export failed: %s", project_id, exc)


def _merge_legacy_store_edits(project_id: str, session: Any, parsed_cfg: Dict[str, Any]) -> int:
    """One-off: user edits that so far lived only in the JSON mapping store
    (``is_user_edited``) are moved into the override table. They are later
    edits than the YAML list, so they win for the same identity. Target names
    get the same Snowflake sanitizing the sync applied to store edits."""
    from sqlalchemy import select
    from semabridge.repository.orm.project_models import MappingOverride

    ps = _shared()
    target = parsed_cfg.get("target") if isinstance(parsed_cfg.get("target"), dict) else {}
    if not target and isinstance(parsed_cfg.get("targets"), list) and parsed_cfg["targets"]:
        target = parsed_cfg["targets"][0] if isinstance(parsed_cfg["targets"][0], dict) else {}
    sanitize = None
    if str((target or {}).get("type") or "").strip().lower() == "snowflake":
        from semabridge.utils.identifiers import IdentifierSanitizer

        sanitize = IdentifierSanitizer(force_uppercase=True, suppress_reserved=True).to_alias
    edits: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for mapping in list(ps._compat_mappings.values()):
        if not isinstance(mapping, dict) or str(mapping.get("project_id") or "") != project_id:
            continue
        if not bool(mapping.get("is_user_edited")):
            continue
        target = str(mapping.get("target_name") or "").strip()
        if sanitize is not None and target:
            target = sanitize(target)
        norm = _normalize_override_input({
            "source_path": mapping.get("source_path"), "target_name": target,
            "model": mapping.get("model"), "entity_kind": mapping.get("entity_kind"),
            "source_name": mapping.get("source_name"),
        })
        if norm is None:
            continue
        key = (norm["model_key"], norm["source_path"])
        prev = edits.get(key)
        if prev is None or str(mapping.get("updated_at") or "") >= prev["_ts"]:
            edits[key] = {**norm, "_ts": str(mapping.get("updated_at") or "")}
    if not edits:
        return 0
    existing = {
        (row.model_key, row.source_path): row
        for row in session.execute(select(MappingOverride).where(MappingOverride.project_id == project_id)).scalars().all()
    }
    now = _now()
    for key, norm in edits.items():
        norm = {k: v for k, v in norm.items() if k != "_ts"}
        row = existing.get(key)
        if row is None:
            session.add(MappingOverride(project_id=project_id, updated_at=now, updated_by="migration:store", **norm))
        else:
            row.target_name = norm["target_name"]
            row.entity_kind = norm["entity_kind"] or row.entity_kind
            row.source_name = norm["source_name"] or row.source_name
            row.model_ref = row.model_ref or norm["model_ref"]
            row.updated_at = now
            row.updated_by = "migration:store"
    return len(edits)


def save_project_config(
    project_id: str,
    yaml_text: str,
    *,
    reason: str,
    user_id: Any = None,
    sync_overrides: bool = True,
    export: bool = True,
    merge_store_edits: bool = False,
) -> Dict[str, Any]:
    """THE writer for a project's config.

    Appends a version row (skipped when the text is unchanged), makes the
    override table match the YAML's ``mappings_overrides`` when the YAML has
    that key (``sync_overrides``), refreshes the in-memory cache and exports
    the rendered YAML file. Raises :class:`ConfigPersistenceError` when the
    database write fails, so a user action never "succeeds" unsaved.

    Returns ``{"project_id", "version", "config_yaml" (rendered), "created"}``.
    """
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError
    from semabridge.repository.orm.project_models import ProjectConfigVersion

    pid = str(project_id or "").strip()
    text = str(yaml_text or "")
    if not pid:
        raise ConfigPersistenceError("project_id is required")
    ps = _shared()
    if is_transient_project(pid):
        with ps.compat_store_lock():
            ps._compat_project_configs[pid] = text
        return {"project_id": pid, "version": None, "config_yaml": text, "created": False}

    parsed = _parse(text)
    _ensure_tables()
    created = False
    version: Optional[int] = None
    migrated_store_edits = 0
    last_exc: Optional[BaseException] = None
    for _attempt in range(3):
        try:
            with _write_lock, _session_manager().get_session() as session:
                latest = session.execute(
                    select(ProjectConfigVersion)
                    .where(ProjectConfigVersion.project_id == pid)
                    .order_by(ProjectConfigVersion.version.desc())
                    .limit(1)
                ).scalars().first()
                if sync_overrides and isinstance(parsed.get("mappings_overrides"), list):
                    replace_overrides(pid, parsed.get("mappings_overrides") or [], user_id=user_id, session=session)
                if merge_store_edits:
                    migrated_store_edits = _merge_legacy_store_edits(pid, session, parsed)
                digest = _sha(text)
                if latest is not None and latest.content_sha256 == digest:
                    version = latest.version
                else:
                    next_version = int(session.execute(
                        select(func.coalesce(func.max(ProjectConfigVersion.version), 0))
                        .where(ProjectConfigVersion.project_id == pid)
                    ).scalar() or 0) + 1
                    session.add(ProjectConfigVersion(
                        project_id=pid,
                        version=next_version,
                        config_yaml=text,
                        content_sha256=digest,
                        created_at=_now(),
                        created_by=str(user_id).strip() if user_id else None,
                        reason=str(reason or "save")[:64],
                    ))
                    version = next_version
                    created = True
            last_exc = None
            break
        except IntegrityError as exc:  # concurrent writer took the version number
            last_exc = exc
            continue
        except Exception as exc:
            last_exc = exc
            break
    if last_exc is not None:
        logger.error("Failed to save config of project %s (reason=%s): %s", pid, reason, last_exc)
        raise ConfigPersistenceError(f"Project '{pid}' configuration could not be saved: {last_exc}") from last_exc

    rendered = _render_for(pid, text)
    with ps.compat_store_lock():
        ps._compat_project_configs[pid] = rendered
    if export:
        _export_yaml(pid, rendered)
    if created:
        logger.info("Project %s config saved as version %s (reason=%s)", pid, version, reason)
    if migrated_store_edits:
        logger.info("Project %s: moved %d mapping-store edit(s) into mapping_overrides", pid, migrated_store_edits)
    return {"project_id": pid, "version": version, "config_yaml": rendered, "created": created,
            "migrated_store_edits": migrated_store_edits}


def refresh_project_config(project_id: str, *, export: bool = True) -> str:
    """Re-render the live config after an override change: refresh the cache
    and the YAML export. Returns the rendered text ('' when no version)."""
    pid = str(project_id or "").strip()
    if is_transient_project(pid):
        return ""
    row = latest_config_row(pid)
    if row is None:
        return ""
    rendered = _render_for(pid, row.config_yaml)
    ps = _shared()
    with ps.compat_store_lock():
        ps._compat_project_configs[pid] = rendered
    if export:
        _export_yaml(pid, rendered)
    return rendered


def get_project_config_text(project_id: str, *, use_cache: bool = True) -> str:
    """THE reader: the live config of a project (stored version + current
    overrides). Order: cache -> database -> legacy YAML file (imported into
    the database on first sight). '' when the project has no config."""
    pid = str(project_id or "").strip()
    if not pid:
        return ""
    ps = _shared()
    if use_cache or is_transient_project(pid):
        cached = ps._compat_project_configs.get(pid)
        if isinstance(cached, str) and cached.strip():
            return cached
    if is_transient_project(pid):
        return ""
    try:
        row = latest_config_row(pid)
    except Exception as exc:
        logger.error("Config of project %s could not be read from the database: %s", pid, exc)
        row = None
        # Database unavailable: serve the last export rather than nothing.
        return ps._compat_load_project_yaml_text(pid) or ""
    if row is not None:
        rendered = _render_for(pid, row.config_yaml)
        with ps.compat_store_lock():
            ps._compat_project_configs[pid] = rendered
        return rendered
    if pid in ps._compat_deleted_project_ids:
        return ""
    file_text = ps._compat_load_project_yaml_text(pid)
    if file_text.strip():
        logger.info("Project %s: importing config file into the database (first read)", pid)
        try:
            return save_project_config(pid, file_text, reason="import", export=False, merge_store_edits=True)["config_yaml"]
        except ConfigPersistenceError:
            return file_text
    return ""


def delete_project_config(project_id: str) -> Dict[str, int]:
    """User-requested project delete: drop its config versions and overrides."""
    from sqlalchemy import delete
    from semabridge.repository.orm.project_models import MappingOverride, ProjectConfigVersion

    pid = str(project_id or "").strip()
    if is_transient_project(pid):
        return {"versions": 0, "overrides": 0}
    _ensure_tables()
    with _write_lock, _session_manager().get_session() as session:
        versions = session.execute(delete(ProjectConfigVersion).where(ProjectConfigVersion.project_id == pid)).rowcount
        overrides = session.execute(delete(MappingOverride).where(MappingOverride.project_id == pid)).rowcount
    return {"versions": int(versions or 0), "overrides": int(overrides or 0)}


# ---------------------------------------------------------------------------
# Startup import (file -> DB)
# ---------------------------------------------------------------------------

def _file_mtime(path: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _latest_override_change(project_id: str) -> Optional[datetime]:
    from sqlalchemy import func, select
    from semabridge.repository.orm.project_models import MappingOverride

    with _session_manager().get_session() as session:
        value = session.execute(
            select(func.max(MappingOverride.updated_at)).where(MappingOverride.project_id == project_id)
        ).scalar()
    return _as_aware(value)


def import_project_config_files(legacy_store_configs: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Bring the database up to date with ``Config/projects/*.yaml``.

    * a file whose project has no DB version yet is imported (``import``),
      together with the project's legacy mapping-store edits;
    * a file modified after the latest DB version (and the latest override
      change) whose content differs from what the DB would export is treated
      as a hand edit and imported as a new version (``file_edit``);
    * a real project that exists only in the legacy JSON store (no file, no
      DB row) is imported from there (``import_store``).
    Files of deleted or preview projects are ignored. Returns a report.
    """
    ps = _shared()
    report: Dict[str, Any] = {"imported": [], "file_edits": [], "imported_from_store": [], "unchanged": [], "errors": []}
    seen: set = set()
    for projects_dir in ps._compat_projects_dirs():
        if not projects_dir.exists():
            continue
        for path in sorted(projects_dir.glob("*.y*ml")):
            pid = path.stem.strip()
            if not pid or pid in seen or is_transient_project(pid) or pid in ps._compat_deleted_project_ids:
                continue
            seen.add(pid)
            try:
                file_text = path.read_text(encoding="utf-8")
                if not file_text.strip():
                    continue
                latest = latest_config_row(pid)
                if latest is None:
                    result = save_project_config(pid, file_text, reason="import", export=False, merge_store_edits=True)
                    report["imported"].append({"project_id": pid, "version": result["version"],
                                               "store_edits": result.get("migrated_store_edits", 0)})
                    if _parse(result["config_yaml"]) != _parse(file_text):
                        _export_yaml(pid, result["config_yaml"])
                    continue
                rendered = _render_for(pid, latest.config_yaml)
                if _parse(rendered) == _parse(file_text):
                    report["unchanged"].append(pid)
                    continue
                mtime = _file_mtime(path)
                db_changed = max(
                    [t for t in (_as_aware(latest.created_at), _latest_override_change(pid)) if t is not None],
                    default=None,
                )
                if mtime is not None and (db_changed is None or mtime > db_changed):
                    result = save_project_config(pid, file_text, reason="file_edit", export=False)
                    logger.warning(
                        "Project %s: %s was edited by hand after the last saved version; imported it as version %s",
                        pid, path, result["version"],
                    )
                    report["file_edits"].append({"project_id": pid, "version": result["version"], "file": str(path)})
                else:
                    # The DB is newer (e.g. an export failed earlier): re-export.
                    _export_yaml(pid, rendered)
                    report["unchanged"].append(pid)
            except Exception as exc:  # keep importing the other projects
                logger.error("Project %s: config file import failed: %s", pid, exc)
                report["errors"].append({"project_id": pid, "error": str(exc)})

    for pid, text in sorted((legacy_store_configs or {}).items()):
        pid = str(pid or "").strip()
        if (
            not pid or pid in seen or is_transient_project(pid)
            or pid in ps._compat_deleted_project_ids or not str(text or "").strip()
        ):
            continue
        try:
            if latest_config_row(pid) is not None:
                continue
            result = save_project_config(pid, str(text), reason="import_store", merge_store_edits=True)
            report["imported_from_store"].append({"project_id": pid, "version": result["version"]})
        except Exception as exc:
            logger.error("Project %s: legacy store config import failed: %s", pid, exc)
            report["errors"].append({"project_id": pid, "error": str(exc)})
    return report


def load_all_configs_into_cache() -> List[str]:
    """Fill ``_compat_project_configs`` with every project's rendered live
    config from the database. Returns the project ids loaded."""
    from sqlalchemy import func, select
    from semabridge.repository.orm.project_models import ProjectConfigVersion

    ps = _shared()
    _ensure_tables()
    with _session_manager().get_session() as session:
        latest_sq = (
            select(ProjectConfigVersion.project_id, func.max(ProjectConfigVersion.version).label("v"))
            .group_by(ProjectConfigVersion.project_id)
            .subquery()
        )
        rows = session.execute(
            select(ProjectConfigVersion.project_id, ProjectConfigVersion.config_yaml)
            .join(latest_sq, (ProjectConfigVersion.project_id == latest_sq.c.project_id)
                  & (ProjectConfigVersion.version == latest_sq.c.v))
        ).all()
    loaded: List[str] = []
    for pid, text in rows:
        if pid in ps._compat_deleted_project_ids:
            continue
        rendered = _render_for(pid, text)
        with ps.compat_store_lock():
            ps._compat_project_configs[pid] = rendered
        loaded.append(pid)
    return loaded
