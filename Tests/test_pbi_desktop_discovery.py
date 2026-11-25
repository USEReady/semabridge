"""Unit tests for Power BI Desktop discovery module."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from semabridge.sources.pbi_desktop.discovery import (
    DiscoveredDesktopInstance,
    discover_running_instances,
    extract_database_name,
    parse_port_file,
)


def test_parse_port_file(tmp_path):
    p_utf16 = tmp_path / "port_utf16.txt"
    p_utf16.write_bytes("51234\r\n".encode("utf-16-le"))
    assert parse_port_file(p_utf16) == 51234

    p_utf8 = tmp_path / "port_utf8.txt"
    p_utf8.write_bytes("52345\n".encode("utf-8"))
    assert parse_port_file(p_utf8) == 52345

    p_empty = tmp_path / "empty.txt"
    p_empty.write_text("")
    assert parse_port_file(p_empty) is None


def test_extract_database_name(tmp_path):
    data_dir = tmp_path / "Data"
    data_dir.mkdir()

    # Pattern: <guid>.<version>.db.xml
    db_xml = data_dir / "ad023c8e-cfd2-4118-9384-acb93155b216.12.db.xml"
    db_xml.write_text("<Database><Name>ad023c8e-cfd2-4118-9384-acb93155b216</Name></Database>")
    assert extract_database_name(data_dir) == "ad023c8e-cfd2-4118-9384-acb93155b216"


def test_discover_running_instances_deterministic_and_mocked(tmp_path):
    # Setup mock workspace directories
    ws1_data = tmp_path / "ws_alpha" / "Data"
    ws1_data.mkdir(parents=True)
    (ws1_data / "msmdsrv.port.txt").write_bytes("50001\r\n".encode("utf-16-le"))
    (ws1_data / "db_alpha.1.db.xml").write_text("<Database><Name>db_alpha</Name></Database>")

    ws2_data = tmp_path / "ws_beta" / "Data"
    ws2_data.mkdir(parents=True)
    (ws2_data / "msmdsrv.port.txt").write_bytes("50002\r\n".encode("utf-16-le"))
    (ws2_data / "db_beta.1.db.xml").write_text("<Database><Name>db_beta</Name></Database>")

    mock_processes = [
        {
            "ProcessId": 100,
            "ParentProcessId": 10,
            "Name": "PBIDesktop.exe",
            "ExecutablePath": r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe",
            "CommandLine": r'"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe" "C:\Models\BetaReport.pbix"',
        },
        {
            "ProcessId": 200,
            "ParentProcessId": 100,
            "Name": "msmdsrv.exe",
            "ExecutablePath": r"C:\Program Files\Microsoft Power BI Desktop\bin\msmdsrv.exe",
            "CommandLine": f'msmdsrv.exe -s "{ws2_data}"',
        },
        {
            "ProcessId": 101,
            "ParentProcessId": 10,
            "Name": "PBIDesktop.exe",
            "ExecutablePath": r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe",
            "CommandLine": r'"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe"',
        },
        {
            "ProcessId": 201,
            "ParentProcessId": 101,
            "Name": "msmdsrv.exe",
            "ExecutablePath": r"C:\Program Files\Microsoft Power BI Desktop\bin\msmdsrv.exe",
            "CommandLine": f'msmdsrv.exe -s "{ws1_data}"',
        },
    ]

    mock_titles = {
        101: ["*AlphaModel - Power BI Desktop"],
    }

    # Discover with mocked inputs
    instances = discover_running_instances(
        verify_active=True,
        process_provider=lambda: mock_processes,
        window_titles_provider=lambda: mock_titles,
        port_checker=lambda p: True,
    )

    assert len(instances) == 2

    # Deterministic sort order by (file_name lowercase, port, msmdsrv_pid)
    # AlphaModel.pbix should come before BetaReport.pbix
    inst_alpha = instances[0]
    inst_beta = instances[1]

    assert inst_alpha.file_name == "AlphaModel.pbix"
    assert inst_alpha.file_method == "window_title"
    assert inst_alpha.has_unsaved_changes is True
    assert inst_alpha.port == 50001
    assert inst_alpha.database_name == "db_alpha"

    assert inst_beta.file_name == "BetaReport.pbix"
    assert inst_beta.file_path == r"C:\Models\BetaReport.pbix"
    assert inst_beta.file_method == "command_line"
    assert inst_beta.has_unsaved_changes is False
    assert inst_beta.port == 50002
    assert inst_beta.database_name == "db_beta"


def test_discover_skips_inactive_port(tmp_path):
    ws_data = tmp_path / "ws_inactive" / "Data"
    ws_data.mkdir(parents=True)
    (ws_data / "msmdsrv.port.txt").write_bytes("59999\r\n".encode("utf-16-le"))

    mock_processes = [
        {
            "ProcessId": 300,
            "ParentProcessId": 299,
            "Name": "msmdsrv.exe",
            "CommandLine": f'msmdsrv.exe -s "{ws_data}"',
        }
    ]

    # Port checker returns False
    instances = discover_running_instances(
        verify_active=True,
        process_provider=lambda: mock_processes,
        port_checker=lambda p: False,
    )
    assert len(instances) == 0
