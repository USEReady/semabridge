import os
from pathlib import Path
from semabridge.api.services.run_report_service import (
    generate_run_report_markdown,
    write_run_report,
    humanize_drop_reason,
)

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
