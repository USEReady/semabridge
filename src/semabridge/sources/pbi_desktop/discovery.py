"""Production discovery for local running Power BI Desktop Analysis Services engines.

Enumerates running Desktop instances, ports, workspace directories, PIDs,
associated database names, and open file names (from process command lines or window titles).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
import glob
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class DiscoveredDesktopInstance:
    """Metadata describing a running Power BI Desktop Analysis Services instance."""

    port: int
    workspace_folder: str
    workspace_dir: str
    msmdsrv_pid: int
    parent_pbidesktop_pid: Optional[int]
    database_name: str
    file_name: str
    file_path: Optional[str]
    file_method: str  # "command_line" | "desktop_recent_history" | "window_title" | "none"
    window_title: Optional[str]
    pbidesktop_exe: Optional[str] = None
    has_unsaved_changes: bool = False
    is_live_connected: bool = False
    is_unsaved_new_file: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_window_titles_for_pids() -> Dict[int, List[str]]:
    """Enumerate top-level visible window titles grouped by process ID on Windows.

    Attempts to attach to the interactive input desktop (Default) so that
    subshells and service threads can discover interactive user windows.
    """
    titles_by_pid: Dict[int, List[str]] = {}
    if os.name != "nt":
        return titles_by_pid

    try:
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
        cb = EnumWindowsProc(enum_proc)

        h_desk = None
        try:
            h_desk = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0100)
        except Exception:
            h_desk = None

        if h_desk:
            try:
                ctypes.windll.user32.EnumDesktopWindows(h_desk, cb, 0)
            finally:
                ctypes.windll.user32.CloseDesktop(h_desk)

        if not titles_by_pid:
            ctypes.windll.user32.EnumWindows(cb, 0)
    except Exception:
        pass
    return titles_by_pid


def get_recent_paths_from_pbi_desktop() -> List[str]:
    """Read recent .pbix file paths from Power BI Desktop's FileHistory in User.zip.

    Checks both standard Desktop and Windows Store app locations.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    user_profile = os.environ.get("USERPROFILE", "")
    candidates = [
        Path(local_app_data) / "Microsoft" / "Power BI Desktop" / "User.zip",
        Path(user_profile) / "Microsoft" / "Power BI Desktop Store App" / "User.zip",
    ]
    results: List[str] = []
    seen = set()

    for user_zip in candidates:
        if not user_zip.exists():
            continue
        try:
            with zipfile.ZipFile(str(user_zip), "r") as zf:
                if "UserInterface/Settings.xml" in zf.namelist():
                    content = zf.read("UserInterface/Settings.xml")
                    root = ET.fromstring(content)
                    for entry in root.findall(".//Entry"):
                        val = entry.attrib.get("Value", "")
                        if "filePath" in val:
                            raw_json = val[1:] if val.startswith("s[") and val.endswith("]") else val
                            try:
                                data = json.loads(raw_json)
                                if isinstance(data, list):
                                    for item in data:
                                        if isinstance(item, dict):
                                            p = item.get("filePath")
                                            if p and p not in seen:
                                                seen.add(p)
                                                results.append(p)
                            except Exception:
                                pass
        except Exception:
            pass
    return results


def parse_port_file(port_file_path: Path) -> Optional[int]:
    """Read Analysis Services port from msmdsrv.port.txt (UTF-16LE or UTF-8)."""
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
            port_num = int(digits)
            if 0 < port_num < 65536:
                return port_num
    except Exception:
        pass
    return None


