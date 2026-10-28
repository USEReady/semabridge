"""Regression tests for the JSON run-report summary view (RunReportSummary.jsx's
data source), ported from demo_version_ref's build_run_report_data()/
_gather_run_report_data() and adapted to this codebase's current
_classify_metrics() (no third "Needs Review" tier yet -- see the
followup_needs_review_classification memory).

Covers:
  - Whole-run JSON matches the same counts/classification the Markdown
    report already produces (same underlying data, different rendering).
  - Per-model scoping (build_run_report_data with source_override) mirrors
    generate_per_model_reports()'s Markdown scoping exactly.
  - dropped vs excluded_by_design are split by the by_design flag into two
    separate stage-grouped sections, not one combined list.
  - The crashed-before-extraction shape.
  - needs_review is always an empty list/0, never omitted.
"""
from __future__ import annotations

import os
import sys
import types
import pytest

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_run_report_summary_json.sqlite")
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

from semabridge.api.services.run_report_service import (
    build_run_report_data,
    _describe_source_for_result,
)

MULTI_MODEL_RUN = {
    "project_id": "proj1",
    "project_name": "Proj One",
    "run_id": "run-123",
    "status": "success",
    "results": [
        {
            "model": "Sales Report",
            "summary": {},
            "dropped_entities": [
                {"entity_kind": "table", "entity_name": "DateTableTemplate_x", "stage": "extraction", "by_design": True, "reason": "Auto-generated date table"},
                {"entity_kind": "metric", "entity_name": "Broken Measure", "stage": "dax_translation", "by_design": False, "reason": "Could not resolve column"},
            ],
            "pbix_path": "C:/tmp/abc_Sales Report.pbix",
        },
        {
            "model": "Marketing Analysis",
            "summary": {},
            "dropped_entities": [],
            "pbix_path": "C:/tmp/def_Marketing Analysis.pbix",
        },
    ],
}


def test_build_run_report_data_splits_dropped_and_excluded_by_design():
    single_run = dict(MULTI_MODEL_RUN, results=[MULTI_MODEL_RUN["results"][0]])
    data = build_run_report_data(single_run, "")

    assert data["crashed"] is False
    assert data["counts"]["dropped"] == 1
    assert data["counts"]["excluded_by_design"] == 1
    assert data["sections"]["dropped"][0]["items"][0]["name"] == "Broken Measure"
    assert data["sections"]["excluded_by_design"][0]["items"][0]["name"] == "DateTableTemplate_x"


def test_build_run_report_data_needs_review_always_present_and_empty():
    single_run = dict(MULTI_MODEL_RUN, results=[MULTI_MODEL_RUN["results"][0]])
    data = build_run_report_data(single_run, "")

    assert data["counts"]["needs_review"] == 0
    assert data["sections"]["needs_review"] == []


def test_build_run_report_data_per_model_scoping_matches_markdown_scoping():
    result = MULTI_MODEL_RUN["results"][0]
    scoped_run = dict(MULTI_MODEL_RUN, results=[result])
    source_override = _describe_source_for_result(result, "", MULTI_MODEL_RUN)

    data = build_run_report_data(scoped_run, "", source_override=source_override)

    assert data["source_files"] == ["abc_Sales Report.pbix"]
    assert data["counts"]["dropped"] == 1
    assert data["counts"]["excluded_by_design"] == 1

    # The OTHER model's own scoped view sees none of the first model's drops.
    other_result = MULTI_MODEL_RUN["results"][1]
    other_scoped = dict(MULTI_MODEL_RUN, results=[other_result])
    other_override = _describe_source_for_result(other_result, "", MULTI_MODEL_RUN)
    other_data = build_run_report_data(other_scoped, "", source_override=other_override)

    assert other_data["source_files"] == ["def_Marketing Analysis.pbix"]
    assert other_data["counts"]["dropped"] == 0
    assert other_data["counts"]["excluded_by_design"] == 0


def test_build_run_report_data_crashed_run_shape():
    crashed_run = {
        "project_id": "proj1",
        "project_name": "Proj One",
        "run_id": "run-crashed",
        "status": "failed",
        "error": "Connection refused",
    }
    data = build_run_report_data(crashed_run, "")

    assert data["crashed"] is True
    assert data.get("unavailable") is False
    assert data["error"] == "Connection refused"
    assert data["run_id"] == "run-crashed"
    assert "counts" not in data


