from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import threading
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"

for candidate in (REPO_ROOT, SRC_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.run_summary import RunSummary, StepStatus
from semabridge.core.settings import get_settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


PIPELINE_STAGES: list[tuple[str, str, tuple[int, ...]]] = [
    ("extraction", "Extraction", (4,)),
    ("osi_conversion", "OSI Conversion", (5,)),
    ("sml_generation", "SML Generation", (6, 7)),
    ("snowflake_deployment", "Snowflake Deployment", (8, 9)),
]


@dataclass
class SyncJobState:
    job_id: str
    source_type: str
    project_name: Optional[str] = None
    status: str = "queued"
    started_at: str = field(default_factory=_utc_now)
    completed_at: Optional[str] = None
    error: Optional[str] = None
    deployment_confirmed: bool = False
    steps: dict[int, dict[str, Any]] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    summary: Optional[dict[str, Any]] = None
    temp_dir: Optional[str] = None
    pbix_path: Optional[str] = None

    def stage_states(self) -> list[dict[str, str]]:
        stage_rows: list[dict[str, str]] = []
        prior_complete = True

        for stage_id, label, step_numbers in PIPELINE_STAGES:
            step_statuses = [
                str(self.steps.get(step_number, {}).get("status", "")).lower()
                for step_number in step_numbers
                if step_number in self.steps
            ]

            if any(status == StepStatus.FAILED.value for status in step_statuses):
                state = "failed"
            elif len(step_statuses) == len(step_numbers) and all(
                status in {StepStatus.SUCCESS.value, StepStatus.SKIPPED.value}
                for status in step_statuses
            ):
                state = "success"
            elif step_statuses:
                state = "running"
            elif self.status in {"queued", "running"} and prior_complete:
                state = "running"
            else:
                state = "pending"

            if state != "success":
                prior_complete = False

            stage_rows.append({"id": stage_id, "label": label, "status": state})

        return stage_rows

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["stage_states"] = self.stage_states()
        return payload


class QueueLogHandler(logging.Handler):
    def __init__(self, job_id: str):
        super().__init__()
        self.job_id = job_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        _append_log(self.job_id, message)


_jobs: dict[str, SyncJobState] = {}
_jobs_lock = threading.Lock()


def _append_log(job_id: str, message: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job.logs.append(message)
        if len(job.logs) > 500:
            job.logs = job.logs[-500:]


def _update_step(
    job_id: str,
    step_number: int,
    status: Any,
    message: Optional[str] = None,
    artifact_ids: Optional[list[str]] = None,
) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job.steps[step_number] = {
            "step_number": step_number,
            "status": str(status),
            "message": message,
            "artifact_ids": artifact_ids or [],
            "updated_at": _utc_now(),
        }
        if step_number == 9 and str(status) == StepStatus.SUCCESS.value:
            job.deployment_confirmed = True


def _set_job_status(
    job_id: str,
    *,
    status: Optional[str] = None,
    error: Optional[str] = None,
    summary: Optional[dict[str, Any]] = None,
) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        if status:
            job.status = status
        if error is not None:
            job.error = error
        if summary is not None:
            job.summary = summary
        if status in {"success", "failed"}:
            job.completed_at = _utc_now()


def _cleanup_job_files(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        temp_dir = job.temp_dir if job else None
        if job:
            job.temp_dir = None
            job.pbix_path = None
    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)


@contextmanager
def _temporary_env(overrides: dict[str, str]) -> Any:
    sentinel = object()
    previous: dict[str, object] = {}

    for key, value in overrides.items():
        previous[key] = os.environ.get(key, sentinel)
        os.environ[key] = value

    get_settings.cache_clear()
    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is sentinel:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(old_value)
        get_settings.cache_clear()


def _run_sync_job(
    job_id: str,
    *,
    source_type: str,
    project_name: Optional[str],
    dataset_id: Optional[str],
    fabric_workspace_id: Optional[str],
    pbix_path: Optional[str],
    env_overrides: dict[str, str],
) -> None:
    _set_job_status(job_id, status="running")

    handler = QueueLogHandler(job_id)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        if source_type == "pbix":
            connector = LocalPBIXConnector({"pbix_path": pbix_path})
            connector.authenticate()
            _append_log(job_id, f"Validated PBIX archive: {pbix_path}")

        with _temporary_env(env_overrides):
            engine = ExecutionEngine()
            original_record_step = engine._record_step

            def patched_record_step(
                step_number: int,
                status: Any,
                message: Optional[str] = None,
                artifact_ids: Optional[list[str]] = None,
            ) -> None:
                original_record_step(step_number, status, message, artifact_ids)
                _update_step(
                    job_id,
                    step_number=step_number,
                    status=status,
                    message=message,
                    artifact_ids=artifact_ids,
                )
                _append_log(
                    job_id,
                    f"STEP | {step_number} | {status} | {message or ''}".strip(),
                )

            engine._record_step = patched_record_step  # type: ignore[method-assign]

            summary: RunSummary = engine.execute(
                source=source_type,
                target="snowflake",
                project_name=project_name,
                deploy=True,
                dataset_id=dataset_id,
                workspace_id=fabric_workspace_id,
                pbix_path=pbix_path,
            )

        summary_payload = summary.to_json()
        raw_status = getattr(summary, "status", None)
        status_text = str(getattr(raw_status, "value", raw_status or ""))
        normalized_status = status_text.split(".")[-1].upper()
        final_status = "success" if normalized_status == "SUCCESS" else "failed"
        if final_status == "failed":
            error_message: Optional[str] = None

            # 1) Prefer structured RunSummary errors when available.
            if summary_payload.get("errors"):
                error_message = summary_payload.get("errors", [{}])[0].get("message")

            # 2) Fall back to the most recent failed step message.
            if not error_message:
                failed_steps = [
                    step
                    for step in (summary_payload.get("steps_completed") or [])
                    if str(step.get("status", "")).lower() == "failed"
                ]
                if failed_steps:
                    last_failed = failed_steps[-1]
                    step_name = str(last_failed.get("step_name") or "Unknown step")
                    step_message = str(last_failed.get("message") or "").strip()
                    error_message = (
                        f"{step_name}: {step_message}" if step_message else f"{step_name} failed."
                    )

            # 3) If RunSummary missed early failures, use job-level step tracking.
            if not error_message:
                with _jobs_lock:
                    job = _jobs.get(job_id)
                    step_rows = list((job.steps or {}).values()) if job else []
                failed_step_rows = [
                    row for row in step_rows
                    if str(row.get("status", "")).lower() == "failed"
                ]
                if failed_step_rows:
                    last_failed_row = sorted(
                        failed_step_rows,
                        key=lambda row: int(row.get("step_number") or 0),
                    )[-1]
                    step_number = int(last_failed_row.get("step_number") or 0)
                    step_message = str(last_failed_row.get("message") or "").strip()
                    if step_message:
                        error_message = f"Step {step_number} failed: {step_message}"
                    else:
                        error_message = f"Step {step_number} failed."

            # 4) Fall back to streamed job logs.
            if not error_message:
                with _jobs_lock:
                    job = _jobs.get(job_id)
                    recent_logs = list(job.logs) if job else []
                for line in reversed(recent_logs):
                    if "ERROR" in line.upper() or "Traceback" in line:
                        error_message = line
                        break
                if not error_message:
                    for line in reversed(recent_logs):
                        if line and line.strip():
                            error_message = line.strip()
                            break

            if not error_message:
                error_message = "Execution failed before step diagnostics were captured."

            _set_job_status(
                job_id,
                status=final_status,
                error=error_message,
                summary=summary_payload,
            )
        else:
            _set_job_status(job_id, status=final_status, summary=summary_payload)

    except Exception as exc:
        logging.getLogger(__name__).exception("UI sync job %s failed", job_id)
        tb_text = traceback.format_exc()
        for line in tb_text.strip().splitlines():
            _append_log(job_id, f"TRACEBACK | {line}")
        _append_log(job_id, f"ERROR | {type(exc).__name__} | {exc}")
        _set_job_status(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        root_logger.removeHandler(handler)
        _cleanup_job_files(job_id)


router = APIRouter(prefix="/ui", tags=["ui-sync"])


@router.post("/sync-jobs")
async def start_sync_job(
    source_type: str = Form(...),
    project_name: Optional[str] = Form(default=None),
    dataset_id: Optional[str] = Form(default=None),
    fabric_workspace_id: Optional[str] = Form(default=None),
    pbix_path: Optional[str] = Form(default=None),
    snowflake_account: Optional[str] = Form(default=None),
    snowflake_warehouse: Optional[str] = Form(default=None),
    snowflake_database: Optional[str] = Form(default=None),
    snowflake_schema: Optional[str] = Form(default=None),
    snowflake_user: Optional[str] = Form(default=None),
    snowflake_password: Optional[str] = Form(default=None),
    snowflake_role: Optional[str] = Form(default=None),
    pbix_file: Optional[UploadFile] = File(default=None),
) -> dict[str, Any]:
    normalized_source = str(source_type or "").strip().lower()
    if normalized_source not in {"fabric", "pbix"}:
        raise HTTPException(status_code=400, detail="source_type must be 'fabric' or 'pbix'.")

    if normalized_source == "fabric" and not str(dataset_id or "").strip():
        raise HTTPException(status_code=400, detail="dataset_id is required for Fabric sync.")

    temp_dir: Optional[str] = None
    resolved_pbix_path: Optional[str] = None

    if normalized_source == "pbix":
        if pbix_file is not None:
            if not pbix_file.filename or not pbix_file.filename.lower().endswith(".pbix"):
                raise HTTPException(status_code=400, detail="Only .pbix files are supported.")

            temp_dir = tempfile.mkdtemp(prefix="semabridge_ui_")
            resolved_pbix_path = str(Path(temp_dir) / pbix_file.filename)
            payload = await pbix_file.read()
            Path(resolved_pbix_path).write_bytes(payload)
        else:
            candidate = str(pbix_path or "").strip()
            if not candidate:
                raise HTTPException(status_code=400, detail="Provide pbix_path or upload a .pbix file.")
            candidate_path = Path(os.path.expandvars(os.path.expanduser(candidate))).resolve()
            if not candidate_path.exists() or not candidate_path.is_file():
                raise HTTPException(status_code=400, detail=f"PBIX file not found: {candidate_path}")
            if candidate_path.suffix.lower() != ".pbix":
                raise HTTPException(status_code=400, detail="pbix_path must point to a .pbix file.")
            resolved_pbix_path = str(candidate_path)

    env_overrides = {
        key: value.strip()
        for key, value in {
            "SNOWFLAKE_ACCOUNT": snowflake_account,
            "SNOWFLAKE_WAREHOUSE": snowflake_warehouse,
            "SNOWFLAKE_DATABASE": snowflake_database,
            "SNOWFLAKE_SCHEMA": snowflake_schema,
            "SNOWFLAKE_USER": snowflake_user,
            "SNOWFLAKE_PASSWORD": snowflake_password,
            "SNOWFLAKE_ROLE": snowflake_role,
        }.items()
        if value and value.strip()
    }

    job_id = str(uuid.uuid4())
    job = SyncJobState(
        job_id=job_id,
        source_type=normalized_source,
        project_name=(project_name or "").strip() or None,
        temp_dir=temp_dir,
        pbix_path=resolved_pbix_path,
    )
    with _jobs_lock:
        _jobs[job_id] = job

    worker = threading.Thread(
        target=_run_sync_job,
        kwargs={
            "job_id": job_id,
            "source_type": normalized_source,
            "project_name": (project_name or "").strip() or None,
            "dataset_id": (dataset_id or "").strip() or None,
            "fabric_workspace_id": (fabric_workspace_id or "").strip() or None,
            "pbix_path": resolved_pbix_path,
            "env_overrides": env_overrides,
        },
        name=f"ui-sync-{job_id[:8]}",
        daemon=True,
    )
    worker.start()

    return {"job_id": job_id, "status": job.status}


@router.get("/sync-jobs/{job_id}")
async def get_sync_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Sync job not found.")
        return job.to_dict()


app = FastAPI(title="SemaBridge UI Bridge", version="1.0.0")
app.include_router(router)
