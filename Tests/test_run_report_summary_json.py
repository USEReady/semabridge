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
