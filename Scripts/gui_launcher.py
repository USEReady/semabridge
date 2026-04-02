#!/usr/bin/env python
"""
Entry point for the SemaBridge Graphical User Interface.
"""

import sys
import os
from pathlib import Path

# Set High DPI policy globally before any Qt imports
# This is critical for modern screens
try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    # Note: We don't want to import QApplication yet to avoid conflict with launch_ui
except ImportError:
    pass

# Ensure src directory is in path
# Ensure src directory is in path (relative to this script)
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "src"))

from semabridge.cli.ui.main_window import SemaBridgeMainWindow, get_stylesheet

if __name__ == "__main__":
    # Note: launch_ui() handles its own QApplication creation
    sys.exit(launch_ui())