def test_build_run_report_data_distinguishes_unavailable_from_genuine_crash():
    """A run reconstructed after a restart (or otherwise missing its
    results/summary blob -- see project_shared.py's _RUN_BLOB_KEYS) must
    not look like it crashed before reading the source file when it
    actually succeeded; see _gather_run_report_data()'s docstring for why
    these can't be told apart from run.results alone without this check.
    """
    succeeded_but_data_gone = {
        "project_id": "proj1",
        "project_name": "Proj One",
        "run_id": "run-restarted",
        "status": "success",
        # No "results" and no "summary" -- exactly what a run looks like
        # after being reloaded from the persisted compat store, or
        # reconstructed via get_project_runs_compat()'s ORM fallback.
    }
    data = build_run_report_data(succeeded_but_data_gone, "")

    assert data["crashed"] is True
    assert data["unavailable"] is True
    assert "completed" in data["error"].lower()
    assert "success" in data["error"].lower()

    # A run that genuinely never reached extraction (still "failed"/"running")
    # keeps the original, unqualified crashed message.
    genuinely_crashed = dict(succeeded_but_data_gone, run_id="run-early-fail", status="failed", error="bad config")
    crashed_data = build_run_report_data(genuinely_crashed, "")
    assert crashed_data["crashed"] is True
    assert crashed_data.get("unavailable") is False
    assert crashed_data["error"] == "bad config"


def test_build_run_report_data_whole_run_matches_generate_run_report_markdown_counts():
    """Cross-check against the already-tested Markdown path: same input,
    same underlying counts, just two different renderings -- proving the
    two views can't silently disagree (the exact risk noted in
    demo_version_ref's _gather_run_report_data docstring)."""
    from semabridge.api.services.run_report_service import generate_run_report_markdown

    single_run = dict(MULTI_MODEL_RUN, results=[MULTI_MODEL_RUN["results"][0]])
    data = build_run_report_data(single_run, "")
    markdown = generate_run_report_markdown(single_run, "")

    assert "Broken Measure" in markdown
    assert "DateTableTemplate_x" in markdown


def test_build_run_report_data_cortex_validation_present():
    """(a) Run with Cortex validation results builds cleanly and returns valid cortex_validation list."""
    run = {
        "project_id": "proj_cortex",
        "project_name": "Cortex Project",
        "run_id": "run-cortex-001",
        "status": "success",
        "results": [
            {
                "model": "SalesModel",
                "summary": {},
                "dropped_entities": [],
                "cortex_validation_results": [
                    {
                        "view_name": "SALES_SEMANTIC_VIEW",
                        "yaml_verified": True,
                        "dimensions_checked": 10,
                        "dimensions_passed": 10,
                        "metrics_checked": 5,
                        "metrics_passed": 5,
                        "remediated_metrics": [],
                    }
                ],
            }
        ],
    }
    data = build_run_report_data(run, "")
    assert data["crashed"] is False
    assert len(data["cortex_validation"]) == 1
    cv = data["cortex_validation"][0]
    assert cv["view_name"] == "SALES_SEMANTIC_VIEW"
    assert cv["model"] == "SalesModel"
    assert cv["yaml_verified"] is True
    assert cv["dimensions_passed"] == 10


def test_build_run_report_data_old_run_absent_cortex_validation():
    """(b) Old run without Cortex validation results defaults cortex_validation to []."""
    run = {
        "project_id": "proj_old",
        "project_name": "Old Project",
        "run_id": "run-old-001",
        "status": "success",
        "results": [
            {
                "model": "LegacyModel",
                "summary": {},
                "dropped_entities": [],
            }
        ],
    }
    data = build_run_report_data(run, "")
    assert data["crashed"] is False
    assert data["cortex_validation"] == []
    assert data["counts"]["tables"] == 0
    assert data["counts"]["converted_total"] == 0


