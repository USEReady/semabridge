"""Compatibility helper for loading pbixray in restricted environments.

On Windows enterprise machines with Windows Defender Application Control (WDAC) or
AppLocker enabled, loading native C-extension libraries (.pyd files like xpress9)
from OneDrive or Temp directories is blocked by corporate policy with:
    ImportError: DLL load failed while importing xpress9: An Application Control policy has blocked this file.

However, user-site packages (AppData\\Roaming\\Python\\...) are permitted.
This module ensures xpress9 is loaded from an allowed path before importing pbixray.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import site
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_pbixray() -> Optional[Any]:
    """Import and return PBIXRay class, resolving Windows WDAC Application Control blocks if needed."""
    try:
        from pbixray import PBIXRay  # type: ignore[import-untyped]
        return PBIXRay
    except ImportError as e:
        err_msg = str(e)
        if "Application Control" not in err_msg and "blocked" not in err_msg.lower():
            # Genuine missing pbixray
            return None

        logger.info("Windows Application Control blocked xpress8/xpress9 from current path; attempting user-site resolution")
        try:
            _resolve_wdac_xpress("xpress8")
            _resolve_wdac_xpress("xpress9")
            from pbixray import PBIXRay  # type: ignore[import-untyped]
            return PBIXRay
        except Exception as retry_err:
            logger.warning("Failed to resolve pbixray under Application Control policy: %s", retry_err)
            return None
    except Exception as exc:
        logger.warning("Unexpected error importing pbixray: %s", exc)
        return None


def _resolve_wdac_xpress(pkg_name: str = "xpress9") -> None:
    """Load xpress8 or xpress9 from an allowed directory (such as AppData/Roaming/Python site-packages)."""
    if pkg_name in sys.modules:
        return

    # Check AppData user-site packages
    target_dir = site.getusersitepackages() if hasattr(site, "getusersitepackages") else ""
    if not target_dir or not os.path.exists(target_dir):
        appdata = os.environ.get("APPDATA", "")
        py_ver = f"Python{sys.version_info.major}{sys.version_info.minor}"
        target_dir = os.path.join(appdata, "Python", py_ver, "site-packages")
    os.makedirs(target_dir, exist_ok=True)

    target_pyd: Optional[str] = None
    if os.path.isdir(target_dir):
        for f in os.listdir(target_dir):
            if f.startswith(pkg_name) and f.endswith(".pyd"):
                target_pyd = os.path.join(target_dir, f)
                break

    if not target_pyd:
        # Copy from virtualenv / sys.path if not in target_dir
        for p in sys.path:
            if os.path.isdir(p) and p != target_dir:
                try:
                    for f in os.listdir(p):
                        if f.startswith(pkg_name) and f.endswith(".pyd"):
                            src = os.path.join(p, f)
                            dest = os.path.join(target_dir, f)
                            try:
                                shutil.copy2(src, dest)
                                target_pyd = dest
                                break
                            except Exception:
                                pass
                except Exception:
                    continue
            if target_pyd:
                break

    if target_pyd and os.path.exists(target_pyd):
        spec = importlib.util.spec_from_file_location(pkg_name, target_pyd)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[pkg_name] = mod
            spec.loader.exec_module(mod)
            return

    raise ImportError(f"Could not find or load {pkg_name} from an allowed location.")


def _resolve_wdac_xpress9() -> None:
    _resolve_wdac_xpress("xpress9")
