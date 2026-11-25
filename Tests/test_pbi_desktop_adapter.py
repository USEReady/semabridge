"""Unit tests for PbiDesktopAdapter conforming to SemanticSourceAdapter."""

from __future__ import annotations

import json
from pathlib import Path
import zipfile
import pytest

from semabridge.sources.base import (
    ModelIdentity,
    SourceError,
    SourceNotFoundError,
    SourceUnsupportedError,
    get_source_adapter,
)
from semabridge.sources.pbi_desktop.adapter import PbiDesktopAdapter
from semabridge.sources.pbi_desktop.discovery import DiscoveredDesktopInstance


@pytest.fixture
def mock_running_instance():
    return DiscoveredDesktopInstance(
        port=51234,
        workspace_folder="AnalysisServicesWorkspace_mock",
        workspace_dir="C:/mock/ws",
        msmdsrv_pid=1234,
        parent_pbidesktop_pid=5678,
        database_name="db_guid_1234",
        file_name="SalesModel.pbix",
        file_path="C:/mock/SalesModel.pbix",
        file_method="command_line",
        window_title="SalesModel - Power BI Desktop",
        pbidesktop_exe="C:/mock/PBIDesktop.exe",
        has_unsaved_changes=False,
        is_live_connected=False,
    )


def test_feature_flag_disabled_by_default(monkeypatch):
    monkeypatch.delenv("pbi_desktop_source", raising=False)
    monkeypatch.delenv("PBI_DESKTOP_SOURCE", raising=False)
    monkeypatch.delenv("SEMABRIDGE_PBI_DESKTOP_SOURCE", raising=False)

    adapter = PbiDesktopAdapter()
    with pytest.raises(SourceUnsupportedError, match="feature flag 'pbi_desktop_source' is off"):
        adapter.validate_and_resolve(["SalesModel.pbix"])


def test_adapter_registration():
    adapter = get_source_adapter("pbi_desktop")
    assert isinstance(adapter, PbiDesktopAdapter)
    assert adapter.source_type == "pbi_desktop"
    assert adapter.capabilities["live_model_extraction"] is True
    assert adapter.capabilities["local_only"] is True


def test_validate_and_resolve_by_file_path(mock_running_instance, monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "1")
    adapter = PbiDesktopAdapter()

    check = adapter.validate_and_resolve(
        ["SalesModel.pbix"],
        instances_provider=lambda: [mock_running_instance],
    )
    assert len(check.identities) == 1
    ident = check.identities[0]
    assert ident.source_type == "pbi_desktop"
    assert ident.model_id == "localhost:51234/db_guid_1234"
    assert ident.display_name == "SalesModel"


def test_validate_and_resolve_file_closed_raises_not_found(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "1")
    adapter = PbiDesktopAdapter()

    with pytest.raises(SourceNotFoundError, match="That file isn't open anymore"):
        adapter.validate_and_resolve(
            ["ClosedModel.pbix"],
            instances_provider=lambda: [],
        )


def test_validate_and_resolve_by_server_database(mock_running_instance, monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "1")
    adapter = PbiDesktopAdapter()

    # Valid local server + database
    check = adapter.validate_and_resolve(
        [{"server": "localhost:51234", "database": "db_guid_1234"}],
        instances_provider=lambda: [mock_running_instance],
    )
    assert len(check.identities) == 1
    assert check.identities[0].model_id == "localhost:51234/db_guid_1234"


def test_validate_and_resolve_rejects_non_local_server(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "1")
    adapter = PbiDesktopAdapter()

    with pytest.raises(SourceError, match="Non-local server rejected"):
        adapter.validate_and_resolve(
            [{"server": "remote-host:51234", "database": "db_guid_1234"}],
            instances_provider=lambda: [],
        )


def test_extract_model_and_stats(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "1")
    adapter = PbiDesktopAdapter()

    ident = ModelIdentity(
        source_type="pbi_desktop",
        model_id="localhost:51234/db_1",
        display_name="TestModel",
    )

    mock_model = {
        "model": {
            "name": "TestModel",
            "tables": [
                {"name": "Customers", "columns": []},
                {"name": "Orders", "columns": []},
            ],
            "relationships": [],
        }
    }

    with monkeypatch.context() as m:
        m.setattr("semabridge.sources.pbi_desktop.adapter.read_tmsl_from_engine", lambda **k: mock_model)
        m.setattr(
            adapter,
            "execute_dax",
            lambda target, query, **k: {
                "rows": [{"RowCount": 150 if "Customers" in query else 400}],
                "columns": ["RowCount"],
            },
        )

        tmsl = adapter.extract_model(ident)
        assert tmsl == mock_model

        stats = adapter.extract_stats(ident, tmsl=tmsl)
        assert stats == {"Customers": 150, "Orders": 400}


def test_extract_report_layer_zip_only(tmp_path, monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "1")
    adapter = PbiDesktopAdapter()

    # Create dummy pbix zip with Report/Layout
    pbix_zip = tmp_path / "DummyReport.pbix"
    layout_content = {
        "id": 1,
        "sections": [
            {
                "name": "Section1",
                "displayName": "Overview",
                "visualContainers": [
                    {
                        "config": json.dumps({
                            "name": "vis1",
                            "singleVisual": {
                                "visualType": "barChart",
                                "projections": {
                                    "Values": [{"queryRef": "Sum(Sales.Amount)"}],
                                },
                            },
                        }),
                    }
                ],
            }
        ],
    }

    with zipfile.ZipFile(pbix_zip, "w") as zf:
        zf.writestr("Report/Layout", json.dumps(layout_content).encode("utf-16-le"))

    ident = ModelIdentity(
        source_type="pbi_desktop",
        model_id="localhost:51234/db_1",
        display_name="DummyReport",
    )
    adapter._resolved_file_paths[ident.model_id] = str(pbix_zip)

    mock_tmsl = {
        "model": {
            "tables": [
                {"name": "Sales", "columns": [{"name": "Amount"}]},
            ]
        }
    }

    report_layer = adapter.extract_report_layer(ident, tmsl=mock_tmsl)
    assert report_layer.reports == ["DummyReport.pbix"]
    assert len(report_layer.warnings) == 0


def test_fingerprint(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "1")
    adapter = PbiDesktopAdapter()

    fp1 = adapter.fingerprint({"model": {"name": "M1"}})
    fp2 = adapter.fingerprint({"model": {"name": "M1"}})
    fp3 = adapter.fingerprint({"model": {"name": "M2"}})

    assert fp1 == fp2
    assert fp1 != fp3
