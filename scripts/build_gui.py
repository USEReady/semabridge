#!/usr/bin/env python
"""
Build script for creating SemaBridge GUI executable (Windowed mode).

Usage:
    uv run python scripts/build_gui.py
"""

import subprocess
import sys
import platform
import shutil
from pathlib import Path


def get_platform_name() -> str:
    """Get platform-specific executable name."""
    system = platform.system()
    if system == "Windows":
        return "SemaBridgeDashboard.exe"
    return "SemaBridgeDashboard"


def clean_build_dirs() -> None:
    """Clean previous build artifacts."""
    dirs_to_clean = ["build", "dist_gui"]
    for dir_name in dirs_to_clean:
        dir_path = Path(dir_name)
        if dir_path.exists():
            print(f"🧹 Cleaning {dir_name}/...")
            shutil.rmtree(dir_path)


def get_pyinstaller_command() -> list:
    """Build the PyInstaller command with GUI-specific options."""
    exe_name = get_platform_name().replace(".exe", "")
    
    # Base command: use sys.executable -m PyInstaller for robustness
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",       # Single file executable
        "--name", exe_name,
        "--noconfirm",     # Overwrite without asking
        "--noconsole",     # WINDOWED MODE: No terminal window
        
        # Add data files (format: source:destination)
        "--add-data", "src/semabridge/formats:semabridge/formats",
        
        # Hidden imports for dynamic loading
        "--hidden-import", "PyQt6",
        "--hidden-import", "PyQt6.QtCore",
        "--hidden-import", "PyQt6.QtGui",
        "--hidden-import", "PyQt6.QtWidgets",
        "--hidden-import", "snowflake.connector",
        "--hidden-import", "duckdb",
        "--hidden-import", "msal",
        "--hidden-import", "httpx",
        "--hidden-import", "tenacity",
        
        # Collect all files from these packages
        "--collect-all", "PyQt6",
        "--collect-all", "duckdb",
        "--collect-all", "rich", 
        
        # Hidden imports for connectors
        "--hidden-import", "semabridge.connectors.fabric_extractor",
        "--hidden-import", "semabridge.connectors.snowflake_extractor",
        "--hidden-import", "semabridge.connectors.fabric_deployer",
        "--hidden-import", "semabridge.connectors.snowflake_deployer",
        
        # Hidden imports for UI components
        "--hidden-import", "semabridge.ui.main_window",
        "--hidden-import", "semabridge.ui.source_browser",
        "--hidden-import", "semabridge.ui.yaml_editor",
        "--hidden-import", "semabridge.ui.diff_viewer",
        "--hidden-import", "semabridge.ui.version_history",
        "--hidden-import", "semabridge.ui.workers",
        
        # Hidden imports for repository
        "--hidden-import", "semabridge.repository.duckdb_manager",
        "--hidden-import", "semabridge.repository.semantic_version_manager",
        "--hidden-import", "semabridge.repository.semantic_diff_engine",
        
        # GUI ENTRY POINT
        "scripts/gui_launcher.py",
    ]
    
    return cmd


def build_gui_executable() -> bool:
    """Build standalone GUI executable."""
    print("=" * 60)
    print("🏗️  Building SemaBridge Dashboard (GUI-Only)")
    print("=" * 60)
    
    exe_name = get_platform_name()
    cmd = get_pyinstaller_command()
    
    print(f"\n📦 Platform: {platform.system()}")
    print(f"📄 Output: dist/{exe_name}")
    print(f"\n🔨 Running PyInstaller...")
    
    try:
        subprocess.run(cmd, check=True)
        
        exe_path = Path("dist") / exe_name
        if not exe_path.exists():
            print(f"\n❌ Error: Executable not found at {exe_path}")
            return False
        
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        
        print("\n" + "=" * 60)
        print("✅ GUI Build Complete!")
        print("=" * 60)
        print(f"📍 Location: {exe_path}")
        print(f"📊 Size: {size_mb:.1f} MB")
        print("\n💡 This executable launches the UI directly without a console.")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Build failed: {e}")
        return False


def main() -> int:
    """Main build process."""
    # Build executable
    success = build_gui_executable()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
