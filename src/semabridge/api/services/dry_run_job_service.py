"""Multi-model background dry-run jobs: background-job-plus-polling wrapper
around the existing _run_dry_run_pipeline() pipeline call.

Generic over source types: a job fans ``selected_sources`` out into one
DryRunJobFile per entry -- a full ``.pbix`` path for ``source.type: pbix``, a
semantic model name for ``source.type: fabric``. Every file is dry-run by the
exact same shared pipeline (mappings_controller._run_dry_run_pipeline) with
``selected_sources=[that one source]``, which derives its own isolated
``preview-*`` project id from that single source -- so every model's result is
fully isolated, and there is no model- or source-specific logic here.

Storage: a dedicated ORM table pair (DryRunJob/DryRunJobFile), not the
existing "compat store" JSON dict real deploy runs use for their own
polling. This was a deliberate choice, not an oversight: the compat store
has a known, already-tracked test-isolation bug (writes leak into the real
Config/projects/ directory during tests), and multi-model dry-run jobs don't
need any of the compat store's other bookkeeping (YAML config persistence,
snapshot capture, etc.) -- only status + result storage, which a small
dedicated table pair provides more safely and more testably. The functional
shape (create -> background dispatch -> poll -> independent per-file rerun)
matches real deploys' pattern exactly; only the storage substrate differs.

Concurrency guarantees:
  * each file's status/result is written to the DB as soon as that file
    finishes (not after the whole pool);
  * a file is only ever picked up from ``pending``, and the pending->running
    transition is a conditional UPDATE, so two overlapping
    run_dry_run_job() calls (e.g. a rerun scheduled while the original job
    is still running) can never run the same file twice;
  * a stale-job guard on status read fails files stuck in running/pending
    longer than ``DRY_RUN_STALE_AFTER`` so the UI stops polling forever.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any, Dict, List, Optional

from semabridge.api.services.pbix_source_validation import (
    MAX_DRY_RUN_MODELS,
    normalize_dry_run_sources,
)
from semabridge.domain.exceptions import NotFoundError, ValidationError
from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile

logger = logging.getLogger(__name__)

# Mirrors sync_execution_service.MAX_BATCH_MODELS / pbix_source_validation.MAX_PBIX_FILES.
MAX_PARALLEL_DRY_RUN_FILES = MAX_DRY_RUN_MODELS

# Files running (or queued and never started) longer than this are treated as
# lost (server restart, killed worker) and failed on the next status read.
DRY_RUN_STALE_AFTER = timedelta(
    minutes=float(os.environ.get("SEMABRIDGE_DRY_RUN_STALE_MINUTES", "30") or 30)
)
STALE_ERROR_MESSAGE = (
    "Dry run timed out: no result was recorded within {minutes} minutes "
    "(the background worker was likely interrupted, e.g. by a server restart). "
    "Retry this model."
)

# Keyed by engine identity, not URL: an engine reset against the same URL
# (e.g. an in-memory SQLite DB) is a brand-new, empty database.
_schema_checked_engines: set = set()
_schema_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    # SQLite drops tzinfo on round-trip; every timestamp here is written as UTC.
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: Optional[datetime]) -> Optional[str]:
    value = _as_aware(value)
    return value.isoformat() if value else None


def _ensure_schema() -> None:
    # ModelRepository()'s constructor runs Base.metadata.create_all() once per
    # DB URL (idempotent) -- cheap to call unconditionally so this module
    # never depends on some other code path having already done it first.
    from semabridge.repository.model_repository import ModelRepository

    ModelRepository()

    # create_all() never adds columns to an existing table: make sure the
    # multi-model columns exist on DBs created before they were introduced.
    try:
        from semabridge.repository.orm.session_factory import get_engine

        engine = get_engine()
        key = id(engine)
        if key in _schema_checked_engines:
            return
        with _schema_lock:
            if key in _schema_checked_engines:
                return
            from semabridge.api.bootstrap.db_migrations import ensure_dry_run_job_columns
            from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile

            DryRunJob.__table__.create(bind=engine, checkfirst=True)
            DryRunJobFile.__table__.create(bind=engine, checkfirst=True)
            ensure_dry_run_job_columns(engine)
            _ensure_plan_hash_columns(engine)
            _schema_checked_engines.add(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dry-run job schema check failed (continuing): %s", exc)


# Dry-run plan fingerprints on dry_run_job_files (see DryRunJobFile). Added
# here with the same hand-written, idempotent ALTER TABLE pattern as
# db_migrations.ensure_dry_run_job_columns (create_all never adds columns).
_PLAN_HASH_COLUMNS = ("content_hash", "config_hash", "overrides_hash")


def _ensure_plan_hash_columns(engine) -> List[str]:
    from sqlalchemy import text

    from semabridge.api.bootstrap.db_migrations import _get_column_names, _table_exists

    table = "dry_run_job_files"
    if not _table_exists(engine, table):
        return []
    existing = _get_column_names(engine, table)
    if not existing:
        return []
    applied = [
        f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR(64)"
        for column in _PLAN_HASH_COLUMNS
        if column not in existing
    ]
    if applied:
        with engine.begin() as conn:
            for ddl in applied:
                conn.execute(text(ddl))
        logger.info("Applied dry-run plan hash columns: %s", ", ".join(applied))
    return applied


def _source_label(source_type: str, source_ref: str) -> str:
    """UI section title: PBIX file stem, or the model name for other sources."""
    if str(source_type or "").lower() == "pbix" or str(source_ref).lower().endswith(".pbix"):
        return PurePath(str(source_ref).replace("\\", "/")).stem or source_ref
    return source_ref


def _resolve_file_list(source_config: Dict[str, Any], selected_sources: List[str]) -> List[str]:
    """The list of per-file source references this job should dry-run, one
    pipeline call per entry (.pbix paths for pbix, model names for fabric).

    Mirrors _build_sync_jobs()'s own precedence (explicit single pbix_path
    wins over a models list) so a job created here resolves to the exact same
    files a real deploy of the same config would.
    """
    explicit_pbix = str(source_config.get("pbix_path") or "").strip()
    source_type = str(source_config.get("type") or "").strip().lower()
    if explicit_pbix and not selected_sources and source_type in ("", "pbix"):
        return [explicit_pbix]
    return [str(p).strip() for p in (selected_sources or []) if str(p or "").strip()]


def _file_payload(f: DryRunJobFile) -> Dict[str, Any]:
    source_type = str(f.source_type or ("pbix" if f.pbix_path else "")).lower()
    source_ref = f.source_ref or f.pbix_path or ""
    label = f.label or f.display_name
    return {
        "file_id": f.file_id,
        "status": f.status,
        "error_message": f.error_message,
        # Back-compat keys (pre multi-model UI).
        "error_summary": f.error_message,
        "filename": f.display_name,
        "pbix_path": (f.pbix_path or None) if source_type == "pbix" else None,
        "source_type": source_type or None,
        "source_ref": source_ref,
        "label": label,
        "preview_project_id": f.preview_project_id,
        "started_at": _iso(f.started_at),
        "completed_at": _iso(f.completed_at),
    }


def create_dry_run_job(
    *,
    project_id: str,
    source_config: Dict[str, Any],
    target_config: Dict[str, Any],
    selected_sources: List[str],
    requested_by_user_id: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validates the request, creates a DryRunJob + one DryRunJobFile per
    source (all status=pending), and returns immediately. Does NOT dispatch
    any background work itself -- the caller (dry_run_jobs_controller.py) is
    expected to schedule run_dry_run_job(job_id) via FastAPI BackgroundTasks
    right after this returns, exactly mirroring run_service.py's
    _create_project_run() + background_tasks.add_task() pattern for real
    deploys.

    ``selected_sources``: full .pbix paths (``source_config.type == "pbix"``)
    or semantic model names (``"fabric"``). Deduplicated case-insensitively;
    max MAX_PARALLEL_DRY_RUN_FILES.
    """
    source_type = str((source_config or {}).get("type") or "").strip().lower()
    selected = normalize_dry_run_sources(list(selected_sources or []), source_type=source_type)

    files = _resolve_file_list(source_config or {}, selected)
    if not files:
        if source_type == "pbix" or not source_type:
            raise ValidationError("No .pbix files specified for this dry-run job.")
        raise ValidationError(f"No models specified for this {source_type} dry-run job.")
    if len(files) > MAX_PARALLEL_DRY_RUN_FILES:
        raise ValidationError(
            f"Up to {MAX_PARALLEL_DRY_RUN_FILES} models are supported per dry-run job; "
            f"{len(files)} were provided."
        )
    _ensure_schema()

    from semabridge.repository.orm.session_factory import db_manager

    job_id = f"dryrun-{uuid.uuid4().hex[:20]}"
    now = _utcnow()
    file_type = source_type or "pbix"

    with db_manager.get_session() as session:
        job = DryRunJob(
            job_id=job_id,
            project_id=project_id,
            status="pending",
            created_at=now,
            source_config_json=json.dumps(source_config or {}),
            target_config_json=json.dumps(target_config or {}),
            requested_by_user_id=str(requested_by_user_id) if requested_by_user_id is not None else None,
            source_type=file_type,
            options_json=json.dumps(options or {}),
        )
        session.add(job)
        file_rows = []
        for index, source_ref in enumerate(files):
            label = _source_label(file_type, source_ref)
            job_file = DryRunJobFile(
                file_id=f"{job_id}-f{index}",
                job_id=job_id,
                file_index=index,
                pbix_path=source_ref if file_type == "pbix" else "",
                display_name=label[:255],
                source_ref=source_ref,
                source_type=file_type,
                label=label[:255],
                status="pending",
                queued_at=now,
            )
            session.add(job_file)
            file_rows.append(job_file)
        session.flush()
        file_summaries = [_file_payload(f) for f in file_rows]

    return {
        "job_id": job_id,
        "project_id": project_id,
        "status": "pending",
        "source_type": file_type,
        "files": file_summaries,
    }