def extract_database_name(data_dir: Path) -> str:
    """Extract database GUID/name from *.db.xml or directory in workspace Data dir."""
    if not data_dir.exists():
        return ""
    try:
        # Match pattern: <db_name>.<version>.db.xml
        for p in data_dir.glob("*.db.xml"):
            name = p.name
            m = re.match(r"^(.+?)\.\d+\.db\.xml$", name, re.IGNORECASE)
            if m:
                return m.group(1).strip()
            # Try reading <Name> from object definition
            try:
                xml_text = p.read_text(encoding="utf-8", errors="ignore")
                m_xml = re.search(r"<Database>\s*<Name>([^<]+)</Name>", xml_text)
                if m_xml:
                    return m_xml.group(1).strip()
            except Exception:
                pass

        # Fallback to *.db directory
        for p in data_dir.glob("*.db"):
            if p.is_dir():
                m = re.match(r"^(.+?)\.\d+\.db$", p.name, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return ""


def get_process_info_via_powershell() -> List[Dict[str, Any]]:
    """Query Win32_Process for msmdsrv and PBIDesktop processes."""
    if os.name != "nt":
        return []
    ps_cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match '(?i)msmdsrv|pbidesktop' } | "
        "Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            if isinstance(data, dict):
                return [data]
            elif isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def is_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """Check if port is actively listening for TCP connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover_running_instances(
    *,
    verify_active: bool = True,
    process_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    window_titles_provider: Optional[Callable[[], Dict[int, List[str]]]] = None,
    recent_paths_provider: Optional[Callable[[], List[str]]] = None,
    port_checker: Optional[Callable[[int], bool]] = None,
) -> List[DiscoveredDesktopInstance]:
    """Discover all running Power BI Desktop Analysis Services instances in deterministic order."""
    get_processes = process_provider or get_process_info_via_powershell
    get_titles = window_titles_provider or get_window_titles_for_pids
    get_recent = recent_paths_provider or get_recent_paths_from_pbi_desktop
    check_port = port_checker or is_port_listening

    processes = get_processes()
    window_titles = get_titles()
    recent_paths = get_recent()

    pbi_desktops: Dict[int, Dict[str, Any]] = {}
    msmdsrv_procs: List[Dict[str, Any]] = []

    for p in processes:
        name = (p.get("Name") or "").lower()
        pid = p.get("ProcessId")
        if not pid:
            continue
        if "pbidesktop" in name:
            pbi_desktops[pid] = p
        elif "msmdsrv" in name:
            msmdsrv_procs.append(p)

    instances: List[DiscoveredDesktopInstance] = []

    for ms in msmdsrv_procs:
        ms_pid = ms.get("ProcessId")
        parent_pid = ms.get("ParentProcessId")
        ms_cmd = ms.get("CommandLine") or ""

        # Extract workspace data directory from -s argument
        # e.g. -s "C:\...\AnalysisServicesWorkspace_<guid>\Data"
        workspace_folder = ""
        workspace_dir_path: Optional[Path] = None
        m_ws = re.search(r'-s\s+"?([^"]+)"?', ms_cmd)
        if m_ws:
            workspace_data_dir = Path(m_ws.group(1).strip())
            workspace_folder = workspace_data_dir.parent.name
            workspace_dir_path = workspace_data_dir.parent
        else:
            workspace_data_dir = None

        if not workspace_data_dir:
            continue

        port_file = workspace_data_dir / "msmdsrv.port.txt"
        port = parse_port_file(port_file)
        if not port:
            continue

        if verify_active and not check_port(port):
            continue

        database_name = extract_database_name(workspace_data_dir)

        # Parent PBIDesktop info
        pbi_info = pbi_desktops.get(parent_pid, {}) if parent_pid else {}
        pbi_cmd = pbi_info.get("CommandLine") or ""
        pbi_exe = pbi_info.get("ExecutablePath") or ""

        # Check command line for .pbix file
        file_from_cmd = None
        m_pbix = re.search(r'"([^"]+\.pbix)"', pbi_cmd, re.IGNORECASE)
        if m_pbix:
            file_from_cmd = m_pbix.group(1)
        else:
            m_pbix2 = re.search(r'([^\s"]+\.pbix)', pbi_cmd, re.IGNORECASE)
            if m_pbix2:
                file_from_cmd = m_pbix2.group(1)

        # Window titles for parent PBIDesktop
        titles = window_titles.get(parent_pid, []) if parent_pid else []
        file_from_title = None
        primary_title = None
        has_unsaved = False

        for t in titles:
            t_str = str(t).strip()
            if not t_str:
                continue
            primary_title = t_str
            clean_t = t_str
            if clean_t.startswith("*") or clean_t.endswith("*"):
                has_unsaved = True
                clean_t = clean_t.strip("*").strip()
            # Strip " - Power BI Desktop" suffix if present
            parts = re.split(r"\s+-\s+Power BI Desktop", clean_t, flags=re.IGNORECASE)
            raw_name = parts[0].strip()
            if raw_name:
                file_from_title = raw_name
                break

        is_unsaved_new_file = False
        file_path: Optional[str] = None
        file_method: str = "none"

        # Check if title indicates genuinely new unsaved file
        title_stem = Path(file_from_title).stem.strip() if file_from_title else ""
        if title_stem.lower() in ("untitled", "unsaved new file") and not file_from_cmd:
            is_unsaved_new_file = True
            file_name = "Unsaved new file"
            file_path = None
            file_method = "none"
        elif file_from_cmd:
            file_name = Path(file_from_cmd).name
            file_path = file_from_cmd
            file_method = "command_line"
        elif file_from_title:
            # Try to match recent files history from User.zip
            matched_recent: Optional[str] = None
            clean_target = title_stem.casefold()
            for rp in recent_paths:
                p_obj = Path(rp)
                if p_obj.stem.casefold() == clean_target or p_obj.name.casefold() == file_from_title.casefold():
                    if p_obj.exists():
                        matched_recent = str(p_obj)
                        break

            if matched_recent:
                file_name = Path(matched_recent).name
                file_path = matched_recent
                file_method = "desktop_recent_history"
            else:
                file_name = file_from_title if file_from_title.lower().endswith(".pbix") else f"{file_from_title}.pbix"
                file_path = None
                file_method = "window_title"
        else:
            file_name = "Unsaved new file"
            file_path = None
            file_method = "none"
            is_unsaved_new_file = True

        # Check for live-connected report (no local model tables)
        # In a live connection, msmdsrv is often not started or has 0 local tabular db files
        is_live = False
        if not database_name:
            is_live = True

        inst = DiscoveredDesktopInstance(
            port=port,
            workspace_folder=workspace_folder,
            workspace_dir=str(workspace_dir_path or ""),
            msmdsrv_pid=ms_pid,
            parent_pbidesktop_pid=parent_pid,
            database_name=database_name,
            file_name=file_name,
            file_path=file_path,
            file_method=file_method,
            window_title=primary_title,
            pbidesktop_exe=pbi_exe,
            has_unsaved_changes=has_unsaved,
            is_live_connected=is_live,
            is_unsaved_new_file=is_unsaved_new_file,
        )
        instances.append(inst)

    # Sort deterministically by (file_name lowercase, port, msmdsrv_pid)
    instances.sort(key=lambda x: (x.file_name.lower(), x.port, x.msmdsrv_pid))
    return instances
