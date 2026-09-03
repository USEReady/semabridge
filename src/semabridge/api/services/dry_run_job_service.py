"""Multi-PBIX background dry-run jobs: background-job-plus-polling wrapper
around the existing, unchanged _run_dry_run_pipeline() pipeline call.

Structurally this is the smallest possible change that eliminates the HTTP
timeout risk for N-file dry-run: the pipeline call itself
(mappings_controller._run_dry_run_pipeline) is reused completely unmodified,
called once per file. What's new here is purely the job-creation /
background-dispatch / per-file-persistence / polling shell around it.

Storage: a dedicated ORM table pair (DryRunJob/DryRunJobFile), not the
existing "compat store" JSON dict real deploy runs use for their own
polling. This was a deliberate choice, not an oversight: the compat store
has a known, already-tracked test-isolation bug (writes leak into the real
Config/projects/ directory during tests), and multi-PBIX dry-run jobs don't
need any of the compat store's other bookkeeping (YAML config persistence,
snapshot capture, etc.) — only status + result storage, which a small
dedicated table pair provides more safely and more testably. The functional
shape (create -> background dispatch -> poll -> independent per-file rerun)
matches real deploys' pattern exactly; only the storage substrate differs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.api.services.pbix_source_validation import validate_pbix_model_list
from semabridge.domain.exceptions import NotFoundError, ValidationError
from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile

logger = logging.getLogger(__name__)

# Mirrors sync_execution_service.MAX_BATCH_MODELS / pbix_source_validation.MAX_PBIX_FILES.
MAX_PARALLEL_DRY_RUN_FILES = 10


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_schema() -> None:
    # ModelRepository()'s constructor runs Base.metadata.create_all() once per
    # DB URL (idempotent) -- cheap to call unconditionally so this module
    # never depends on some other code path having already done it first.
    from semabridge.repository.model_repository import ModelRepository

    ModelRepository()


def _resolve_file_list(source_config: Dict[str, Any], selected_sources: List[str]) -> List[str]:
    """The list of full .pbix paths this job should dry-run, one per file.

    Mirrors _build_sync_jobs()'s own precedence (explicit single pbix_path
    wins over a models list) so a job created here resolves to the exact same
    files a real deploy of the same config would.
    """
    explicit_pbix = str(source_config.get("pbix_path") or "").strip()
    if explicit_pbix and not selected_sources:
        return [explicit_pbix]
    return [str(p).strip() for p in (selected_sources or []) if str(p or "").strip()]


def create_dry_run_job(
    *,
    project_id: str,
    source_config: Dict[str, Any],
    target_config: Dict[str, Any],
    selected_sources: List[str],
    requested_by_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validates the request, creates a DryRunJob + one DryRunJobFile per file
    (all status=pending), and returns immediately. Does NOT dispatch any
    background work itself — the caller (dry_run_jobs_controller.py) is
    expected to schedule run_dry_run_job(job_id) via FastAPI BackgroundTasks
    right after this returns, exactly mirroring run_service.py's
    _create_project_run() + background_tasks.add_task() pattern for real
    deploys.
    """
    source_type = str(source_config.get("type") or "").strip().lower()
    validate_pbix_model_list(selected_sources, source_type=source_type)

    files = _resolve_file_list(source_config, selected_sources)
    if not files:
        raise ValidationError("No .pbix files specified for this dry-run job.")
    if len(files) > MAX_PARALLEL_DRY_RUN_FILES:
        raise ValidationError(
            f"Up to {MAX_PARALLEL_DRY_RUN_FILES} PBIX files are supported per dry-run job; "
            f"{len(files)} were provided."
        )
    _ensure_schema()

    from semabridge.repository.orm.session_factory import db_manager

    job_id = f"dryrun-{uuid.uuid4().hex[:20]}"
    now = _utcnow()

    with db_manager.get_session() as session:
        job = DryRunJob(
            job_id=job_id,
            project_id=project_id,
            status="pending",
            created_at=now,
            source_config_json=json.dumps(source_config or {}),
            target_config_json=json.dumps(target_config or {}),
            requested_by_user_id=str(requested_by_user_id) if requested_by_user_id is not None else None,
        )
        session.add(job)
        file_summaries = []
        for index, pbix_path in enumerate(files):
            file_id = f"{job_id}-f{index}"
            display_name = Path(pbix_path).stem or pbix_path
            job_file = DryRunJobFile(
                file_id=file_id,
                job_id=job_id,
                file_index=index,
                pbix_path=pbix_path,
                display_name=display_name,
                status="pending",
            )
            session.add(job_file)
            file_summaries.append({
                "file_id": file_id,
                "filename": display_name,
                "pbix_path": pbix_path,
                "status": "pending",
            })

    return {
        "job_id": job_id,
        "project_id": project_id,
        "status": "pending",
        "files": file_summaries,
    }


