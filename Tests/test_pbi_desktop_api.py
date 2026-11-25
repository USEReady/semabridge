"""Unit tests for Power BI Desktop sources API endpoints."""

from __future__ import annotations

from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest

from semabridge.api.main import app
from semabridge.sources.pbi_desktop.discovery import DiscoveredDesktopInstance


@pytest.fixture
def client():
    return TestClient(app)


def test_get_desktop_instances_mocked(client):
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
        has_unsaved_changes=False,
        is_live_connected=False,
    )

    with patch(
        "semabridge.api.routers.pbi_desktop_router.discover_running_instances",
        return_value=[mock_instance],
    ):
        res = client.get("/api/sources/pbi-desktop/instances")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert len(data["instances"]) == 1
        inst = data["instances"][0]
        assert inst["port"] == 51234
        assert inst["file_name"] == "SalesModel.pbix"
        assert inst["database_name"] == "guid_123"


def test_get_desktop_instances_empty(client):
    with patch(
        "semabridge.api.routers.pbi_desktop_router.discover_running_instances",
        return_value=[],
    ):
        res = client.get("/api/sources/pbi-desktop/instances")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["instances"] == []
