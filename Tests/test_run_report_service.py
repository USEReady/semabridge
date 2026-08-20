import os
from pathlib import Path
from types import SimpleNamespace

from semabridge.api.services.run_report_service import (
    build_run_report_data,
    generate_run_report_markdown,
    write_run_report,
    humanize_drop_reason,
)
from semabridge.converter.dax_ast_parser import ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK

def test_humanize_drop_reason():
    rec1 = {"reason": "auto-generated power bi date table"}
    assert "calendar table Power BI creates" in humanize_drop_reason(rec1)

    rec2 = {"reason": "some unknown obscure error reason"}
    assert "This item could not be included in the converted model." in humanize_drop_reason(rec2)


def test_generate_and_write_run_report(tmp_path, monkeypatch):
    monkeypatch.setattr("semabridge.api.services.run_report_service.REPORTS_ROOT", tmp_path)

    run_data = {
        "project_id": "proj-test-123",
        "run_id": "run-456",
        "project_name": "Test Sales Report",
        "status": "success",
        "started_at": "2026-08-18T10:00:00Z",
        "completed_at": "2026-08-18T10:01:30Z",
        "duration_ms": 90000,
        "sync_mode": "copy",
        "source_type": "pbix",
        "results": [
            {
                "dropped_entities": [
                    {
                        "entity_name": "DateTableTemplate_123",
                        "entity_kind": "table",
                        "stage": "tmsl_to_osi",
                        "reason": "auto-generated power bi date table",
                        "by_design": True,
                    }
                ]
            }
        ],
    }

    project_cfg = """
project_name: test_project
source:
  type: pbix
targets:
  - type: snowflake
"""

    report_path = write_run_report(run_data, project_cfg)
    assert report_path is not None
    assert os.path.exists(report_path)

    content = Path(report_path).read_text(encoding="utf-8")
    assert "# Run Report — Test Sales Report" in content
    assert "DateTableTemplate_123" in content
    assert "An automatic calendar table Power BI creates" in content


def _synthetic_snapshot_blob():
    return {
        "datasets": [{"unique_name": "Fact", "columns": [{"unique_name": "Amount"}]}],
        "relationships": [],
        "metrics": [
            {"unique_name": "CleanMetric", "sync_enabled": True, "complexity_tier": 1, "advisory_categories": []},
            {"unique_name": "AiMetric", "sync_enabled": True, "complexity_tier": 5, "llm_self_reported_confidence": 0.9, "advisory_categories": []},
            {
                "unique_name": "NeedsReviewMetric",
                "sync_enabled": True,
                "complexity_tier": 3,
                "advisory_categories": [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK],
                "advisory_notes": ["some note"],
            },
            {"unique_name": "FailedMetric", "sync_enabled": False},
        ],
    }


def _run_with_synthetic_snapshot():
    return {
        "project_id": "proj-test-456",
        "run_id": "run-789",
        "project_name": "Synthetic Report",
        "status": "warning",
        "started_at": "2026-08-20T10:00:00Z",
        "completed_at": "2026-08-20T10:01:00Z",
        "duration_ms": 60000,
        "source_type": "pbix",
        "results": [
            {
                "model": "Synthetic Report",
                "summary": {"sml_snapshot_id": "snap-1", "target_type": "snowflake"},
                "dropped_entities": [
                    {
                        "entity_name": "DroppedMetric",
                        "entity_kind": "metric",
                        "stage": "ddl_deployment",
                        "reason": "Snowflake rejected this identifier",
                        "by_design": False,
                    },
                    {
                        "entity_name": "DateTableTemplate_123",
                        "entity_kind": "table",
                        "stage": "extraction",
                        "reason": "auto-generated power bi date table",
                        "by_design": True,
                    },
                ],
            }
        ],
    }


def _patch_snapshot(monkeypatch):
    fake_snapshot = SimpleNamespace(sml_blob=_synthetic_snapshot_blob())
    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.get_snapshot",
        lambda self, snapshot_id: fake_snapshot,
    )