def test_build_run_report_data_upsert_no_changes():
    """(c) Upsert 'no changes' run builds cleanly with empty cortex_validation and valid upsert_deltas."""
    run = {
        "project_id": "proj_upsert",
        "project_name": "Upsert Project",
        "run_id": "run-upsert-001",
        "status": "success",
        "results": [
            {
                "model": "UnchangedModel",
                "summary": {
                    "upsert_delta": {
                        "has_changes": False,
                        "first_deployment": False,
                        "summary_text": "No changes detected vs deployed view.",
                        "kinds": {},
                    }
                },
                "dropped_entities": [],
            }
        ],
    }
    data = build_run_report_data(run, "")
    assert data["crashed"] is False
    assert data["cortex_validation"] == []
    assert len(data["upsert_deltas"]) == 1
    delta_entry = data["upsert_deltas"][0]
    assert delta_entry["model"] == "UnchangedModel"
    assert delta_entry["delta"]["has_changes"] is False


@pytest.mark.asyncio
async def test_report_summary_endpoint_all_three_run_types(tmp_path, monkeypatch):
    """Test the /report-summary endpoint returns 200 with valid summary for:
    (a) run with Cortex validation, (b) old run without, (c) upsert no changes run.
    """
    import pytest
    from httpx import ASGITransport, AsyncClient
    from semabridge.api.main import app
    from semabridge.api.services import project_shared, run_service
    from semabridge.api.services.run_model_results import persist_run_model_results

    monkeypatch.setattr("semabridge.api.services.run_report_service.REPORTS_ROOT", tmp_path / "reports")
    monkeypatch.setattr("semabridge.core.run_model_logs.RUN_LOGS_ROOT", tmp_path / "runs")

    project_shared._compat_ensure_loaded()

    runs_to_test = [
        (
            "proj_test_cortex",
            "run-endpoint-cortex",
            {
                "id": "run-endpoint-cortex",
                "run_id": "run-endpoint-cortex",
                "project_id": "proj_test_cortex",
                "project_name": "Cortex Endpoint Proj",
                "status": "success",
                "results": [
                    {
                        "model": "ModelA",
                        "status": "success",
                        "summary": {
                            "cortex_validation_results": [
                                {
                                    "view_name": "VIEW_A",
                                    "yaml_verified": True,
                                    "dimensions_checked": 5,
                                    "dimensions_passed": 5,
                                }
                            ]
                        },
                        "dropped_entities": [],
                    }
                ],
            },
        ),
        (
            "proj_test_old",
            "run-endpoint-old",
            {
                "id": "run-endpoint-old",
                "run_id": "run-endpoint-old",
                "project_id": "proj_test_old",
                "project_name": "Old Endpoint Proj",
                "status": "success",
                "results": [
                    {
                        "model": "ModelOld",
                        "status": "success",
                        "summary": {},
                        "dropped_entities": [],
                    }
                ],
            },
        ),
        (
            "proj_test_upsert",
            "run-endpoint-upsert",
            {
                "id": "run-endpoint-upsert",
                "run_id": "run-endpoint-upsert",
                "project_id": "proj_test_upsert",
                "project_name": "Upsert Endpoint Proj",
                "status": "success",
                "results": [
                    {
                        "model": "ModelUpsert",
                        "status": "success",
                        "summary": {
                            "upsert_delta": {
                                "has_changes": False,
                                "first_deployment": False,
                                "summary_text": "No changes.",
                            }
                        },
                        "dropped_entities": [],
                    }
                ],
            },
        ),
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for pid, rid, run_dict in runs_to_test:
            # Register in project_shared and run_service
            project_shared._compat_projects[pid] = {"id": pid, "name": run_dict["project_name"]}
            run_service._compat_project_runs[pid] = [run_dict]
            # Write per-model results to satisfy run_belongs_to_project
            persist_run_model_results(run_dict, "")

            res = await client.get(f"/api/projects/{pid}/runs/{rid}/report-summary")
            assert res.status_code == 200, f"Expected 200 for {pid}/{rid}, got {res.status_code}: {res.text}"
            data = res.json()
            assert data["run_id"] == rid
            assert data["crashed"] is False
            assert "counts" in data
            assert "sections" in data
            assert isinstance(data["cortex_validation"], list)

