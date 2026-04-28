#!/usr/bin/env python
"""
Build script for creating SemaBridge executable.

Usage:
    uv run python scripts/build_exe.py
    
Creates a standalone executable with all dependencies bundled, including:
- Python interpreter
- Qt6 libraries and plugins
- All Python dependencies (PyQt6, DuckDB, Snowflake connector, etc.)
- Application resources (formats, templates, UI files)
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
        return "SemaBridge.exe"
    return "SemaBridge"


def clean_build_dirs() -> None:
    """Clean previous build artifacts."""
    dirs_to_clean = ["build", "dist"]
    for dir_name in dirs_to_clean:
        dir_path = Path(dir_name)
        if dir_path.exists():
            print(f"🧹 Cleaning {dir_name}/...")
            shutil.rmtree(dir_path)


def get_pyinstaller_command() -> list:
    """Build the PyInstaller command with all necessary options."""
    exe_name = get_platform_name().replace(".exe", "")
    
    # Base command
    cmd = [
        "pyinstaller",
        "--onefile",  # Single file executable
        "--name", exe_name,
        "--noconfirm",  # Overwrite without asking
        "--console",  # Keep console for CLI output
        
        # Add data files (format: source:destination)
        "--add-data", "src/semabridge/formats:semabridge/formats",
        
        # Hidden imports for dynamic loading
        "--hidden-import", "snowflake.connector",
        "--hidden-import", "duckdb",
        "--hidden-import", "msal",
        "--hidden-import", "httpx",
        "--hidden-import", "tenacity",
        
        # Collect all files from these packages
        "--collect-all", "duckdb",
        "--collect-all", "rich",  # Fix for Rich library unicode data
        
        # Hidden imports for connectors (dynamic plugin loading)
        "--hidden-import", "semabridge.connectors.fabric_extractor",
        "--hidden-import", "semabridge.connectors.snowflake_extractor",
        "--hidden-import", "semabridge.connectors.fabric_deployer",
        "--hidden-import", "semabridge.connectors.snowflake_deployer",
        
        # Hidden imports for repository
        "--hidden-import", "semabridge.repository.duckdb_manager",
        "--hidden-import", "semabridge.repository.semantic_version_manager",
        "--hidden-import", "semabridge.repository.semantic_diff_engine",
        
        # Entry point
        "src/semabridge/cli/main.py",
    ]
    
    return cmd


def build_executable() -> bool:
    """Build standalone executable with PyInstaller."""
    print("=" * 60)
    print("🏗️  Building SemaBridge Executable")
    print("=" * 60)
    
    exe_name = get_platform_name()
    cmd = get_pyinstaller_command()
    
    print(f"\n📦 Platform: {platform.system()}")
    print(f"📄 Output: dist/{exe_name}")
    print(f"\n🔨 Running PyInstaller...")
    print(f"   Command: {' '.join(cmd[:3])} ...")
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=False,  # Show PyInstaller output
        )
        
        # Check if executable was created
        exe_path = Path("dist") / exe_name
        if not exe_path.exists():
            print(f"\n❌ Error: Executable not found at {exe_path}")
            return False
        
        # Get size info
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        
        print("\n" + "=" * 60)
        print("✅ Build Complete!")
        print("=" * 60)
        print(f"📍 Location: {exe_path}")
        print(f"📊 Size: {size_mb:.1f} MB")
        print(f"\n💡 To run:")
        print(f"   ./dist/{exe_name} --help")
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Build failed with exit code {e.returncode}")
        return False
    except Exception as e:
        print(f"\n❌ Build failed: {e}")
        return False


def main() -> int:
    """Main build process."""
    print("\n🌉 SemaBridge Executable Builder\n")
    
    # Check if PyInstaller is available
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ PyInstaller not found. Please install it:")
        print("   pip install pyinstaller")
        return 1
    
    # Clean previous builds
    clean_build_dirs()
    
    # Build executable
    success = build_executable()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())