def test_classify_metrics_carves_out_needs_review_regardless_of_tier(monkeypatch):
    """The core new behavior: a metric carrying a review-required advisory
    category goes to its own bucket, not blended into Standard/AI-assisted
    with just a message tacked on -- independent of complexity_tier."""
    _patch_snapshot(monkeypatch)
    data = build_run_report_data(_run_with_synthetic_snapshot(), "source:\n  type: pbix\n")

    assert data["counts"]["standard"] == 1
    assert data["counts"]["ai_assisted"] == 1
    assert data["counts"]["needs_review"] == 1
    assert data["counts"]["dropped"] == 1
    assert data["counts"]["excluded_by_design"] == 1
    assert data["counts"]["converted_total"] == 3  # standard + ai_assisted + needs_review

    names_by_section = {
        key: {entry["name"] for entry in entries}
        for key, entries in data["sections"].items()
        if key not in ("dropped", "excluded_by_design")
    }
    assert names_by_section["standard"] == {"CleanMetric"}
    assert names_by_section["ai_assisted"] == {"AiMetric"}
    assert names_by_section["needs_review"] == {"NeedsReviewMetric"}

    needs_review_entry = data["sections"]["needs_review"][0]
    assert needs_review_entry["advisory_msgs"], "expected a plain-language message for the needs_review entry"


def test_build_run_report_data_dropped_section_shape(monkeypatch):
    _patch_snapshot(monkeypatch)
    data = build_run_report_data(_run_with_synthetic_snapshot(), "source:\n  type: pbix\n")

    dropped = data["sections"]["dropped"]
    assert len(dropped) == 1
    assert dropped[0]["stage"] == "ddl_deployment"
    assert dropped[0]["items"][0]["name"] == "DroppedMetric"
    assert dropped[0]["items"][0]["reason"]


def test_by_design_drops_never_appear_in_the_dropped_section(monkeypatch):
    """The bug this closes: a by_design=True drop (e.g. an auto-generated
    Power BI date table) showing up inside "Not Included"/"what we
    couldn't include" reads as a real problem even though the item was
    marked "(expected — not an error)" -- it must live in its own,
    separately-counted section instead."""
    _patch_snapshot(monkeypatch)
    data = build_run_report_data(_run_with_synthetic_snapshot(), "source:\n  type: pbix\n")

    dropped_names = {
        item["name"] for section in data["sections"]["dropped"] for item in section["items"]
    }
    assert "DateTableTemplate_123" not in dropped_names
    assert dropped_names == {"DroppedMetric"}

    excluded = data["sections"]["excluded_by_design"]
    assert len(excluded) == 1
    assert excluded[0]["stage"] == "extraction"
    assert excluded[0]["items"][0]["name"] == "DateTableTemplate_123"


def test_markdown_separates_excluded_by_design_from_real_issues(monkeypatch):
    _patch_snapshot(monkeypatch)
    run = _run_with_synthetic_snapshot()
    markdown = generate_run_report_markdown(run, "source:\n  type: pbix\n")

    assert "## No action needed" in markdown
    couldnt_include_section = markdown.split("## What we couldn't include, and why")[1].split("## No action needed")[0]
    assert "DateTableTemplate_123" not in couldnt_include_section
    assert "DroppedMetric" in couldnt_include_section
    excluded_section = markdown.split("## No action needed")[1]
    assert "DateTableTemplate_123" in excluded_section


def test_build_run_report_data_and_markdown_agree_on_counts(monkeypatch):
    """The two views are rendered from the same _gather_run_report_data()
    call -- their counts must never disagree."""
    _patch_snapshot(monkeypatch)
    run = _run_with_synthetic_snapshot()
    project_cfg = "source:\n  type: pbix\n"

    data = build_run_report_data(run, project_cfg)
    markdown = generate_run_report_markdown(run, project_cfg)

    assert f"{data['counts']['converted_total']} of {data['counts']['calculations']} calculations converted." in markdown
    assert "Needs review (1)" in markdown
    assert "NeedsReviewMetric" in markdown


def test_build_run_report_data_handles_crashed_run():
    run = {"project_id": "p", "run_id": "r", "error": "connection refused"}
    data = build_run_report_data(run, "source:\n  type: pbix\n")
    assert data["crashed"] is True
    assert data["error"] == "connection refused"
