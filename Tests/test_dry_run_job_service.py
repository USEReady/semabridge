"""Part B: multi-PBIX background dry-run job tests.

Covers:
  - create_dry_run_job(): validation (reusing Part A/D validators), file-list
    resolution, DB row creation.
  - run_dry_run_job(): N files processed independently and in parallel; a
    partial failure surfaces BOTH the success and the failure with real
    messages, never hiding the success.
  - Independent per-file rerun: resetting/rerunning one failed file does not
    touch a sibling's already-completed (success OR failed) state.
  - Genuine parallelism: two files' pipeline calls actually overlap in wall
    time, not just "both eventually complete" (which a sequential
    implementation would also satisfy).
"""
from __future__ import annotations

import os
import sys
import time
import types

import pytest

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_dry_run_job_service.sqlite")
os.environ.setdefault("AUTH_ENABLED", "false")

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.api.services import dry_run_job_service as job_service
from semabridge.domain.exceptions import NotFoundError, ValidationError

FILE_A = "C:/Reports/Sales Report.pbix"
FILE_B = "C:/Reports/Marketing Analysis.pbix"
FILE_C = "C:/Reports/Ops Dashboard.pbix"


def _mock_pipeline(results_by_path, delays_by_path=None, calls=None, concurrency_tracker=None):
    """Builds a fake _run_dry_run_pipeline replacement keyed by the single
    entry in selected_sources (each job-file call is always scoped to
    exactly one file — see dry_run_job_service._run_one_file_sync).
    """
    delays_by_path = delays_by_path or {}

    async def _fake(*, project_id, request_user_id, source_config, target_config, selected_sources):
        path = selected_sources[0]
        if calls is not None:
            calls.append(path)
        if concurrency_tracker is not None:
            concurrency_tracker["current"] += 1
            concurrency_tracker["max"] = max(concurrency_tracker["max"], concurrency_tracker["current"])
        delay = delays_by_path.get(path, 0)
        if delay:
            time.sleep(delay)
        if concurrency_tracker is not None:
            concurrency_tracker["current"] -= 1
        outcome = results_by_path[path]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return _fake


def _success_result(filename: str) -> dict:
    return {
        "success": True,
        "project_id": "preview-test",
        "model_name": filename,
        "entity_mappings": [{"source_name": "AMOUNT", "source_file": filename}],
        "extraction_failed": False,
        "dropped_entities": [],
        "summary": {"total_fields": 1, "auto_mapped": 1, "unmapped": 0, "collisions": 0, "predicted_failures": 0, "extraction_failed": False},
    }


@pytest.fixture(autouse=True)
def _patch_pipeline(monkeypatch):
    """Every test in this file patches the pipeline explicitly via the
    helper below; this fixture just ensures a clean import of the target
    module attribute so monkeypatch.setattr always finds a real target.
    """
    import semabridge.api.controllers.mappings_controller as mc
    assert hasattr(mc, "_run_dry_run_pipeline")
    yield


def _patch(monkeypatch, fake_pipeline):
    import semabridge.api.controllers.mappings_controller as mc
    monkeypatch.setattr(mc, "_run_dry_run_pipeline", fake_pipeline)


# ---------------------------------------------------------------------------
# create_dry_run_job
# ---------------------------------------------------------------------------


def test_create_dry_run_job_creates_pending_job_and_files():
    job = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A, FILE_B],
    )
    assert job["status"] == "pending"
    assert len(job["files"]) == 2
    assert {f["filename"] for f in job["files"]} == {"Sales Report", "Marketing Analysis"}
    assert all(f["status"] == "pending" for f in job["files"])

    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["status"] == "pending"
    assert len(status["files"]) == 2


def test_create_dry_run_job_no_longer_rejects_naming_collision(tmp_path):
    # Naming disambiguation now happens once, at real job-construction time
    # (_build_sync_jobs, see test_pbix_view_name_collision.py) -- a
    # same-named-file collision no longer blocks dry-run job creation.
    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Sales_Report.pbix"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    result = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[str(file_a), str(file_b)],
    )
    assert result["job_id"]
    assert len(result["files"]) == 2


def test_create_dry_run_job_rejects_bad_file_list():
    with pytest.raises(ValidationError):
        job_service.create_dry_run_job(
            project_id="preview-test",
            source_config={"type": "pbix"},
            target_config={"type": "snowflake"},
            selected_sources=["not-a-pbix-path"],
        )


def test_create_dry_run_job_rejects_empty_file_list():
    with pytest.raises(ValidationError, match="No .pbix files"):
        job_service.create_dry_run_job(
            project_id="preview-test",
            source_config={"type": "pbix"},
            target_config={"type": "snowflake"},
            selected_sources=[],
        )


# ---------------------------------------------------------------------------
# run_dry_run_job — independent per-file outcomes, partial failure visibility
# ---------------------------------------------------------------------------


def test_run_dry_run_job_processes_all_files_independently(monkeypatch):
    job = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A, FILE_B],
    )
    fake = _mock_pipeline({
        FILE_A: _success_result("Sales Report"),
        FILE_B: _success_result("Marketing Analysis"),
    })
    _patch(monkeypatch, fake)

    job_service.run_dry_run_job(job["job_id"])

    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["status"] == "success"
    assert all(f["status"] == "success" for f in status["files"])


