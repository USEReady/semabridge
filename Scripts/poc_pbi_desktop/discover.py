"""Discovery script for running Power BI Desktop Analysis Services instances.

Discovers running msmdsrv engines, their ports, workspace folders,
parent PBIDesktop PIDs, and associated PBIX file names via:
1) Process command line arguments
2) Win32 Top-level Window titles
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess

def get_window_titles_for_pids() -> Dict[int, List[str]]:
    """Enumerate visible window titles for all running processes."""
    titles_by_pid: Dict[int, List[str]] = {}

    def enum_proc(hwnd: int, lparam: int) -> bool:
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            p_val = pid.value
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value.strip()
                if title:
                    titles_by_pid.setdefault(p_val, []).append(title)
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    ctypes.windll.user32.EnumWindows(EnumWindowsProc(enum_proc), 0)
    return titles_by_pid


def parse_port_file(port_file_path: Path) -> Optional[int]:
    """Read Analysis Services port from msmdsrv.port.txt (UTF-16LE or ASCII/UTF-8)."""
    if not port_file_path.exists():
        return None
    try:
        raw_bytes = port_file_path.read_bytes()
        text = raw_bytes.decode("utf-16-le", errors="ignore").strip()
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            text = raw_bytes.decode("utf-8", errors="ignore").strip()
            digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            return int(digits)
    except Exception:
        pass
    return None


def get_process_info_via_powershell() -> List[Dict[str, Any]]:
    """Query Win32_Process for msmdsrv and PBIDesktop processes."""
    ps_cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match '(?i)msmdsrv|pbidesktop' } | "
        "Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine | "
        "ConvertTo-Json -Compress"
    )
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        data = json.loads(res.stdout)
        if isinstance(data, dict):
            return [data]
        elif isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def discover_running_pbi_instances() -> List[Dict[str, Any]]:
    """Discover all running Power BI Desktop engines and return detailed metadata."""
    processes = get_process_info_via_powershell()
    window_titles = get_window_titles_for_pids()

    pbi_desktops: Dict[int, Dict[str, Any]] = {}
    msmdsrv_procs: List[Dict[str, Any]] = []

    for p in processes:
        name = (p.get("Name") or "").lower()
        pid = p.get("ProcessId")
        if "pbidesktop" in name and pid:
            pbi_desktops[pid] = p
        elif "msmdsrv" in name and pid:
            msmdsrv_procs.append(p)

    instances: List[Dict[str, Any]] = []

    for ms in msmdsrv_procs:
        ms_pid = ms.get("ProcessId")
        parent_pid = ms.get("ParentProcessId")
        ms_cmd = ms.get("CommandLine") or ""
        
        # Extract workspace data directory from -s argument
        # e.g. -s "C:\Users\...\AnalysisServicesWorkspace_...\Data"
        workspace_folder = None
        m_ws = re.search(r'-s\s+"?([^"]+)"?', ms_cmd)
        if m_ws:
            workspace_data_dir = Path(m_ws.group(1).strip())
            workspace_folder = workspace_data_dir.parent.name
        else:
            workspace_data_dir = None

        port = None
        port_file = None
        if workspace_data_dir and workspace_data_dir.exists():
            p_file = workspace_data_dir / "msmdsrv.port.txt"
            if p_file.exists():
                port_file = str(p_file)
                port = parse_port_file(p_file)

        # Parent PBIDesktop info
        pbi_info = pbi_desktops.get(parent_pid, {})
        pbi_cmd = pbi_info.get("CommandLine") or ""
        pbi_exe = pbi_info.get("ExecutablePath") or ""

        # Extract pbix file from command line
        file_from_cmd = None
        m_pbix = re.search(r'"([^"]+\.pbix)"', pbi_cmd, re.IGNORECASE)
        if m_pbix:
            file_from_cmd = m_pbix.group(1)
        else:
            # Fallback if unquoted
            m_pbix2 = re.search(r'([^\s"]+\.pbix)', pbi_cmd, re.IGNORECASE)
            if m_pbix2:
                file_from_cmd = m_pbix2.group(1)

        # Window titles for parent PBIDesktop
        titles = window_titles.get(parent_pid, [])
        file_from_title = None
        for t in titles:
            # Power BI title format: "<FileName> - Power BI Desktop" or similar
            if "power bi desktop" in t.lower():
                parts = t.split(" - Power BI Desktop")
                if parts[0].strip():
                    file_from_title = parts[0].strip()

        # Determine file name and discovery method
        if file_from_cmd:
            file_name = Path(file_from_cmd).name
            file_path = file_from_cmd
            file_method = "command_line"
        elif file_from_title:
            file_name = file_from_title
            file_path = None
            file_method = "window_title"
        else:
            file_name = "Untitled"
            file_path = None
            file_method = "none"

        instances.append({
            "port": port,
            "workspace_folder": workspace_folder,
            "port_file": port_file,
            "msmdsrv_pid": ms_pid,
            "parent_pbidesktop_pid": parent_pid,
            "pbidesktop_exe": pbi_exe,
            "file_name": file_name,
            "file_path": file_path,
            "file_method": file_method,
            "all_window_titles": titles,
            "msmdsrv_cmdline": ms_cmd,
            "pbidesktop_cmdline": pbi_cmd,
        })

    return instances


if __name__ == "__main__":
    results = discover_running_pbi_instances()
    print(f"Discovered {len(results)} running Desktop engine(s):\n")
    for idx, inst in enumerate(results, 1):
        print(f"[{idx}] Port: {inst['port']}")
        print(f"    Workspace: {inst['workspace_folder']}")
        print(f"    Port file: {inst['port_file']}")
        print(f"    msmdsrv PID: {inst['msmdsrv_pid']}")
        print(f"    Parent PBIDesktop PID: {inst['parent_pbidesktop_pid']}")
        print(f"    File Name: {inst['file_name']}")
        print(f"    File Path: {inst['file_path']}")
        print(f"    Detection Method: {inst['file_method']}")
        print(f"    Window Titles: {inst['all_window_titles']}")
        print()