def _run_one_file_sync(
    *,
    project_id: str,
    requested_by_user_id: Optional[str],
    source_config: Dict[str, Any],
    target_config: Dict[str, Any],
    pbix_path: str,
) -> Dict[str, Any]:
    """Runs _run_dry_run_pipeline() for exactly one file, on its own thread
    with its own event loop (see run_dry_run_job()'s docstring for why: the
    pipeline's `await sync_models(...)` call is actually synchronous/blocking
    under the hood, so N files must run on N real OS threads to be genuinely
    concurrent — an asyncio.gather() over N coroutines on one thread would
    not overlap at all).
    """
    from semabridge.api.controllers.mappings_controller import _run_dry_run_pipeline

    # Strip a stray singular pbix_path so _build_sync_jobs()'s "explicit
    # pbix_path wins over models" precedence can't silently make every file
    # in the batch resolve to the same (wrong) file — this call is scoped to
    # exactly one file via selected_sources=[pbix_path] below.
    scoped_source_config = dict(source_config or {})
    scoped_source_config.pop("pbix_path", None)

    return asyncio.run(_run_dry_run_pipeline(
        project_id=project_id,
        request_user_id=requested_by_user_id,
        source_config=scoped_source_config,
        target_config=target_config,
        selected_sources=[pbix_path],
    ))


def _aggregate_job_status(file_statuses: List[str]) -> str:
    if any(s in ("pending", "running") for s in file_statuses):
        return "running"
    if all(s == "success" for s in file_statuses):
        return "success"
    if all(s == "failed" for s in file_statuses):
        return "failed"
    return "partial"


def run_dry_run_job(job_id: str) -> None:
    """Background task body: runs every pending/running file in this job in
    parallel (ThreadPoolExecutor, mirroring sync_execution_service.py's own
    proven _run_parallel_jobs() pattern), writing each file's result back to
    its own DryRunJobFile row as soon as it completes -- independently of its
    siblings, so a partial failure never hides or delays the files that
    succeeded.
    """
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job = session.get(DryRunJob, job_id)
        if job is None:
            logger.error("run_dry_run_job: job %s not found", job_id)
            return
        source_config = json.loads(job.source_config_json)
        target_config = json.loads(job.target_config_json)
        project_id = job.project_id
        requested_by_user_id = job.requested_by_user_id
        pending_files = [f for f in job.files if f.status in ("pending", "running")]
        file_ids = [f.file_id for f in pending_files]
        job.status = "running"

    if not file_ids:
        return

    results: Dict[str, Dict[str, Any]] = {}
    errors: Dict[str, str] = {}

    with db_manager.get_session() as session:
        for file_id in file_ids:
            job_file = session.get(DryRunJobFile, file_id)
            if job_file is not None:
                job_file.status = "running"
                job_file.started_at = _utcnow()

    max_workers = min(len(file_ids), MAX_PARALLEL_DRY_RUN_FILES)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with db_manager.get_session() as session:
            paths_by_file_id = {f.file_id: f.pbix_path for f in session.get(DryRunJob, job_id).files if f.file_id in file_ids}

        future_to_file_id = {
            executor.submit(
                _run_one_file_sync,
                project_id=project_id,
                requested_by_user_id=requested_by_user_id,
                source_config=source_config,
                target_config=target_config,
                pbix_path=paths_by_file_id[file_id],
            ): file_id
            for file_id in file_ids
        }
        for future in as_completed(future_to_file_id):
            file_id = future_to_file_id[future]
            try:
                results[file_id] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Dry-run job %s file %s crashed: %s", job_id, file_id, exc)
                errors[file_id] = str(exc)

    with db_manager.get_session() as session:
        for file_id in file_ids:
            job_file = session.get(DryRunJobFile, file_id)
            if job_file is None:
                continue
            job_file.completed_at = _utcnow()
            if file_id in errors:
                job_file.status = "failed"
                job_file.error_message = errors[file_id]
                continue
            result = results.get(file_id) or {}
            # extraction_failed=True is a "soft" failure the pipeline returns
            # successfully rather than raising (see _run_dry_run_pipeline) —
            # must still surface as a failed file, not a silent success with
            # zero fields, per "show real error messages, never hide failures."
            if result.get("success") and not result.get("extraction_failed"):
                job_file.status = "success"
            else:
                job_file.status = "failed"
                job_file.error_message = str(
                    result.get("error")
                    or ("Extraction failed — no semantic model could be built from this file." if result.get("extraction_failed") else None)
                    or "Dry run did not succeed."
                )
            job_file.result_json = json.dumps(result)

        job = session.get(DryRunJob, job_id)
        if job is not None:
            all_statuses = [f.status for f in job.files]
            job.status = _aggregate_job_status(all_statuses)
            job.completed_at = _utcnow()


