"""A model with no tables (thin report / empty model) is SKIPPED, not failed.

Covers the typed error (NoSourceTablesError keeps its type through step 4),
the thin-report detection for every Connections shape Power BI writes, the
dry-run job aggregate, and how the sync and retry treat skipped models.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from semabridge.api.services.dry_run_job_service import _aggregate_job_status
from semabridge.core.engine.exceptions import (
    ExtractionError,
    NoSourceTablesError,
    is_no_source_tables_summary,
)
from semabridge.core.engine.extraction.base import _step4_extract
from semabridge.core.engine.extraction.pbix import _empty_pbix_model_message

REPO = Path(__file__).resolve().parents[1]


def _pbix(tmp_path: Path, connections: dict | None) -> str:
    path = tmp_path / "Thin.pbix"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Version", "1")
        if connections is not None:
            z.writestr("Connections", json.dumps(connections))
    return str(path)


@pytest.mark.parametrize("connections, expected", [
    ({"RemoteArtifacts": [{"DatasetId": "ds-1"}]}, "ds-1"),
    ({"Connections": [{"PbiModelDatabaseName": "db-2"}]}, "db-2"),
    ({"Connections": [{"PbiServiceModelId": 77}]}, "77"),
    ({"Connections": [{"ConnectionString": "Data Source=pbiazure://x;Initial Catalog=sales-model"}]}, "sales-model"),
])
def test_thin_report_is_live_connection_for_every_connections_shape(tmp_path, connections, expected):
    message, reason = _empty_pbix_model_message(_pbix(tmp_path, connections))
    assert reason == "LIVE_CONNECTION"
    assert expected in message


def test_empty_model_without_connection_is_no_tables(tmp_path):
    message, reason = _empty_pbix_model_message(_pbix(tmp_path, None))
    assert reason == "NO_TABLES"
    assert "contains no tables" in message


def test_real_thin_report_example():
    example = REPO / "Tests" / "fixtures" / "pbix" / "thin_report_live_connection.pbix"
    if not example.exists():
        if os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true":
            pytest.fail("example thin report not present in CI")
        pytest.skip("example thin report not present")
    _, reason = _empty_pbix_model_message(str(example))
    assert reason == "LIVE_CONNECTION"


def test_step4_keeps_the_no_tables_type():
    def boom(_ctx, _path):
        raise NoSourceTablesError("no tables", reason_code="LIVE_CONNECTION")

    fake = SimpleNamespace(_current_step=0, _extract_pbix=boom, _record_step=lambda *a, **k: None)
    with pytest.raises(NoSourceTablesError) as info:
        _step4_extract(fake, SimpleNamespace(source_type="pbix"), None, None, "x.pbix")
    assert info.value.reason_code == "LIVE_CONNECTION"
    assert isinstance(info.value, ExtractionError)  # old callers still see an extraction error


def test_summary_classification():
    no_tables = {"errors": [{"error_type": "NoSourceTablesError", "message": "no tables"}]}
    mixed = {"errors": [{"error_type": "NoSourceTablesError"}, {"error_type": "DeploymentError"}]}
    assert is_no_source_tables_summary(no_tables)
    assert not is_no_source_tables_summary(mixed)       # a real failure is never hidden
    assert not is_no_source_tables_summary({"errors": []})
    assert not is_no_source_tables_summary(None)


@pytest.mark.parametrize("statuses, expected", [
    (["success", "skipped"], "success"),
    (["skipped", "skipped"], "failed"),        # nothing deployable
    (["failed", "skipped"], "failed"),
    (["success", "failed", "skipped"], "partial"),
    (["success", "running"], "running"),
])
def test_dry_run_job_aggregate(statuses, expected):
    assert _aggregate_job_status(statuses) == expected