def test_run_dry_run_job_partial_failure_shows_all_files_with_real_errors(monkeypatch):
    """The core 'never hide successes on partial failure' requirement."""
    job = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A, FILE_B],
    )
    fake = _mock_pipeline({
        FILE_A: _success_result("Sales Report"),
        FILE_B: RuntimeError("Snowflake credential invalid for this file's target."),
    })
    _patch(monkeypatch, fake)

    job_service.run_dry_run_job(job["job_id"])

    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["status"] == "partial"
    assert len(status["files"]) == 2  # both files still shown, neither hidden

    by_name = {f["filename"]: f for f in status["files"]}
    assert by_name["Sales Report"]["status"] == "success"
    assert by_name["Marketing Analysis"]["status"] == "failed"
    assert "Snowflake credential invalid for this file's target." in by_name["Marketing Analysis"]["error_summary"]

    # Drill-in for the successful file still returns its real entity_mappings.
    success_file_id = next(f["file_id"] for f in status["files"] if f["filename"] == "Sales Report")
    detail = job_service.get_dry_run_job_file_result(job["job_id"], success_file_id)
    assert detail["status"] == "success"
    assert detail["result"]["entity_mappings"][0]["source_file"] == "Sales Report"


def test_run_dry_run_job_extraction_failed_counts_as_file_failure(monkeypatch):
    """_run_dry_run_pipeline returns success=True, extraction_failed=True as
    a 'soft' failure rather than raising -- must still surface as a failed
    file, not a silent success with zero fields.
    """
    job = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A],
    )
    soft_failure = _success_result("Sales Report")
    soft_failure["extraction_failed"] = True
    soft_failure["entity_mappings"] = []
    fake = _mock_pipeline({FILE_A: soft_failure})
    _patch(monkeypatch, fake)

    job_service.run_dry_run_job(job["job_id"])

    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["status"] == "failed"
    assert status["files"][0]["status"] == "failed"
    assert "Extraction failed" in status["files"][0]["error_summary"]


def test_run_dry_run_job_all_failed_status_is_failed_not_partial(monkeypatch):
    job = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A, FILE_B],
    )
    fake = _mock_pipeline({
        FILE_A: RuntimeError("boom A"),
        FILE_B: RuntimeError("boom B"),
    })
    _patch(monkeypatch, fake)

    job_service.run_dry_run_job(job["job_id"])

    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["status"] == "failed"


# ---------------------------------------------------------------------------
# Independent per-file rerun
# ---------------------------------------------------------------------------


def test_rerun_one_failed_file_does_not_touch_sibling_success_state(monkeypatch):
    job = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A, FILE_B],
    )
    fake_first = _mock_pipeline({
        FILE_A: _success_result("Sales Report"),
        FILE_B: RuntimeError("transient network error"),
    })
    _patch(monkeypatch, fake_first)
    job_service.run_dry_run_job(job["job_id"])

    status = job_service.get_dry_run_job_status(job["job_id"])
    by_name = {f["filename"]: f for f in status["files"]}
    success_file_id = by_name["Sales Report"]["file_id"]
    failed_file_id = by_name["Marketing Analysis"]["file_id"]

    success_completed_at_before = job_service.get_dry_run_job_file_result(job["job_id"], success_file_id)

    # Re-run just the failed file, this time succeeding.
    job_service.reset_dry_run_job_file_for_rerun(job["job_id"], failed_file_id)
    fake_second = _mock_pipeline({FILE_B: _success_result("Marketing Analysis")})
    _patch(monkeypatch, fake_second)
    job_service.run_dry_run_job(job["job_id"])

    status_after = job_service.get_dry_run_job_status(job["job_id"])
    by_name_after = {f["filename"]: f for f in status_after["files"]}
    assert by_name_after["Marketing Analysis"]["status"] == "success"
    # The sibling that already succeeded is completely untouched.
    assert by_name_after["Sales Report"]["status"] == "success"
    success_completed_at_after = job_service.get_dry_run_job_file_result(job["job_id"], success_file_id)
    assert success_completed_at_after["result"] == success_completed_at_before["result"]
    assert status_after["status"] == "success"


def test_reset_dry_run_job_file_for_rerun_raises_for_unknown_file():
    job = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A],
    )
    with pytest.raises(NotFoundError):
        job_service.reset_dry_run_job_file_for_rerun(job["job_id"], "not-a-real-file-id")


# ---------------------------------------------------------------------------
# Genuine parallelism (not just "both eventually finish")
# ---------------------------------------------------------------------------


def test_run_dry_run_job_actually_overlaps_multiple_files_in_wall_time(monkeypatch):
    job = job_service.create_dry_run_job(
        project_id="preview-test",
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A, FILE_B, FILE_C],
    )
    tracker = {"current": 0, "max": 0}
    fake = _mock_pipeline(
        {
            FILE_A: _success_result("Sales Report"),
            FILE_B: _success_result("Marketing Analysis"),
            FILE_C: _success_result("Ops Dashboard"),
        },
        delays_by_path={FILE_A: 0.3, FILE_B: 0.3, FILE_C: 0.3},
        concurrency_tracker=tracker,
    )
    _patch(monkeypatch, fake)

    started = time.time()
    job_service.run_dry_run_job(job["job_id"])
    elapsed = time.time() - started

    # A sequential implementation would take >= 0.9s (3 x 0.3s); a parallel
    # one comfortably finishes well under that.
    assert elapsed < 0.8, f"Files did not appear to run in parallel (took {elapsed:.2f}s)"
    assert tracker["max"] >= 2, f"Never observed more than {tracker['max']} concurrent file(s) — not actually parallel"

    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["status"] == "success"