def get_dry_run_job_status(job_id: str) -> Dict[str, Any]:
    """Lightweight per-file status list -- what the per-file list view (Part
    C) polls every few seconds. Does NOT include each file's full
    entity_mappings payload (see get_dry_run_job_file_result for that).
    """
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job = session.get(DryRunJob, job_id)
        if job is None:
            raise NotFoundError(f"Dry-run job '{job_id}' not found.")
        return {
            "job_id": job.job_id,
            "project_id": job.project_id,
            "status": job.status,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "files": [
                {
                    "file_id": f.file_id,
                    "filename": f.display_name,
                    "pbix_path": f.pbix_path,
                    "status": f.status,
                    "error_summary": f.error_message,
                }
                for f in job.files
            ],
        }


def get_dry_run_job_file_result(job_id: str, file_id: str) -> Dict[str, Any]:
    """Full dry-run payload for ONE file — same RAW shape _run_dry_run_pipeline()/
    the single-file /dry-run endpoint has always returned (source_name/
    target_name/source_expression/entity_mappings/...). This is what the
    drill-in view (Part C) fetches.

    NOTE: this is NOT the NormalizedRow[] shape DryRunMappingTable's `mappings`
    prop requires — the single-file /dry-run caller (CreateProjectPage.jsx)
    has always run this exact shape through utils/normalizeRows.js before
    handing it to DryRunMappingTable, never straight into the component. An
    earlier version of this docstring claimed otherwise and the multi-file
    drill-in (MultiFileDryRunStatus.jsx) took it literally, skipping
    normalizeRows() entirely — producing blank DAX expressions and "fx
    Measure"/"unknown"/"— unmapped —" placeholders on every row despite
    successful dry runs. Any frontend caller of this endpoint MUST call
    normalizeRows(result) before rendering it.
    """
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job_file = session.get(DryRunJobFile, file_id)
        if job_file is None or job_file.job_id != job_id:
            raise NotFoundError(f"Dry-run job file '{file_id}' not found on job '{job_id}'.")
        if job_file.status in ("pending", "running"):
            return {
                "file_id": file_id,
                "filename": job_file.display_name,
                "status": job_file.status,
                "result": None,
            }
        result = json.loads(job_file.result_json) if job_file.result_json else None
        return {
            "file_id": file_id,
            "filename": job_file.display_name,
            "status": job_file.status,
            "error": job_file.error_message,
            "result": result,
        }


def reset_dry_run_job_file_for_rerun(job_id: str, file_id: str) -> Dict[str, Any]:
    """Resets exactly one file's row to pending, leaving every sibling row
    (and the parent job's other files) completely untouched -- the caller
    (dry_run_jobs_controller.py) is expected to schedule
    run_dry_run_job(job_id) again via BackgroundTasks right after this
    returns; run_dry_run_job() only re-processes files whose status is
    pending/running, so sibling files that already succeeded or failed are
    skipped, not re-run.
    """
    _ensure_schema()
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        job_file = session.get(DryRunJobFile, file_id)
        if job_file is None or job_file.job_id != job_id:
            raise NotFoundError(f"Dry-run job file '{file_id}' not found on job '{job_id}'.")
        job_file.status = "pending"
        job_file.error_message = None
        job_file.result_json = None
        job_file.started_at = None
        job_file.completed_at = None

        job = session.get(DryRunJob, job_id)
        if job is not None:
            job.status = "running"
            job.completed_at = None

        filename = job_file.display_name

    return {"job_id": job_id, "file_id": file_id, "filename": filename, "status": "pending"}