def _call_pipeline(pipeline, **kwargs) -> Dict[str, Any]:
    """Invoke the pipeline coroutine, forwarding only the kwargs it accepts
    (lets test doubles with a narrower signature keep working)."""
    try:
        params = inspect.signature(pipeline).parameters
        accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        if not accepts_var_kw:
            kwargs = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        pass
    return asyncio.run(pipeline(**kwargs))


def _run_one_file_sync(
    *,
    project_id: str,
    requested_by_user_id: Optional[str],
    source_config: Dict[str, Any],
    target_config: Dict[str, Any],
    pbix_path: str,
    all_sources: Optional[List[str]] = None,
    ontology_domain_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Runs _run_dry_run_pipeline() for exactly one source (``pbix_path`` is
    the generic per-file source reference -- a .pbix path or a model name;
    the parameter name is kept for back-compat), on its own thread with its
    own event loop: the pipeline's `await sync_models(...)` call is actually
    synchronous/blocking under the hood, so N files must run on N real OS
    threads to be genuinely concurrent.
    """
    from semabridge.api.controllers import mappings_controller

    # Strip a stray singular pbix_path so _build_sync_jobs()'s "explicit
    # pbix_path wins over models" precedence can't silently make every file
    # in the batch resolve to the same (wrong) file -- this call is scoped to
    # exactly one source via selected_sources=[source_ref] below.
    scoped_source_config = dict(source_config or {})
    scoped_source_config.pop("pbix_path", None)
    scoped_source_config.pop("models", None)

    return _call_pipeline(
        mappings_controller._run_dry_run_pipeline,
        project_id=project_id,
        request_user_id=requested_by_user_id,
        source_config=scoped_source_config,
        target_config=target_config,
        selected_sources=[pbix_path],
        # Every model of this job: a new (wizard) multi-model project's dry
        # run names each model exactly as project creation will.
        all_sources=list(all_sources or []),
        ontology_domain_ids=list(ontology_domain_ids) if ontology_domain_ids else None,
    )


def _aggregate_job_status(file_statuses: List[str]) -> str:
    if not file_statuses:
        return "failed"
    if any(s in ("pending", "running") for s in file_statuses):
        return "running"
    # "skipped" = a model with no tables: fine next to deployable models, but
    # a job with nothing deployable did not succeed.
    if all(s in ("success", "skipped") for s in file_statuses):
        return "success" if "success" in file_statuses else "failed"
    if all(s in ("failed", "skipped") for s in file_statuses):
        return "failed"
    return "partial"


def _expected_preview_project_id(requested_by_user_id, source_config, source_ref) -> Optional[str]:
    try:
        from semabridge.api.controllers.mappings_controller import _dry_run_preview_project_id

        scoped = dict(source_config or {})
        scoped.pop("pbix_path", None)
        return _dry_run_preview_project_id(requested_by_user_id, scoped, [source_ref])
    except Exception:  # noqa: BLE001
        return None


def _claim_pending_files(job_id: str) -> List[Dict[str, Any]]:
    """Atomically move this job's ``pending`` files to ``running``.

    The transition is a conditional UPDATE (``WHERE status = 'pending'``) per
    file: if another run_dry_run_job() invocation claimed the file first, the
    rowcount is 0 and this invocation skips it -- a file can never be run
    twice concurrently.
    """
    from sqlalchemy import update

    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        candidates = session.query(DryRunJobFile).filter(
            DryRunJobFile.job_id == job_id,
            DryRunJobFile.status == "pending",
        ).order_by(DryRunJobFile.file_index).all()
        candidate_info = [
            (f.file_id, f.source_ref or f.pbix_path) for f in candidates
        ]

    claimed: List[Dict[str, Any]] = []
    for file_id, source_ref in candidate_info:
        now = _utcnow()
        with db_manager.get_session() as session:
            result = session.execute(
                update(DryRunJobFile)
                .where(DryRunJobFile.file_id == file_id, DryRunJobFile.status == "pending")
                .values(status="running", started_at=now, completed_at=None, error_message=None)
            )
            if (result.rowcount or 0) == 1:
                claimed.append({"file_id": file_id, "source_ref": source_ref, "started_at": now})
    return claimed


def _record_file_outcome(
    job_id: str,
    file_id: str,
    *,
    started_at: datetime,
    result: Optional[Dict[str, Any]],
    error: Optional[str],
    expected_preview_id: Optional[str],
) -> None:
    """Persist ONE file's outcome right away (called from the as_completed
    loop) and refresh the parent job's aggregate status."""
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job_file = session.get(DryRunJobFile, file_id)
        if job_file is None:
            return
        # Only the invocation that claimed this attempt may write it: if the
        # file was re-queued (pending) or re-claimed by a newer attempt since,
        # drop this stale result instead of clobbering the newer one.
        current_started = _as_aware(job_file.started_at)
        if job_file.status == "pending" or (
            current_started is not None and abs((current_started - started_at).total_seconds()) > 1
        ):
            logger.info("Dry-run job %s file %s: discarding superseded result", job_id, file_id)
            return
        job_file.completed_at = _utcnow()
        job_file.content_hash = None
        job_file.config_hash = None
        job_file.overrides_hash = None
        if error is not None:
            job_file.status = "failed"
            job_file.error_message = error
            job_file.result_json = None
        else:
            result = result or {}
            # extraction_failed=True is a "soft" failure the pipeline returns
            # successfully rather than raising (see _run_dry_run_pipeline) --
            # must still surface as a failed file, not a silent success with
            # zero fields, per "show real error messages, never hide failures."
            if result.get("success") and not result.get("extraction_failed"):
                job_file.status = "success"
                job_file.error_message = None
                # What this dry run validated -- the sync gate requires the
                # same file content and deploy plan.
                job_file.content_hash = _hash_or_none(result.get("content_hash"))
                job_file.config_hash = _hash_or_none(result.get("config_hash"))
                job_file.overrides_hash = _hash_or_none(result.get("overrides_hash"))
            elif result.get("skipped_no_tables"):
                # Nothing to deploy (no tables, e.g. a thin report). Hashes
                # are kept so the gate still notices if the file changes.
                job_file.status = "skipped"
                job_file.error_message = str(result.get("skip_reason") or "This model has no tables.")
                job_file.content_hash = _hash_or_none(result.get("content_hash"))
                job_file.config_hash = _hash_or_none(result.get("config_hash"))
            else:
                job_file.status = "failed"
                job_file.error_message = str(
                    result.get("error")
                    or ("Extraction failed — no semantic model could be built from this file." if result.get("extraction_failed") else None)
                    or "Dry run did not succeed."
                )
            preview_id = str(result.get("project_id") or "").strip()
            if preview_id.startswith("preview-"):
                job_file.preview_project_id = preview_id
            job_file.result_json = json.dumps(_decorate_result(result, job_file), default=str)
        if not job_file.preview_project_id and expected_preview_id:
            job_file.preview_project_id = expected_preview_id

    _refresh_job_status(job_id)


