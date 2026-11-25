"""Tests for semabridge.pbitool.json schema, icon data, and install scripts."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_DIR = REPO_ROOT / "tools" / "pbi_desktop_launcher"


def test_pbitool_json_schema_and_fields():
    tool_file = LAUNCHER_DIR / "semabridge.pbitool.json"
    assert tool_file.exists(), f"pbitool.json not found at {tool_file}"

    data = json.loads(tool_file.read_text(encoding="utf-8"))

    # Required fields
    assert data.get("version") == "1.0"
    assert data.get("name") == "SemaBridge"
    assert "description" in data and len(data["description"]) > 0
    assert "path" in data
    assert data.get("arguments") == '"%server%" "%database%"'

    # Icon data verification
    icon_data = data.get("iconData", "")
    assert icon_data.startswith("image/png;base64,")
    raw_b64 = icon_data.split("image/png;base64,")[1]
    decoded_bytes = base64.b64decode(raw_b64)
    # Check PNG magic bytes: \x89PNG\r\n\x1a\n
    assert decoded_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_install_and_uninstall_scripts_exist():
    install_ps1 = LAUNCHER_DIR / "install.ps1"
    uninstall_ps1 = LAUNCHER_DIR / "uninstall.ps1"
    assert install_ps1.exists()
    assert uninstall_ps1.exists()

    install_content = install_ps1.read_text(encoding="utf-8")
    assert "Power BI Desktop\\External Tools" in install_content
    assert "Test-IsAdministrator" in install_content

    uninstall_content = uninstall_ps1.read_text(encoding="utf-8")
    assert "semabridge.pbitool.json" in uninstall_content


def test_powershell_script_syntax():
    """Verify that install.ps1 and uninstall.ps1 have valid PowerShell syntax."""
    for script_name in ("install.ps1", "uninstall.ps1"):
        script_path = LAUNCHER_DIR / script_name
        # Test parser with powershell
        cmd = f"[System.Management.Automation.Language.Parser]::ParseFile('{script_path}', [ref]$null, [ref]$null)"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
        assert res.returncode == 0, f"Syntax error in {script_name}: {res.stderr}"
