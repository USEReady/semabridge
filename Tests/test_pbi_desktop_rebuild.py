"""Comprehensive tests for Power BI Desktop Rebuild (API, validation, resolution, report, isolation)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.api.services.pbix_source_validation import (
    get_supported_source_types,
    require_source_type,
    validate_pbi_desktop_source_config,
)
from semabridge.api.services.run_report_service import _describe_source, _describe_source_for_result
from semabridge.api.services.schedule_service import save_project_schedule_compat
from semabridge.api.services.sync_execution_service import _build_sync_jobs
from semabridge.domain.exceptions import ValidationError
from semabridge.sources.base import SourceNotFoundError, get_source_adapter
from semabridge.sources.pbi_desktop.adapter import PbiDesktopAdapter
from semabridge.sources.pbi_desktop.discovery import DiscoveredDesktopInstance


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Inspect Endpoint Tests
# ---------------------------------------------------------------------------


def test_inspect_endpoint_disabled(client, monkeypatch):
    monkeypatch.delenv("pbi_desktop_source", raising=False)
    monkeypatch.delenv("PBI_DESKTOP_SOURCE", raising=False)
    monkeypatch.delenv("SEMABRIDGE_PBI_DESKTOP_SOURCE", raising=False)

    res = client.get("/api/sources/pbi-desktop/inspect", params={"file_path": "C:/path/test.pbix"})
    assert res.status_code == 403
    assert "disabled" in res.json()["detail"].lower()


def test_inspect_endpoint_not_open(client, monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    with patch("semabridge.api.routers.pbi_desktop_router.discover_running_instances", return_value=[]):
        res = client.get("/api/sources/pbi-desktop/inspect", params={"file_path": "C:/path/SalesModel.pbix"})
        assert res.status_code == 404
        assert res.json()["detail"] == "Open SalesModel.pbix in Power BI Desktop and try again"


def test_inspect_endpoint_success(client, monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    mock_instance = DiscoveredDesktopInstance(
        port=51234,
        workspace_folder="AnalysisServicesWorkspace_abc",
        workspace_dir="C:/mock/ws",
        msmdsrv_pid=1111,
        parent_pbidesktop_pid=2222,
        database_name="guid_123",
        file_name="SalesModel.pbix",
        file_path="C:/mock/SalesModel.pbix",
        file_method="command_line",
        window_title="SalesModel - Power BI Desktop",
        pbidesktop_exe="C:/mock/PBIDesktop.exe",
        has_unsaved_changes=True,
        is_live_connected=False,
    )

    mock_tmsl = {
        "model": {
            "tables": [
                {"name": "Orders", "measures": [{"name": "Total Sales"}, {"name": "Total Profit"}]},
                {"name": "Customers", "measures": []},
                {"name": "LocalDateTable_abc", "measures": []},  # system date table excluded
            ],
            "relationships": [{"name": "Rel_Orders_Customers"}],
        }
    }

    with patch("semabridge.api.routers.pbi_desktop_router.discover_running_instances", return_value=[mock_instance]), \
         patch("semabridge.api.routers.pbi_desktop_router.read_tmsl_from_engine", return_value=mock_tmsl):
        res = client.get("/api/sources/pbi-desktop/inspect", params={"file_path": "C:/mock/SalesModel.pbix"})
        assert res.status_code == 200
        data = res.json()
        assert data["file_name"] == "SalesModel.pbix"
        assert data["tables_count"] == 2
        assert data["measures_count"] == 2
        assert data["relationships_count"] == 1
        assert data["has_unsaved_changes"] is True
        assert data["is_live_connected"] is False


# ---------------------------------------------------------------------------
# 2. Project Create / Save Validation Tests
# ---------------------------------------------------------------------------


def test_validation_missing_models(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    with pytest.raises(ValidationError, match="At least one Power BI Desktop model must be selected"):
        validate_pbi_desktop_source_config({"type": "pbi_desktop", "models": []})


def test_validation_rejects_non_pbix(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    with pytest.raises(ValidationError, match="does not look like a .pbix file path"):
        validate_pbi_desktop_source_config({
            "type": "pbi_desktop",
            "models": [{"file_path": "C:/path/report.xlsx"}],
        })


def test_validation_rejects_relative_path(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    with pytest.raises(ValidationError, match="is not an absolute path"):
        validate_pbi_desktop_source_config({
            "type": "pbi_desktop",
            "models": [{"file_path": "relative/path/report.pbix"}],
        })


def test_validation_rejects_duplicates(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    with pytest.raises(ValidationError, match="Duplicate model selected"):
        validate_pbi_desktop_source_config({
            "type": "pbi_desktop",
            "models": [
                {"file_path": "C:/path/report.pbix"},
                {"file_path": "C:/path/report.pbix"},
            ],
        })


def test_validation_rejects_dynamic_session_fields(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    with pytest.raises(ValidationError, match="Dynamic session field 'port' must not be saved"):
        validate_pbi_desktop_source_config({
            "type": "pbi_desktop",
            "port": 51234,
            "models": [{"file_path": "C:/path/report.pbix"}],
        })

    with pytest.raises(ValidationError, match="Dynamic session field 'database' must not be saved"):
        validate_pbi_desktop_source_config({
            "type": "pbi_desktop",
            "database": "guid_123",
            "models": [{"file_path": "C:/path/report.pbix"}],
        })


def test_validation_passes_valid_config(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    # Must not raise
    validate_pbi_desktop_source_config({
        "type": "pbi_desktop",
        "models": [
            {"file_path": "C:/data/sales.pbix", "display_name": "Sales"},
            {"file_path": "C:/data/marketing.pbix", "display_name": "Marketing"},
        ],
    })


# ---------------------------------------------------------------------------
# 3. Runtime Resolution (Open vs Closed Files)
# ---------------------------------------------------------------------------


def test_runtime_resolution_open_file(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    mock_instance = DiscoveredDesktopInstance(
        port=51234,
        workspace_folder="AnalysisServicesWorkspace_abc",
        workspace_dir="C:/mock/ws",
        msmdsrv_pid=1111,
        parent_pbidesktop_pid=2222,
        database_name="guid_123",
        file_name="Sales.pbix",
        file_path="C:/data/sales.pbix",
        file_method="command_line",
        window_title="Sales - Power BI Desktop",
        pbidesktop_exe="C:/mock/PBIDesktop.exe",
        has_unsaved_changes=False,
        is_live_connected=False,
    )

    with patch("semabridge.sources.pbi_desktop.adapter.discover_running_instances", return_value=[mock_instance]):
        config = {
            "source": {
                "type": "pbi_desktop",
                "models": [{"file_path": "C:/data/sales.pbix", "display_name": "Sales"}],
            },
            "target": {"type": "snowflake"},
        }
        sync_jobs, source_type, target_type, _, _ = _build_sync_jobs(config)
        assert source_type == "pbi_desktop"
        assert len(sync_jobs) == 1
        assert sync_jobs[0]["dataset_id"] == "localhost:51234/guid_123"
        assert sync_jobs[0]["model_label"] == "Sales"


def test_runtime_resolution_closed_file_fails_with_exact_message(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    with patch("semabridge.sources.pbi_desktop.adapter.discover_running_instances", return_value=[]):
        config = {
            "source": {
                "type": "pbi_desktop",
                "models": [{"file_path": "C:/data/closed_model.pbix", "display_name": "Closed Model"}],
            },
            "target": {"type": "snowflake"},
        }
        with pytest.raises(ValidationError, match="Open closed_model.pbix in Power BI Desktop and try again"):
            _build_sync_jobs(config)


# ---------------------------------------------------------------------------
# 4. Run Report Labels and Credential Origin
# ---------------------------------------------------------------------------


def test_credential_origin_local_engine(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    adapter = PbiDesktopAdapter()
    creds = adapter.resolve_credentials()
    assert creds.origin == "local engine"


def test_run_report_source_description(monkeypatch):
    project_cfg = """