def _hash_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if len(text) == 64 else None


def _decorate_result(result: Dict[str, Any], job_file: DryRunJobFile) -> Dict[str, Any]:
    """Guarantee per-model identity on every stored result: ``label``,
    ``source_file``, ``source_ref``, ``model_name`` at the top level and
    ``source_file`` on every row, so the UI can title each model section."""
    out = dict(result)
    label = job_file.label or job_file.display_name
    source_ref = job_file.source_ref or job_file.pbix_path
    out.setdefault("label", label)
    if not out.get("label"):
        out["label"] = label
    if not out.get("source_file"):
        out["source_file"] = label
    out["source_ref"] = source_ref
    out.setdefault("source_type", job_file.source_type)
    if not out.get("model_name"):
        out["model_name"] = label
    rows = out.get("entity_mappings")
    if isinstance(rows, list):
        decorated = []
        for row in rows:
            if isinstance(row, dict) and not row.get("source_file"):
                row = {**row, "source_file": out["source_file"]}
            decorated.append(row)
        out["entity_mappings"] = decorated
    return out


def _refresh_job_status(job_id: str) -> None:
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job = session.get(DryRunJob, job_id)
        if job is None:
            return
        statuses = [f.status for f in job.files]
        job.status = _aggregate_job_status(statuses)
        if job.status in ("running", "pending"):
            job.completed_at = None
        elif job.completed_at is None:
            job.completed_at = _utcnow()


