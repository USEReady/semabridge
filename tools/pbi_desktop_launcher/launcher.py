"""Power BI Desktop External Tools launcher for SemaBridge.

Invoked by Power BI Desktop with arguments:
  "%server%" "%database%"

Reads the SemaBridge base URL from launcher_config.json located in the launcher's directory.
Opens the SemaBridge UI URL:
  /projects/new?source=pbi_desktop&server=<server>&database=<database>

If the config file or base URL is missing, shows a clear error message and halts
(no default URL is assumed).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import urllib.parse
import webbrowser
from typing import Optional, Tuple


def get_config_path() -> Path:
    """Return path to launcher_config.json."""
    env_path = os.environ.get("SEMABRIDGE_LAUNCHER_CONFIG")
    if env_path:
        return Path(env_path).resolve()
    return Path(__file__).resolve().parent / "launcher_config.json"


def load_base_url(config_path: Optional[Path] = None) -> str:
    """Load semabridge_base_url from config file. Raises RuntimeError if missing."""
    cfg_file = config_path or get_config_path()
    if not cfg_file.exists():
        raise RuntimeError(
            f"Configuration file not found: {cfg_file}. "
            "Please configure 'semabridge_base_url' in launcher_config.json (no default URL is permitted)."
        )

    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to parse configuration file {cfg_file}: {exc}") from exc

    base_url = (data.get("semabridge_base_url") or "").strip()
    if not base_url:
        raise RuntimeError(
            f"Missing 'semabridge_base_url' in {cfg_file}. "
            "Please specify the SemaBridge base URL (no default URL is permitted)."
        )
    return base_url.rstrip("/")


def build_launch_url(base_url: str, server: str, database: str) -> str:
    """Construct full project creation URL for pre-selected Desktop instance."""
    query_params = {
        "source": "pbi_desktop",
        "server": server,
        "database": database,
    }
    encoded = urllib.parse.urlencode(query_params)
    return f"{base_url}/projects/new?{encoded}"


def show_error(message: str) -> None:
    """Display error message to stderr and Windows message box if available."""
    sys.stderr.write(f"ERROR: {message}\n")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "SemaBridge External Tools Launcher", 0x10)
        except Exception:
            pass


def main(args: Optional[list[str]] = None) -> int:
    """Main launcher entrypoint."""
    argv = args if args is not None else sys.argv[1:]
    if len(argv) < 2:
        show_error(
            "Invalid arguments. Expected arguments: \"%server%\" \"%database%\".\n"
            f"Received: {argv}"
        )
        return 1

    server = argv[0].strip().strip('"')
    database = argv[1].strip().strip('"')

    try:
        base_url = load_base_url()
    except Exception as exc:
        show_error(str(exc))
        return 1

    target_url = build_launch_url(base_url, server, database)
    print(f"Launching SemaBridge: {target_url}")
    webbrowser.open(target_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
