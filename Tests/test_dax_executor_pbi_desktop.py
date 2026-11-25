"""Tests for Power BI Desktop DAX backend, Store app workspaces, and stale workspace filtering."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from semabridge.reconciliation.dax_executor import (
    discover_local_pbi_ports,
    _PBI_PORT_PATTERNS,
    _is_port_listening,
    _get_running_msmdsrv_workspaces,
    _open_adomd_connection,
)


def test_pbi_port_patterns_includes_store_app():
    """Verify that Store App workspace pattern is in _PBI_PORT_PATTERNS."""
    store_pattern = r"%USERPROFILE%\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt"
    assert store_pattern in _PBI_PORT_PATTERNS


def test_discover_store_app_workspace(tmp_path, monkeypatch):
    """Verify discovery finds msmdsrv.port.txt in Store App workspace directory."""
    store_ws = tmp_path / "StoreApp" / "AnalysisServicesWorkspaces" / "AnalysisServicesWorkspace_store_1" / "Data"
    store_ws.mkdir(parents=True)
    (store_ws / "msmdsrv.port.txt").write_bytes("59123\r\n".encode("utf-16-le"))

    test_pattern = str(tmp_path / "StoreApp" / "AnalysisServicesWorkspaces" / "*" / "Data" / "msmdsrv.port.txt")
    monkeypatch.setattr("semabridge.reconciliation.dax_executor._PBI_PORT_PATTERNS", (test_pattern,))
    monkeypatch.setattr("semabridge.reconciliation.dax_executor._is_port_listening", lambda p: True)
    monkeypatch.setattr(
        "semabridge.reconciliation.dax_executor._get_running_msmdsrv_workspaces",
        lambda: {"analysisservicesworkspace_store_1"},
    )

    discovered = discover_local_pbi_ports(skip_stale=True)
    assert len(discovered) == 1
    assert discovered[0]["port"] == 59123
    assert discovered[0]["workspace_dir"] == "AnalysisServicesWorkspace_store_1"


def test_skip_stale_workspace_port_not_listening(tmp_path, monkeypatch):
    """Workspaces whose ports do not accept a TCP connection are skipped as stale."""
    ws_data = tmp_path / "AnalysisServicesWorkspaces" / "AnalysisServicesWorkspace_dead" / "Data"
    ws_data.mkdir(parents=True)
    (ws_data / "msmdsrv.port.txt").write_bytes("51111\r\n".encode("utf-8"))

    test_pattern = str(tmp_path / "AnalysisServicesWorkspaces" / "*" / "Data" / "msmdsrv.port.txt")
    monkeypatch.setattr("semabridge.reconciliation.dax_executor._PBI_PORT_PATTERNS", (test_pattern,))
    # Port is not listening
    monkeypatch.setattr("semabridge.reconciliation.dax_executor._is_port_listening", lambda p: False)
    monkeypatch.setattr(
        "semabridge.reconciliation.dax_executor._get_running_msmdsrv_workspaces",
        lambda: {"analysisservicesworkspace_dead"},
    )

    discovered = discover_local_pbi_ports(skip_stale=True)
    assert len(discovered) == 0

    # If skip_stale=False, the port file is read without connectivity checks
    unfiltered = discover_local_pbi_ports(skip_stale=False)
    assert len(unfiltered) == 1
    assert unfiltered[0]["port"] == 51111


def test_skip_stale_workspace_msmdsrv_not_running(tmp_path, monkeypatch):
    """Workspaces whose msmdsrv process is not running are skipped as stale even if port is open."""
    ws_data = tmp_path / "AnalysisServicesWorkspaces" / "AnalysisServicesWorkspace_orphan" / "Data"
    ws_data.mkdir(parents=True)
    (ws_data / "msmdsrv.port.txt").write_bytes("52222\r\n".encode("utf-8"))

    test_pattern = str(tmp_path / "AnalysisServicesWorkspaces" / "*" / "Data" / "msmdsrv.port.txt")
    monkeypatch.setattr("semabridge.reconciliation.dax_executor._PBI_PORT_PATTERNS", (test_pattern,))
    monkeypatch.setattr("semabridge.reconciliation.dax_executor._is_port_listening", lambda p: True)
    # Different msmdsrv workspace is running
    monkeypatch.setattr(
        "semabridge.reconciliation.dax_executor._get_running_msmdsrv_workspaces",
        lambda: {"analysisservicesworkspace_other"},
    )

    discovered = discover_local_pbi_ports(skip_stale=True)
    assert len(discovered) == 0


def test_keep_active_workspace(tmp_path, monkeypatch):
    """Active workspaces where port accepts connection AND msmdsrv is running are kept."""
    ws1 = tmp_path / "AnalysisServicesWorkspaces" / "AnalysisServicesWorkspace_live" / "Data"
    ws1.mkdir(parents=True)
    (ws1 / "msmdsrv.port.txt").write_bytes("53333\r\n".encode("utf-8"))

    ws2 = tmp_path / "AnalysisServicesWorkspaces" / "AnalysisServicesWorkspace_stale" / "Data"
    ws2.mkdir(parents=True)
    (ws2 / "msmdsrv.port.txt").write_bytes("54444\r\n".encode("utf-8"))

    test_pattern = str(tmp_path / "AnalysisServicesWorkspaces" / "*" / "Data" / "msmdsrv.port.txt")
    monkeypatch.setattr("semabridge.reconciliation.dax_executor._PBI_PORT_PATTERNS", (test_pattern,))
    # Only port 53333 is listening
    monkeypatch.setattr("semabridge.reconciliation.dax_executor._is_port_listening", lambda p: p == 53333)
    # Only AnalysisServicesWorkspace_live is running
    monkeypatch.setattr(
        "semabridge.reconciliation.dax_executor._get_running_msmdsrv_workspaces",
        lambda: {"analysisservicesworkspace_live"},
    )

    discovered = discover_local_pbi_ports(skip_stale=True)
    assert len(discovered) == 1
    assert discovered[0]["port"] == 53333
    assert discovered[0]["workspace_dir"] == "AnalysisServicesWorkspace_live"