def run_dry_run_job(job_id: str) -> None:
    """Background task body: claims every ``pending`` file in this job and
    runs them in parallel (ThreadPoolExecutor, mirroring
    sync_execution_service.py's own proven _run_parallel_jobs() pattern).
    Each file's result is written to its own DryRunJobFile row the moment it
    completes -- independently of its siblings, so a partial failure never
    hides or delays the files that succeeded, and pollers see progress live.
    """
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job = session.get(DryRunJob, job_id)
        if job is None:
            logger.error("run_dry_run_job: job %s not found", job_id)
            return
        source_config = json.loads(job.source_config_json or "{}")
        target_config = json.loads(job.target_config_json or "{}")
        try:
            options = json.loads(job.options_json or "{}") or {}
        except (TypeError, ValueError):
            options = {}
        project_id = job.project_id
        requested_by_user_id = job.requested_by_user_id
        all_sources = [f.source_ref or f.pbix_path for f in job.files if (f.source_ref or f.pbix_path)]

    claimed = _claim_pending_files(job_id)
    if not claimed:
        _refresh_job_status(job_id)
        return
    _refresh_job_status(job_id)

    max_workers = min(len(claimed), MAX_PARALLEL_DRY_RUN_FILES)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dry-run-job") as executor:
        future_to_claim = {
            executor.submit(
                _run_one_file_sync,
                project_id=project_id,
                requested_by_user_id=requested_by_user_id,
                source_config=source_config,
                target_config=target_config,
                pbix_path=claim["source_ref"],
                all_sources=all_sources,
                ontology_domain_ids=options.get("ontology_domain_ids") if isinstance(options, dict) else None,
            ): claim
            for claim in claimed
        }
        for future in as_completed(future_to_claim):
            claim = future_to_claim[future]
            file_id = claim["file_id"]
            result: Optional[Dict[str, Any]] = None
            error: Optional[str] = None
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Dry-run job %s file %s crashed: %s", job_id, file_id, exc)
                error = str(exc) or exc.__class__.__name__
            try:
                _record_file_outcome(
                    job_id,
                    file_id,
                    started_at=claim["started_at"],
                    result=result,
                    error=error,
                    expected_preview_id=_expected_preview_project_id(
                        requested_by_user_id, source_config, claim["source_ref"]
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Dry-run job %s: failed to persist file %s outcome: %s", job_id, file_id, exc)

    _refresh_job_status(job_id)


def _fail_stale_files(session, job: DryRunJob) -> bool:
    """Stale-job guard: fail files stuck ``running`` (by started_at) or
    ``pending`` (by queued_at) longer than DRY_RUN_STALE_AFTER."""
    now = _utcnow()
    changed = False
    message = STALE_ERROR_MESSAGE.format(minutes=int(DRY_RUN_STALE_AFTER.total_seconds() // 60))
    for f in job.files:
        if f.status == "running":
            ref = _as_aware(f.started_at)
        elif f.status == "pending":
            ref = _as_aware(f.queued_at)
        else:
            continue
        if ref is None or now - ref <= DRY_RUN_STALE_AFTER:
            continue
        f.status = "failed"
        f.error_message = message
        f.completed_at = now
        changed = True
    if changed:
        job.status = _aggregate_job_status([f.status for f in job.files])
        if job.status not in ("running", "pending") and job.completed_at is None:
            job.completed_at = now
    return changed


def get_dry_run_job_meta(job_id: str) -> Dict[str, Any]:
    """Ownership metadata for access checks (project_id, requested_by_user_id)."""
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job = session.get(DryRunJob, job_id)
        if job is None:
            raise NotFoundError(f"Dry-run job '{job_id}' not found.")
        return {
            "job_id": job.job_id,
            "project_id": job.project_id,
            "requested_by_user_id": job.requested_by_user_id,
            "source_type": job.source_type,
        }


def link_dry_run_job_to_project(
    target_project_id: str,
    dry_run_job_id: Optional[str] = None,
    preview_project_ids: Optional[List[str]] = None,
    user_id: Optional[str] = None,
) -> int:
    """Re-associates dry run job(s) from a transient preview project id to the
    real target project id after project save.
    """
    _ensure_schema()
    target = str(target_project_id or "").strip()
    if not target or target == "preview" or target.startswith("preview-"):
        return 0

    from semabridge.api.services.project_ownership_service import auth_is_enabled
    from semabridge.repository.orm.dry_run_job_models import DryRunJob
    from semabridge.repository.orm.session_factory import db_manager

    updated_count = 0
    with db_manager.get_session() as session:
        if dry_run_job_id:
            job = session.get(DryRunJob, str(dry_run_job_id).strip())
            if job is not None:
                if not auth_is_enabled() or not user_id or str(job.requested_by_user_id or "") == str(user_id):
                    job.project_id = target
                    updated_count += 1

        if preview_project_ids:
            clean_previews = [str(p).strip() for p in preview_project_ids if str(p or "").strip()]
            if clean_previews:
                jobs = session.query(DryRunJob).filter(DryRunJob.project_id.in_(clean_previews)).all()
                for job in jobs:
                    if not auth_is_enabled() or not user_id or str(job.requested_by_user_id or "") == str(user_id):
                        job.project_id = target
                        updated_count += 1

    return updated_count


def get_dry_run_job_preview_project_ids(job_id: str) -> List[Dict[str, Any]]:
    """Every per-file preview project id of a job (for synonym migration)."""
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job = session.get(DryRunJob, job_id)
        if job is None:
            raise NotFoundError(f"Dry-run job '{job_id}' not found.")
        try:
            source_config = json.loads(job.source_config_json or "{}")
        except (TypeError, ValueError):
            source_config = {}
        out = []
        for f in job.files:
            preview_id = f.preview_project_id or _expected_preview_project_id(
                job.requested_by_user_id, source_config, f.source_ref or f.pbix_path
            )
            out.append({
                "file_id": f.file_id,
                "label": f.label or f.display_name,
                "source_ref": f.source_ref or f.pbix_path,
                "preview_project_id": preview_id,
            })
        return out


def get_dry_run_job_status(job_id: str) -> Dict[str, Any]:
    """Lightweight per-file status list -- what the per-file list view
    polls every few seconds. Does NOT include each file's full
    entity_mappings payload (see get_dry_run_job_file_result for that).
    Applies the stale-job guard before answering.
    """
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job = session.get(DryRunJob, job_id)
        if job is None:
            raise NotFoundError(f"Dry-run job '{job_id}' not found.")
        _fail_stale_files(session, job)
        return {
            "job_id": job.job_id,
            "project_id": job.project_id,
            "status": job.status,
            "source_type": job.source_type or "pbix",
            "created_at": _iso(job.created_at),
            "completed_at": _iso(job.completed_at),
            "files": [_file_payload(f) for f in job.files],
        }


def get_dry_run_job_file_result(job_id: str, file_id: str) -> Dict[str, Any]:
    """Full dry-run payload for ONE file -- same RAW shape _run_dry_run_pipeline()/
    the single-file /dry-run endpoint has always returned (source_name/
    target_name/source_expression/entity_mappings/...), plus per-model
    identity (``label``/``source_file``/``model_name``/``source_ref``). This
    is what the drill-in view fetches.

    NOTE: this is NOT the NormalizedRow[] shape DryRunMappingTable's `mappings`
    prop requires -- any frontend caller of this endpoint MUST call
    normalizeRows(result) before rendering it (exactly like the single-file
    /dry-run caller in CreateProjectPage.jsx).
    """
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job_file = session.get(DryRunJobFile, file_id)
        if job_file is None or job_file.job_id != job_id:
            raise NotFoundError(f"Dry-run job file '{file_id}' not found on job '{job_id}'.")
        base = {
            "file_id": file_id,
            "filename": job_file.display_name,
            "label": job_file.label or job_file.display_name,
            "source_ref": job_file.source_ref or job_file.pbix_path,
            "source_type": job_file.source_type,
            "status": job_file.status,
        }
        if job_file.status in ("pending", "running"):
            return {**base, "result": None}
        result = json.loads(job_file.result_json) if job_file.result_json else None
        return {**base, "error": job_file.error_message, "result": result}


def reset_dry_run_job_file_for_rerun(job_id: str, file_id: str) -> Dict[str, Any]:
    """Resets exactly one file's row to pending, leaving every sibling row
    (and the parent job's other files) completely untouched. The caller
    (dry_run_jobs_controller.py) is expected to schedule
    run_dry_run_job(job_id) again via BackgroundTasks right after this
    returns; run_dry_run_job() only claims ``pending`` files, so siblings
    that are running/succeeded/failed are never re-run.

    Raises ValidationError if the file is currently running (a rerun can't
    start a second concurrent run of the same file).
    """
    _ensure_schema()
    from sqlalchemy import update

    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job_file = session.get(DryRunJobFile, file_id)
        if job_file is None or job_file.job_id != job_id:
            raise NotFoundError(f"Dry-run job file '{file_id}' not found on job '{job_id}'.")
        label = job_file.label or job_file.display_name
        filename = job_file.display_name
        if job_file.status == "pending":
            # Already queued -- idempotent.
            return {"job_id": job_id, "file_id": file_id, "filename": filename, "label": label, "status": "pending"}

    now = _utcnow()
    with db_manager.get_session() as session:
        result = session.execute(
            update(DryRunJobFile)
            .where(
                DryRunJobFile.file_id == file_id,
                DryRunJobFile.job_id == job_id,
                DryRunJobFile.status.in_(("success", "failed", "skipped")),
            )
            .values(
                status="pending",
                error_message=None,
                result_json=None,
                started_at=None,
                completed_at=None,
                queued_at=now,
                content_hash=None,
                config_hash=None,
                overrides_hash=None,
            )
        )
        if (result.rowcount or 0) != 1:
            raise ValidationError(
                f"Dry-run for '{label}' is already running; wait for it to finish before retrying."
            )
        job = session.get(DryRunJob, job_id)
        if job is not None:
            job.status = "running"
            job.completed_at = None

    return {"job_id": job_id, "file_id": file_id, "filename": filename, "label": label, "status": "pending"}


def _load_project_sync_config(project_id: str, source_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The config the project's SYNC runs with (saved config + mapping-store
    edits merged into mappings_overrides, as run_service._prepare_sync_config
    builds it), with ``source_cfg`` as its source. Read-only."""
    config: Dict[str, Any] = {}
    try:
        import yaml

        import semabridge.api.services.project_shared as project_shared
        from semabridge.api.services.mapping_service import _compat_apply_manual_mapping_overrides_to_cfg

        project_shared._compat_ensure_loaded()
        bundle = project_shared._compat_load_modular_project(project_id) or {}
        base = str(bundle.get("config_yaml") or "") or str(project_shared._compat_project_configs.get(project_id) or "")
        if base:
            merged = _compat_apply_manual_mapping_overrides_to_cfg(base, project_id) or base
            parsed = yaml.safe_load(merged) if isinstance(merged, str) else merged
            if isinstance(parsed, dict):
                config = parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dry-run gate: config of project %s unavailable (%s); using its source only.", project_id, exc)
    return {**config, "source": dict(source_cfg or {})}


def _gate_jobs(config: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str]:
    """The sync jobs (with their deploy naming) the sync of ``config`` runs:
    sync_execution_service._build_sync_jobs itself. When the files cannot
    all be resolved (the sync itself would then fail per model), fall back
    to the same naming over the configured entries."""
    from semabridge.api.services.sync_execution_service import (
        _assign_project_mode_naming,
        _build_sync_jobs,
        _normalize_connector_type,
        resolve_target_type,
        _resolve_target_config,
    )
    from semabridge.utils.identifiers import clean_pbix_model_name
    from semabridge.utils.name_translator import make_unique_labels
    from semabridge.utils.project_models import model_entry_key

    try:
        jobs, _source_type, target_type, _s, _t = _build_sync_jobs(config)
        return jobs, target_type
    except Exception as exc:  # noqa: BLE001
        logger.info("Dry-run gate: sync jobs not resolvable (%s); naming configured entries directly.", exc)
    source_cfg = config.get("source") or {}
    source_type = _normalize_connector_type(source_cfg.get("type", "pbix"), "pbix")
    target_type = resolve_target_type(_resolve_target_config(config) or {}, "snowflake")
    entries = [model_entry_key(m) for m in (source_cfg.get("models") or [])]
    entries = [e for e in entries if e] or ([str(source_cfg["pbix_path"])] if source_cfg.get("pbix_path") else [])
    jobs = []
    for entry in entries:
        is_file = source_type == "pbix" or entry.lower().endswith(".pbix")
        jobs.append({
            "dataset_id": None if is_file else entry,
            "pbix_path": entry if is_file else None,
            "model_label": (clean_pbix_model_name(entry) if is_file else entry) or "model",
            "source_ref": entry,
        })
    for job, label in zip(jobs, make_unique_labels([str(j["model_label"]) for j in jobs])):
        job["model_label"] = label
    _assign_project_mode_naming(jobs, source_cfg)
    return jobs, target_type


def _name_keys(*values: Any) -> set:
    """Name-based identity keys of a model reference (legacy matching)."""
    from semabridge.utils.identifiers import clean_pbix_model_name

    keys = set()
    for value in values:
        if not value:
            continue
        text = str(value).strip().lower()
        norm = text.replace("\\", "/")
        keys.update({text, norm, PurePath(norm).stem.lower()})
        cleaned = (clean_pbix_model_name(norm) or "").strip().lower()
        if cleaned:
            keys.add(cleaned)
    keys.discard("")
    return keys


def _row_scope(row: Dict[str, Any]) -> str:
    scope = str(row.get("mapping_scope") or "").strip()
    if scope:
        return scope
    kind = str(row.get("entity_kind") or "").lower()
    if kind in ("measure", "metric"):
        return "metric"
    return f"column::{str(row.get('parent_source_path') or row.get('source_table') or '').strip().lower()}"


def _plan_blockers(result: Dict[str, Any], overrides: List[Dict[str, Any]], target_type: str) -> List[str]:
    """Blocking issues of one stored dry-run result, re-validated against the
    overrides the sync will apply now: unresolved mapping blockers (minus
    rows the user has since renamed), names that collide AFTER applying the
    current overrides, and ontology blocking conflicts."""
    from semabridge.api.services.mapping_service import _compat_collect_dry_run_blockers

    issues: List[str] = []
    from semabridge.target_rules.naming import override_deploy_name

    override_names: Dict[str, str] = {}
    for row in overrides or []:
        if isinstance(row, dict) and str(row.get("source_path") or "").strip() and str(row.get("target_name") or "").strip():
            source_path = str(row["source_path"]).strip()
            # Same rule the sync deploys with (columns: semantic alias,
            # metrics: to_alias), so 'Value' clashes with VALUE here too; a
            # saved COL_DATE for 'Date' is only the old automatic name
            # (Snowflake): the sync ignores it, so the gate must too.
            name = override_deploy_name(row["target_name"], source_path, target_type)
            if name is None:
                continue
            override_names[source_path.lower()] = name

    rows = [r for r in (result.get("entity_mappings") or []) if isinstance(r, dict)]
    blockers = _compat_collect_dry_run_blockers(result)
    open_blockers = [
        b for b in blockers
        if (b.get("source_path") or "").strip().lower() not in override_names
        and (b.get("id") or "").strip().lower() not in override_names
    ]
    if open_blockers:
        issues.append(f"{len(open_blockers)} unresolved dry-run blocker(s)")

    # The renamed names must still be unique in their namespace.
    seen: Dict[tuple, str] = {}
    duplicates: List[str] = []
    for row in rows:
        if row.get("sync_enabled") is False:
            continue
        path = str(row.get("source_path") or "").strip().lower()
        name = (override_names.get(path) or str(row.get("target_name") or "")).strip().upper()
        if not name:
            continue
        key = (_row_scope(row), name)
        if key in seen and seen[key] != path:
            duplicates.append(name)
        else:
            seen[key] = path
    if duplicates:
        shown = ", ".join(sorted(set(duplicates))[:5])
        issues.append(f"{len(set(duplicates))} duplicate target name(s) after your edits ({shown})")

    conflicts = result.get("blocking_conflicts")
    if conflicts is None:
        conflicts = (result.get("ontology_changes") or {}).get("blocking_conflicts")
    if conflicts:
        issues.append(f"{len(conflicts)} ontology blocking conflict(s)")

    tbp = result.get("table_binding_plan")
    if isinstance(tbp, dict):
        tbp_blocked = [b for b in (tbp.get("blocked") or []) if b]
        if tbp_blocked:
            issues.append(f"{len(tbp_blocked)} blocked target table binding(s)")

    dbx_plan = result.get("databricks_plan")
    if isinstance(dbx_plan, dict):
        if dbx_plan.get("error"):
            issues.append(f"Databricks plan error ({dbx_plan['error']})")
        dbx_blocked = [b for b in (dbx_plan.get("blocked") or []) if b]
        if dbx_blocked:
            issues.append(f"{len(dbx_blocked)} Databricks blocked item(s)")
        dbx_missing = [t for t in (dbx_plan.get("missing_tables") or []) if t]
        if dbx_missing:
            issues.append(f"{len(dbx_missing)} Databricks missing source table(s)")
        dbx_skipped = [v for v in (dbx_plan.get("views") or []) if isinstance(v, dict) and v.get("skipped")]
        if dbx_skipped:
            issues.append(f"{len(dbx_skipped)} Databricks skipped view(s)")
    return issues


def _load_gate_files(project_id: str) -> List[Dict[str, Any]]:
    """This project's dry-run files, newest first (the job's creation, then
    the file's own completion) -- what the sync gate chooses from."""
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        rows = (
            session.query(DryRunJobFile, DryRunJob)
            .join(DryRunJob, DryRunJobFile.job_id == DryRunJob.job_id)
            .filter(DryRunJob.project_id == project_id)
            .order_by(DryRunJob.created_at.desc(), DryRunJobFile.file_index)
            .limit(500)
            .all()
        )
        files = []
        for f, job in rows:
            files.append({
                "status": f.status,
                "error": f.error_message,
                "label": f.label or f.display_name,
                "result_json": f.result_json,
                "content_hash": f.content_hash,
                "config_hash": f.config_hash,
                "preview_project_id": f.preview_project_id,
                "keys": _name_keys(f.source_ref, f.pbix_path, f.label, f.display_name),
                "order": _as_aware(f.completed_at or f.started_at or f.queued_at or job.created_at),
                "job_created": _as_aware(job.created_at),
            })
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    files.sort(key=lambda f: (f["job_created"] or epoch, f["order"] or epoch), reverse=True)
    return files


def _pick_gate_candidate(job: Dict[str, Any], files: List[Dict[str, Any]]) -> tuple:
    """(dry-run file, problem) the gate uses for one sync job.

    The newest finished dry-run file whose content_hash equals the sha256 of
    the file the sync will read; for sources without file content (Fabric)
    the newest finished dry run of the model by name; else a legacy
    (hash-less) dry run by name. ``problem`` is None, "legacy",
    "in_progress" (newest by name still running), "changed" (dry runs exist
    but of other content) or "missing"."""
    from semabridge.api.services.sync_execution_service import file_content_sha256

    job_keys = _name_keys(job.get("source_ref"), job.get("pbix_path"), job.get("model_label"))
    by_name = [f for f in files if f["keys"] & job_keys]
    if by_name and by_name[0]["status"] in ("running", "pending"):
        return None, "in_progress"
    content_hash = file_content_sha256(job.get("pbix_path") or job.get("source_ref"))
    finished = lambda f: f["status"] not in ("running", "pending")  # noqa: E731
    if content_hash:
        hashed = [f for f in files if f["content_hash"] == content_hash and finished(f)]
        if hashed:
            return hashed[0], None
    else:
        named = next((f for f in by_name if finished(f)), None)
        if named is not None:
            return named, None
    if by_name and any(f["content_hash"] for f in by_name):
        return None, "changed"
    legacy = next((f for f in by_name if not f["content_hash"] and finished(f)), None)
    if legacy is None:
        return None, "missing"
    return legacy, "legacy"


def accepted_dry_run_previews(project_id: str, config: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """model_label -> preview project id of the dry run the sync gate accepts
    for that model (same file content as the sync will read, successful),
    or None when the model has no usable dry run. Read-only; never raises
    for a single model (a model that cannot be resolved maps to None)."""
    _ensure_schema()
    effective = dict(config or {})
    jobs, _target_type = _gate_jobs(effective)
    if not jobs:
        return {}
    files = _load_gate_files(project_id)
    out: Dict[str, Optional[str]] = {}
    for job in jobs:
        label = str(job.get("model_label") or job.get("source_ref") or "")
        try:
            candidate, problem = _pick_gate_candidate(job, files)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dry-run lookup for %s/%s failed: %s", project_id, label, exc)
            candidate, problem = None, "error"
        usable = (
            candidate is not None
            and problem in (None, "legacy")
            and candidate["status"] == "success"
            and candidate.get("preview_project_id")
        )
        out[label] = str(candidate["preview_project_id"]) if usable else None
    return out


def validate_multi_model_dry_run_readiness(
    project_id: str,
    source_cfg: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Server-side gate: refuses a multi-mode project sync unless EVERY model
    the sync will deploy has a successful dry run of the SAME content and the
    SAME deploy plan, with no blocker left open.

    Per configured model (the sync's own jobs, see _gate_jobs):
      * the newest dry-run file of this project whose content_hash equals the
        sha256 of the file the sync will read (any upload copy of the same
        bytes matches) must have succeeded, and its config_hash (view name,
        physical prefix, target -- model_config_fingerprint) must equal the
        sync's; otherwise "model X changed since its dry run";
      * its stored result must have no unresolved blockers once the current
        mapping overrides are applied, no duplicate names after them, and no
        ontology blocking conflicts.
    Legacy dry runs (stored before hashes existed) are matched by name and
    still allowed, with a logged warning.

    Only dry-run jobs of THIS project count (wizard jobs are linked to the
    project at save time by link_dry_run_job_to_project, owner-checked).
    Read-only: nothing is modified here.
    """
    _ensure_schema()
    from semabridge.api.services.sync_execution_service import (
        _config_with_model_scoped_overrides,
        model_config_fingerprint,
    )

    effective = dict(config) if isinstance(config, dict) else _load_project_sync_config(project_id, source_cfg)
    effective["source"] = dict(source_cfg or effective.get("source") or {})
    if not (effective["source"].get("models") or effective["source"].get("pbix_path")):
        return
    jobs, target_type = _gate_jobs(effective)
    if not jobs:
        return

    files = _load_gate_files(project_id)
    if not files:
        raise ValidationError(
            f"Multi-model project '{project_id}' cannot be synced: "
            "no dry-run job found for this project. Every model must have a completed, "
            "successful dry run with no unresolved blockers before deployment."
        )

    skipped_models: List[str] = []
    deployable_models = 0
    for job in jobs:
        label = str(job.get("source_ref") or job.get("model_label"))
        display = str(job.get("model_label") or label)
        config_hash = model_config_fingerprint(effective, job, target_type)
        candidate, problem = _pick_gate_candidate(job, files)
        if problem == "in_progress":
            raise ValidationError(
                f"Multi-model project '{project_id}' cannot be synced: "
                f"a dry run of model '{display}' is currently in progress. Wait for it to complete."
            )
        if problem == "changed":
            raise ValidationError(
                f"Multi-model project '{project_id}' cannot be synced: "
                f"model '{display}' changed since its dry run — re-run the dry run."
            )
        if problem == "missing" or candidate is None:
            raise ValidationError(
                f"Multi-model project '{project_id}' cannot be synced: "
                f"model '{label}' has not been dry-run in the latest dry-run job."
            )
        if problem == "legacy":
            logger.warning(
                "Dry-run gate: project %s model '%s' only has a legacy dry run (no content hash); "
                "allowing it without checking the file content or deploy plan. Re-run the dry run "
                "to enable change detection.",
                project_id, display,
            )

        if candidate["status"] == "skipped":
            # No tables (e.g. a thin report): the sync skips this model too.
            # The same-content check above already ran (a changed file needs
            # a new dry run); there is no plan to compare.
            skipped_models.append(display)
            continue
        deployable_models += 1
        if candidate["status"] != "success":
            err = candidate["error"] or f"status is '{candidate['status']}'"
            raise ValidationError(
                f"Multi-model project '{project_id}' cannot be synced: "
                f"dry run for model '{candidate['label'] or display}' failed ({err}). "
                "Retry the dry run for this model."
            )
        if candidate["config_hash"] and candidate["config_hash"] != config_hash:
            raise ValidationError(
                f"Multi-model project '{project_id}' cannot be synced: "
                f"model '{display}' changed since its dry run (its view name, table prefix or target "
                "differs from what the dry run showed) — re-run the dry run."
            )
        if candidate["result_json"]:
            try:
                result_data = json.loads(candidate["result_json"]) or {}
            except Exception:  # noqa: BLE001
                result_data = {}
            overrides = _config_with_model_scoped_overrides(effective, job).get("mappings_overrides") or []
            issues = _plan_blockers(result_data, overrides, target_type)
            if issues:
                raise ValidationError(
                    f"Multi-model project '{project_id}' cannot be synced: "
                    f"model '{candidate['label'] or display}' has {'; '.join(issues)}. "
                    "Resolve all collisions and blockers before syncing."
                )

    if skipped_models and deployable_models == 0:
        raise ValidationError(
            f"Multi-model project '{project_id}' cannot be synced: no model has tables to deploy "
            f"({', '.join(skipped_models)} {'was' if len(skipped_models) == 1 else 'were'} skipped)."
        )
    if skipped_models:
        logger.info(
            "Dry-run gate: project %s will skip %d model(s) with no tables: %s",
            project_id, len(skipped_models), ", ".join(skipped_models),
        )
