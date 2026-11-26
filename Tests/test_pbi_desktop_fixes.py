"""Unit tests for Power BI Desktop fixes (File -> Open, unsaved new file, ontology review, ambiguous table binding)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from semabridge.api.services.pbix_source_validation import validate_pbi_desktop_source_config
from semabridge.api.services.ontology_review_service import read_source_models
from semabridge.domain.exceptions import ValidationError
from semabridge.sources.base import SourceConfigError, SourceNotFoundError, get_source_adapter
from semabridge.sources.pbi_desktop.discovery import DiscoveredDesktopInstance, discover_running_instances
from semabridge.sources.pbi_desktop.model_reader import read_pbi_desktop_models
from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.connectors.table_binding_resolver import LiveSchemaMetadata, TableBindingAmbiguityError
from semabridge.sml.models import SMLDataset, SMLModel


# ---------------------------------------------------------------------------
# 1. File -> Open & Recent History Path Detection
# ---------------------------------------------------------------------------


def test_discovery_file_open_matches_recent_history(tmp_path):
    # Setup mock workspace and fake recent file
    ws_data = tmp_path / "ws1" / "Data"
    ws_data.mkdir(parents=True)
    (ws_data / "msmdsrv.port.txt").write_bytes("51000\r\n".encode("utf-16-le"))
    (ws_data / "db.1.db.xml").write_text("<Database><Name>db-guid</Name></Database>")

    fake_pbix = tmp_path / "Regional Sales Sample.pbix"
    fake_pbix.write_bytes(b"PK\x03\x04")

    mock_processes = [
        {
            "ProcessId": 1000,
            "ParentProcessId": 100,
            "Name": "PBIDesktop.exe",
            "CommandLine": r'"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe"',
        },
        {
            "ProcessId": 2000,
            "ParentProcessId": 1000,
            "Name": "msmdsrv.exe",
            "CommandLine": f'msmdsrv.exe -s "{ws_data}"',
        },
    ]

    mock_titles = {
        1000: ["Regional Sales Sample - Power BI Desktop"],
    }

    mock_recent = [str(fake_pbix)]

    instances = discover_running_instances(
        verify_active=True,
        process_provider=lambda: mock_processes,
        window_titles_provider=lambda: mock_titles,
        recent_paths_provider=lambda: mock_recent,
        port_checker=lambda p: True,
    )

    assert len(instances) == 1
    inst = instances[0]
    assert inst.file_name == "Regional Sales Sample.pbix"
    assert inst.file_path == str(fake_pbix)
    assert inst.file_method == "desktop_recent_history"
    assert inst.is_unsaved_new_file is False


def test_discovery_ambiguous_recent_history_leaves_path_empty(tmp_path):
    # Setup mock workspace and two fake recent files with the same filename in different folders
    ws_data = tmp_path / "ws1" / "Data"
    ws_data.mkdir(parents=True)
    (ws_data / "msmdsrv.port.txt").write_bytes("51000\r\n".encode("utf-16-le"))
    (ws_data / "db.1.db.xml").write_text("<Database><Name>db-guid</Name></Database>")

    dir1 = tmp_path / "folder1"
    dir1.mkdir(parents=True)
    file1 = dir1 / "Sales.pbix"
    file1.write_bytes(b"PK\x03\x04")

    dir2 = tmp_path / "folder2"
    dir2.mkdir(parents=True)
    file2 = dir2 / "Sales.pbix"
    file2.write_bytes(b"PK\x03\x04")

    mock_processes = [
        {
            "ProcessId": 1000,
            "ParentProcessId": 100,
            "Name": "PBIDesktop.exe",
            "CommandLine": r'"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe"',
        },
        {
            "ProcessId": 2000,
            "ParentProcessId": 1000,
            "Name": "msmdsrv.exe",
            "CommandLine": f'msmdsrv.exe -s "{ws_data}"',
        },
    ]

    mock_titles = {
        1000: ["Sales - Power BI Desktop"],
    }

    mock_recent = [str(file1), str(file2)]

    instances = discover_running_instances(
        verify_active=True,
        process_provider=lambda: mock_processes,
        window_titles_provider=lambda: mock_titles,
        recent_paths_provider=lambda: mock_recent,
        port_checker=lambda p: True,
    )

    assert len(instances) == 1
    inst = instances[0]
    # Two history entries with the same file name in different folders -> no path chosen
    assert inst.file_name == "Sales.pbix"
    assert inst.file_path is None
    assert inst.file_method == "window_title"
    assert inst.is_unsaved_new_file is False


def test_discovery_unsaved_new_file_untitled(tmp_path):
    ws_data = tmp_path / "ws_new" / "Data"
    ws_data.mkdir(parents=True)
    (ws_data / "msmdsrv.port.txt").write_bytes("51001\r\n".encode("utf-16-le"))
    (ws_data / "db.1.db.xml").write_text("<Database><Name>db-new</Name></Database>")

    mock_processes = [
        {
            "ProcessId": 1001,
            "ParentProcessId": 100,
            "Name": "PBIDesktop.exe",
            "CommandLine": r'"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe"',
        },
        {
            "ProcessId": 2001,
            "ParentProcessId": 1001,
            "Name": "msmdsrv.exe",
            "CommandLine": f'msmdsrv.exe -s "{ws_data}"',
        },
    ]

    mock_titles = {
        1001: ["*Untitled - Power BI Desktop"],
    }

    instances = discover_running_instances(
        verify_active=True,
        process_provider=lambda: mock_processes,
        window_titles_provider=lambda: mock_titles,
        recent_paths_provider=lambda: [],
        port_checker=lambda p: True,
    )

    assert len(instances) == 1
    inst = instances[0]
    assert inst.file_name == "Unsaved new file"
    assert inst.file_path is None
    assert inst.is_unsaved_new_file is True
    assert inst.has_unsaved_changes is True


# ---------------------------------------------------------------------------
# 2. Validation Rejects Unsaved New File
# ---------------------------------------------------------------------------


def test_validation_rejects_unsaved_new_file(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")

    # Reject untitled / unsaved new file
    cfg_unsaved = {
        "type": "pbi_desktop",
        "models": [{"display_name": "Unsaved new file"}],
    }
    with pytest.raises(ValidationError) as exc:
        validate_pbi_desktop_source_config(cfg_unsaved)
    assert "Unsaved new file" in str(exc.value)

    cfg_untitled = {
        "type": "pbi_desktop",
        "models": [{"display_name": "Untitled.pbix"}],
    }
    with pytest.raises(ValidationError) as exc:
        validate_pbi_desktop_source_config(cfg_untitled)
    assert "Unsaved new file" in str(exc.value)

    # Valid config with display_name only (file_path None) passes
    cfg_valid = {
        "type": "pbi_desktop",
        "models": [{"display_name": "Regional Sales Sample"}],
    }
    validate_pbi_desktop_source_config(cfg_valid)


# ---------------------------------------------------------------------------
# 3. Adapter Resolution: Path, Display Name, and Ambiguity Check
# ---------------------------------------------------------------------------


def test_adapter_resolution_display_name_and_ambiguity(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")
    adapter = get_source_adapter("pbi_desktop")

    inst1 = DiscoveredDesktopInstance(
        port=50001,
        workspace_folder="ws1",
        workspace_dir="C:/ws1",
        msmdsrv_pid=1001,
        parent_pbidesktop_pid=2001,
        database_name="db1",
        file_name="Sales Model.pbix",
        file_path="C:/Files/Sales Model.pbix",
        file_method="command_line",
        window_title="Sales Model",
    )

    inst2 = DiscoveredDesktopInstance(
        port=50002,
        workspace_folder="ws2",
        workspace_dir="C:/ws2",
        msmdsrv_pid=1002,
        parent_pbidesktop_pid=2002,
        database_name="db2",
        file_name="Sales Model.pbix",
        file_path="C:/Other/Sales Model.pbix",
        file_method="command_line",
        window_title="Sales Model",
    )

    # 1. Single matching instance by display_name
    check = adapter.validate_and_resolve(
        [{"display_name": "Sales Model"}],
        instances_provider=lambda: [inst1],
    )
    assert len(check.identities) == 1
    assert check.identities[0].model_id == "localhost:50001/db1"
    assert check.identities[0].display_name == "Sales Model"

    # 2. Ambiguous running instances with same display_name -> error naming both
    with pytest.raises(SourceConfigError) as exc:
        adapter.validate_and_resolve(
            [{"display_name": "Sales Model"}],
            instances_provider=lambda: [inst1, inst2],
        )
    err_str = str(exc.value)
    assert "Multiple running Power BI Desktop instances match 'Sales Model'" in err_str
    assert "localhost:50001" in err_str
    assert "localhost:50002" in err_str

    # 3. Disambiguated by saved file_path
    check_disambiguated = adapter.validate_and_resolve(
        [{"file_path": "C:/Other/Sales Model.pbix", "display_name": "Sales Model"}],
        instances_provider=lambda: [inst1, inst2],
    )
    assert len(check_disambiguated.identities) == 1
    assert check_disambiguated.identities[0].model_id == "localhost:50002/db2"


# ---------------------------------------------------------------------------
# 4. Ontology Review Before Dry Run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ontology_review_before_dry_run_pbi_desktop(monkeypatch):
    monkeypatch.setenv("pbi_desktop_source", "true")

    mock_tmsl = {
        "model": {
            "name": "Regional Sales Sample",
            "tables": [
                {
                    "name": "Sales",
                    "columns": [{"name": "Amount", "dataType": "decimal"}],
                    "measures": [{"name": "Total Sales", "expression": "SUM(Sales[Amount])"}],
                }
            ],
            "relationships": [],
        }
    }

    mock_inst = DiscoveredDesktopInstance(
        port=56888,
        workspace_folder="ws_reg",
        workspace_dir="C:/ws_reg",
        msmdsrv_pid=30836,
        parent_pbidesktop_pid=23840,
        database_name="reg-guid",
        file_name="Regional Sales Sample.pbix",
        file_path="C:/pbix/Regional Sales Sample.pbix",
        file_method="desktop_recent_history",
        window_title="Regional Sales Sample",
    )

    source_cfg = {
        "type": "pbi_desktop",
        "models": [{"display_name": "Regional Sales Sample", "file_path": "C:/pbix/Regional Sales Sample.pbix"}],
    }

    with patch("semabridge.sources.pbi_desktop.adapter.discover_running_instances", return_value=[mock_inst]), \
         patch("semabridge.sources.pbi_desktop.adapter.read_tmsl_from_engine", return_value=mock_tmsl):
        models, errors = await read_source_models(source_cfg, ["Regional Sales Sample"], project_id="preview")
        assert len(errors) == 0
        assert len(models) == 1
        assert models[0].label == "Regional Sales Sample"


# ---------------------------------------------------------------------------
# 5. Ambiguous Table Binding Returns Blocked Plan
# ---------------------------------------------------------------------------


def test_plan_source_tables_ambiguous_binding_returns_blocked():
    mgr = SnowflakeSchemaManager.__new__(SnowflakeSchemaManager)
    mgr._target_namespace = lambda: ("TEST_DB", "TEST_SCHEMA")
    mgr._safe_table_name = lambda name: name.upper()
    mgr._physical_model_column_names = lambda ds: ["DATE", "YEAR", "MONTH"]
    mgr._schema_fqn = lambda: "TEST_DB.TEST_SCHEMA"
    mgr._execute_sql = lambda cursor, sql, context=None: None
    mgr.refresh_schema_cache = lambda: None
    mgr._fetch_schema_metadata = lambda cursor: None
    mgr._live_schema_metadata = LiveSchemaMetadata()

    # Mock cursor fetchall returning existing tables
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [("TEST_DB", "SALES_DATE"), ("TEST_DB", "MARKETING_DATE")]

    sml = SMLModel(
        unique_name="model1",
        label="MyModel",
        datasets=[SMLDataset(unique_name="Date")],
    )

    with patch("semabridge.connectors.table_binding_resolver.resolve_dataset_to_physical_table",
               side_effect=TableBindingAmbiguityError("Date", ["SALES_DATE", "MARKETING_DATE"])):
        plan = mgr.plan_source_tables(mock_cursor, sml)
        assert len(plan) == 1
        entry = plan[0]
        assert entry["action"] == "blocked"
        assert entry["dataset"] == "Date"
        assert "SALES_DATE" in entry["table"]
        assert "MARKETING_DATE" in entry["table"]
        assert "Ambiguous table binding" in entry["message"]