source:
  type: pbi_desktop
  models:
    - file_path: C:/data/Finance.pbix
      display_name: Finance
"""
    run_dict = {
        "source_type": "pbi_desktop",
        "results": [{"model": "Finance", "has_unsaved_changes": True}],
    }
    source_kind, file_labels = _describe_source(project_cfg, run_dict)
    assert source_kind == "Power BI Desktop"
    assert len(file_labels) == 1
    assert "Finance.pbix (includes unsaved changes)" in file_labels[0] or "Finance (includes unsaved changes)" in file_labels[0]

    # Per-model result description
    model_result = {"file_name": "Finance.pbix", "has_unsaved_changes": True}
    res_kind, res_labels = _describe_source_for_result(model_result, project_cfg, run_dict)
    assert res_kind == "Power BI Desktop"
    assert res_labels == ["Finance.pbix (includes unsaved changes)"]


# ---------------------------------------------------------------------------
# 5. Schedule Warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_warning_for_pbi_desktop():
    fake_project = {
        "id": "proj-desktop-1",
        "name": "Desktop Project",
        "source": {"type": "pbi_desktop", "models": [{"file_path": "C:/data/test.pbix"}]},
    }
    with patch("semabridge.api.services.project_shared._compat_get_project", return_value=fake_project), \
         patch("semabridge.api.services.schedule_service.scheduler_service.save_project_schedule", return_value={"schedule_type": "cron", "enabled": True}):
        res = await save_project_schedule_compat("proj-desktop-1", {"schedule_type": "cron", "cron": "0 2 * * *"})
        assert "warning" in res
        assert res["warning"] == "Scheduled runs require the file to be open in Power BI Desktop."


# ---------------------------------------------------------------------------
# 6. Source Isolation (PBIX, Fabric, Snowflake Unaffected)
# ---------------------------------------------------------------------------


def test_sources_isolation_when_flag_off(monkeypatch):
    monkeypatch.delenv("pbi_desktop_source", raising=False)
    monkeypatch.delenv("PBI_DESKTOP_SOURCE", raising=False)
    monkeypatch.delenv("SEMABRIDGE_PBI_DESKTOP_SOURCE", raising=False)

    allowed = get_supported_source_types()
    assert "pbi_desktop" not in allowed
    assert "pbix" in allowed
    assert "fabric" in allowed
    assert "snowflake" in allowed

    # Attempting to save pbi_desktop when flag is off fails
    with pytest.raises(ValidationError):
        require_source_type({"type": "pbi_desktop"})


def test_sources_isolation_pbix_and_fabric_unaffected(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")

    # PBIX source build sync jobs works as before
    pbix_config = {
        "source": {"type": "pbix", "pbix_path": "C:/fake/path.pbix"},
        "target": {"type": "snowflake"},
    }
    jobs, stype, _, _, _ = _build_sync_jobs(pbix_config)
    assert stype == "pbix"
    assert len(jobs) == 1

    # Snowflake source build sync jobs works as before
    snowflake_config = {
        "source": {"type": "snowflake", "models": ["MY_VIEW"]},
        "target": {"type": "snowflake"},
    }
    jobs, stype, _, _, _ = _build_sync_jobs(snowflake_config)
    assert stype == "snowflake"
    assert len(jobs) == 1
