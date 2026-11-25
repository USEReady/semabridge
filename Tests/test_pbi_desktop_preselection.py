"""Tests for backend pre-selection validation for Power BI Desktop sources."""

from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.sources.base import SourceError, SourceNotFoundError
from semabridge.sources.pbi_desktop.adapter import PbiDesktopAdapter
from semabridge.sources.pbi_desktop.discovery import DiscoveredDesktopInstance
from semabridge.sources.pbi_desktop.preselection import (
    parse_desktop_server,
    validate_preselected_desktop_source,
)


@pytest.fixture
def mock_instances():
    return [
        DiscoveredDesktopInstance(
            port=51234,
            workspace_folder="mock_ws",
            workspace_dir="C:/AppData/Local/Microsoft/Power BI Desktop/AnalysisServicesWorkspaces/mock_ws",
            msmdsrv_pid=1001,
            parent_pbidesktop_pid=2001,
            database_name="42f02900-5bfa-4740-9d8a-8be865b20612",
            file_name="Sales Model.pbix",
            file_path="C:/Projects/Sales Model.pbix",
            file_method="command_line",
            window_title="Sales Model - Power BI Desktop",
            pbidesktop_exe="C:/Program Files/Microsoft Power BI Desktop/bin/PBIDesktop.exe",
            has_unsaved_changes=False,
            is_live_connected=False,
        ),
        DiscoveredDesktopInstance(
            port=51235,
            workspace_folder="mock_ws2",
            workspace_dir="C:/AppData/Local/Microsoft/Power BI Desktop/AnalysisServicesWorkspaces/mock_ws2",
            msmdsrv_pid=1002,
            parent_pbidesktop_pid=2002,
            database_name="competitive-analysis-db-guid",
            file_name="Competitive Marketing Analysis.pbix",
            file_path="C:/Projects/Competitive Marketing Analysis.pbix",
            file_method="command_line",
            window_title="Competitive Marketing Analysis - Power BI Desktop",
            pbidesktop_exe="C:/Program Files/Microsoft Power BI Desktop/bin/PBIDesktop.exe",
            has_unsaved_changes=True,
            is_live_connected=False,
        ),
    ]


def test_parse_desktop_server():
    assert parse_desktop_server("localhost:51234") == ("localhost", 51234)
    assert parse_desktop_server("127.0.0.1:51234") == ("127.0.0.1", 51234)
    assert parse_desktop_server("localhost") == ("localhost", None)
    assert parse_desktop_server("[::1]:51234") == ("::1", 51234)
    assert parse_desktop_server("http://localhost:51234") == ("localhost", 51234)

    with pytest.raises(SourceError):
        parse_desktop_server("")


def test_valid_preselection(mock_instances):
    matched = validate_preselected_desktop_source(
        server="localhost:51234",
        database="42f02900-5bfa-4740-9d8a-8be865b20612",
        instances_provider=lambda: mock_instances,
    )
    assert matched is not None
    assert matched.port == 51234
    assert matched.file_name == "Sales Model.pbix"

    # Also works with 127.0.0.1 and case-insensitive database GUID
    matched_127 = validate_preselected_desktop_source(
        server="127.0.0.1:51235",
        database="COMPETITIVE-ANALYSIS-DB-GUID",
        instances_provider=lambda: mock_instances,
    )
    assert matched_127.port == 51235
    assert matched_127.file_name == "Competitive Marketing Analysis.pbix"


def test_closed_file_rejected(mock_instances):
    # Port doesn't match or database was closed
    with pytest.raises(SourceNotFoundError) as exc_info:
        validate_preselected_desktop_source(
            server="localhost:51234",
            database="closed-db-guid-9999",
            instances_provider=lambda: mock_instances,
        )
    assert "That file isn't open anymore" in str(exc_info.value)

    # Completely empty instances
    with pytest.raises(SourceNotFoundError) as exc_info2:
        validate_preselected_desktop_source(
            server="localhost:51234",
            database="42f02900-5bfa-4740-9d8a-8be865b20612",
            instances_provider=lambda: [],
        )
    assert "That file isn't open anymore" in str(exc_info2.value)


def test_non_local_server_rejected(mock_instances):
    # Hostnames, remote IPs, or cloud domains must be rejected
    non_local_servers = [
        "192.168.1.100:51234",
        "remote.corporate.net:51234",
        "analysis.windows.net:51234",
        "10.0.0.5:51234",
        "8.8.8.8:51234",
    ]

    for srv in non_local_servers:
        with pytest.raises(SourceError) as exc_info:
            validate_preselected_desktop_source(
                server=srv,
                database="42f02900-5bfa-4740-9d8a-8be865b20612",
                instances_provider=lambda: mock_instances,
            )
        assert "Non-local server rejected" in str(exc_info.value)


def test_adapter_validate_and_resolve_preselection(mock_instances):
    adapter = PbiDesktopAdapter()

    with patch("semabridge.sources.pbi_desktop.adapter.is_pbi_desktop_source_enabled", return_value=True):
        # 1. Valid preselection
        check = adapter.validate_and_resolve(
            refs=[{"server": "localhost:51234", "database": "42f02900-5bfa-4740-9d8a-8be865b20612"}],
            instances_provider=lambda: mock_instances,
        )
        assert len(check.identities) == 1
        assert check.identities[0].display_name == "Sales Model"

        # 2. Closed file
        with pytest.raises(SourceNotFoundError) as exc_closed:
            adapter.validate_and_resolve(
                refs=[{"server": "localhost:51234", "database": "closed-db"}],
                instances_provider=lambda: mock_instances,
            )
        assert "That file isn't open anymore" in str(exc_closed.value)

        # 3. Non-local server rejected
        with pytest.raises(SourceError) as exc_nonlocal:
            adapter.validate_and_resolve(
                refs=[{"server": "remote-server:51234", "database": "42f02900-5bfa-4740-9d8a-8be865b20612"}],
                instances_provider=lambda: mock_instances,
            )
        assert "Non-local server rejected" in str(exc_nonlocal.value)


def test_api_validate_preselection_endpoint(mock_instances):
    client = TestClient(app)

    with patch("semabridge.sources.pbi_desktop.preselection.discover_running_instances", return_value=mock_instances):
        # 1. Valid preselection
        res_valid = client.get(
            "/api/sources/pbi-desktop/validate-preselection",
            params={"server": "localhost:51234", "database": "42f02900-5bfa-4740-9d8a-8be865b20612"},
        )
        assert res_valid.status_code == 200
        data = res_valid.json()
        assert data["valid"] is True
        assert data["instance"]["file_name"] == "Sales Model.pbix"

        # 2. Closed file
        res_closed = client.get(
            "/api/sources/pbi-desktop/validate-preselection",
            params={"server": "localhost:51234", "database": "old-closed-db"},
        )
        assert res_closed.status_code == 200
        data_closed = res_closed.json()
        assert data_closed["valid"] is False
        assert data_closed["error"] == "That file isn't open anymore"
        assert data_closed["show_picker"] is True

        # 3. Non-local server rejected -> 400
        res_nonlocal = client.get(
            "/api/sources/pbi-desktop/validate-preselection",
            params={"server": "remote.host:51234", "database": "42f02900-5bfa-4740-9d8a-8be865b20612"},
        )
        assert res_nonlocal.status_code == 400
        assert "Non-local server rejected" in res_nonlocal.json()["detail"]
