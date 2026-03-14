# Build System and UI - Design

**Feature:** UV-based Build System and Streamlit UI
**Version:** 1.0
**Last Updated:** February 6, 2026

---

## 1. Architecture Overview

The Build System and UI feature consists of two main components:
1. **Build System**: UV-based dependency management and PyInstaller executable generation
2. **Streamlit UI**: Web-based configuration wizard and version control viewer

```
┌─────────────────────────────────────────────────────────┐
│                   SemaBridge Executable                  │
│  ┌────────────────────────────────────────────────────┐ │
│  │              CLI Entry Point                       │ │
│  │  (main.py with --ui flag detection)               │ │
│  └────────────┬───────────────────────┬───────────────┘ │
│               │                       │                  │
│      ┌────────▼────────┐     ┌───────▼────────┐        │
│      │   CLI Mode      │     │   UI Mode      │        │
│      │  (Typer/Click)  │     │  (Streamlit)   │        │
│      └─────────────────┘     └────────────────┘        │
└─────────────────────────────────────────────────────────┘
```

---

## 2. Build System Design

### 2.1 UV Integration

**File:** `pyproject.toml` (updated)

```toml
[project]
name = "semabridge"
version = "1.0.0"
description = "Semantic model synchronization platform"
requires-python = ">=3.10"
dependencies = [
    "snowflake-connector-python>=3.0.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "pyyaml>=6.0",
    "httpx>=0.25.0",
    "msal>=1.24.0",
    "typer>=0.9.0",
    "rich>=13.0.0",
    "python-dotenv>=1.0.0",
    "duckdb>=0.9.0",
    "streamlit>=1.28.0",
    "tenacity>=8.2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "black>=23.0.0",
    "ruff>=0.1.0",
    "mypy>=1.0.0",
    "pyinstaller>=6.0.0",
]

[project.scripts]
semabridge = "semabridge.cli.main:main"

[tool.uv]
dev-dependencies = ["pyinstaller>=6.0.0"]

[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"
```

### 2.2 Build Script

**File:** `scripts/build_exe.py`

```python
#!/usr/bin/env python
"""
Build script for creating SemaBridge executable.

Usage:
    uv run python scripts/build_exe.py
"""

import subprocess
import sys
import platform
import shutil
from pathlib import Path


def get_platform_name():
    """Get platform-specific executable name."""
    system = platform.system()
    if system == "Windows":
        return "SemaBridge.exe"
    return "SemaBridge"


def clean_build_dirs():
    """Clean previous build artifacts."""
    dirs_to_clean = ["build", "dist"]
    for dir_name in dirs_to_clean:
        dir_path = Path(dir_name)
        if dir_path.exists():
            print(f"Cleaning {dir_name}/...")
            shutil.rmtree(dir_path)


def build_executable():
    """Build standalone executable with PyInstaller."""
    print("Building SemaBridge executable...")
    
    exe_name = get_platform_name()
    
    # PyInstaller command
    cmd = [
        "pyinstaller",
        "--onefile",  # Single file
        "--name", exe_name.replace(".exe", ""),  # Output name
        "--add-data", "src/semabridge/ui:semabridge/ui",  # Include UI files
        "--add-data", "src/semabridge/formats:semabridge/formats",  # Include formats
        "--hidden-import", "streamlit",  # Ensure Streamlit is included
        "--hidden-import", "snowflake.connector",
        "--hidden-import", "duckdb",
        "--collect-all", "streamlit",  # Collect all Streamlit files
        "--noconfirm",  # Overwrite without asking
        "main.py",  # Entry point
    ]
    
    try:
        subprocess.run(cmd, check=True)
        print(f"\n✓ Build complete: dist/{exe_name}")
        print(f"  Size: {Path('dist') / exe_name}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Build failed: {e}")
        return False


def main():
    """Main build process."""
    print("=" * 60)
    print("SemaBridge Executable Builder")
    print("=" * 60)
    
    # Clean previous builds
    clean_build_dirs()
    
    # Build executable
    success = build_executable()
    
    if success:
        print("\n" + "=" * 60)
        print("Build successful!")
        print("=" * 60)
        sys.exit(0)
    else:
        print("\n" + "=" * 60)
        print("Build failed!")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### 2.3 Build Commands

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment with uv
uv venv

# Install dependencies
uv pip install -e ".[dev]"

# Build executable
uv run python scripts/build_exe.py

# Run executable
./dist/SemaBridge --help
```

---

## 3. UI Design

### 3.1 UI Entry Point

**File:** `src/semabridge/ui/app.py`

```python
"""
SemaBridge Streamlit UI Application.

Launch with: semabridge --ui
"""

import streamlit as st
from pathlib import Path
import yaml

from semabridge.ui.pages import configuration_wizard, version_explorer
from semabridge.core.config_loader import load_config
from semabridge.repository.duckdb_manager import DuckDBManager


def main():
    """Main UI application."""
    
    # Page configuration
    st.set_page_config(
        page_title="SemaBridge Control Center",
        page_icon="🌉",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    
    # Custom CSS for professional look
    st.markdown("""
        <style>
        .main {
            background-color: #f5f7fa;
        }
        .stButton>button {
            background-color: #0066cc;
            color: white;
            border-radius: 4px;
            padding: 0.5rem 1rem;
        }
        .stButton>button:hover {
            background-color: #0052a3;
        }
        h1, h2, h3 {
            color: #1a1a1a;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Sidebar navigation
    st.sidebar.title("🌉 SemaBridge")
    st.sidebar.markdown("---")
    
    page = st.sidebar.radio(
        "Navigation",
        ["Configuration Wizard", "Version Explorer", "Settings"],
        label_visibility="collapsed",
    )
    
    # Route to appropriate page
    if page == "Configuration Wizard":
        configuration_wizard.render()
    elif page == "Version Explorer":
        version_explorer.render()
    elif page == "Settings":
        render_settings()


def render_settings():
    """Render settings page."""
    st.title("⚙️ Settings")
    
    st.markdown("### Global Configuration")
    
    # Load current config
    try:
        config = load_config()
        st.success("Configuration loaded successfully")
    except Exception as e:
        st.error(f"Failed to load configuration: {e}")
        config = {}
    
    # Display config
    st.json(config)


if __name__ == "__main__":
    main()
```

### 3.2 Configuration Wizard Page

**File:** `src/semabridge/ui/pages/configuration_wizard.py`

```python
"""Configuration Wizard page for SemaBridge UI."""

import streamlit as st
import yaml
from pathlib import Path
from typing import List, Dict, Any

from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor


def render():
    """Render configuration wizard page."""
    
    st.title("📝 Configuration Wizard")
    st.markdown("Create a new sync configuration")
    
    # Step 1: Source Configuration
    st.markdown("### Step 1: Source Configuration")
    
    source_type = st.selectbox(
        "Select Source System",
        ["Snowflake", "Fabric", "Databricks"],
        help="Choose the platform to extract semantic models from",
    )
    
    if source_type == "Fabric":
        render_fabric_source_config()
    elif source_type == "Snowflake":
        render_snowflake_source_config()
    
    st.markdown("---")
    
    # Step 2: Target Configuration
    st.markdown("### Step 2: Target Configuration")
    
    target_type = st.selectbox(
        "Select Target System",
        ["Snowflake", "Fabric"],
        help="Choose the platform to deploy semantic models to",
    )
    
    if target_type == "Snowflake":
        render_snowflake_target_config()
    elif target_type == "Fabric":
        render_fabric_target_config()
    
    st.markdown("---")
    
    # Step 3: Model Selection
    st.markdown("### Step 3: Model Selection")
    
    if st.button("🔍 Discover Models", type="primary"):
        with st.spinner("Discovering models..."):
            models = discover_models(source_type)
            st.session_state["discovered_models"] = models
    
    if "discovered_models" in st.session_state:
        render_model_selection(st.session_state["discovered_models"])
    
    st.markdown("---")
    
    # Step 4: Generate Configuration
    st.markdown("### Step 4: Generate Configuration")
    
    if st.button("✨ Generate Configuration", type="primary"):
        config = generate_config()
        st.session_state["generated_config"] = config
    
    if "generated_config" in st.session_state:
        render_config_preview(st.session_state["generated_config"])


def render_fabric_source_config():
    """Render Fabric source configuration form."""
    
    col1, col2 = st.columns(2)
    
    with col1:
        tenant_id = st.text_input(
            "Tenant ID",
            help="Azure AD Tenant ID",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        )
        client_id = st.text_input(
            "Client ID",
            help="Azure AD Application (Client) ID",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        )
    
    with col2:
        workspace_id = st.text_input(
            "Workspace ID",
            help="Fabric Workspace ID",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        )
        client_secret = st.text_input(
            "Client Secret",
            type="password",
            help="Azure AD Client Secret",
        )
    
    # Store in session state
    st.session_state["source_config"] = {
        "type": "fabric",
        "tenant_id": tenant_id,
        "client_id": client_id,
        "workspace_id": workspace_id,
        "client_secret": client_secret,
    }


def render_snowflake_source_config():
    """Render Snowflake source configuration form."""
    
    col1, col2 = st.columns(2)
    
    with col1:
        account = st.text_input(
            "Account",
            help="Snowflake account identifier",
            placeholder="myaccount.us-east-1",
        )
        user = st.text_input(
            "User",
            help="Snowflake username",
        )
        database = st.text_input(
            "Database",
            help="Snowflake database name",
        )
    
    with col2:
        password = st.text_input(
            "Password",
            type="password",
            help="Snowflake password",
        )
        warehouse = st.text_input(
            "Warehouse",
            help="Snowflake warehouse name",
        )
        schema = st.text_input(
            "Schema",
            help="Snowflake schema name",
            value="PUBLIC",
        )
    
    # Store in session state
    st.session_state["source_config"] = {
        "type": "snowflake",
        "account": account,
        "user": user,
        "password": password,
        "warehouse": warehouse,
        "database": database,
        "schema": schema,
    }


def render_snowflake_target_config():
    """Render Snowflake target configuration form."""
    st.info("Using same Snowflake connection as source")


def render_fabric_target_config():
    """Render Fabric target configuration form."""
    st.info("Using same Fabric connection as source")


def discover_models(source_type: str) -> List[Dict[str, Any]]:
    """Discover available models from source."""
    
    if source_type == "Fabric":
        # Mock discovery for now
        return [
            {"name": "Sales_V1", "size_mb": 150, "last_modified": "2026-02-01"},
            {"name": "Finance_Main", "size_mb": 200, "last_modified": "2026-02-03"},
            {"name": "HR_Secure", "size_mb": 50, "last_modified": "2026-01-28"},
            {"name": "Marketing_Ops", "size_mb": 120, "last_modified": "2026-02-05"},
        ]
    elif source_type == "Snowflake":
        return [
            {"name": "SALES_MODEL", "size_mb": 180, "last_modified": "2026-02-02"},
            {"name": "INVENTORY_MODEL", "size_mb": 90, "last_modified": "2026-01-30"},
        ]
    
    return []


def render_model_selection(models: List[Dict[str, Any]]):
    """Render model selection interface."""
    
    st.markdown("#### Available Models")
    
    # Search/filter
    search = st.text_input("🔍 Search models", placeholder="Enter model name or pattern")
    
    # Filter models
    filtered_models = models
    if search:
        filtered_models = [
            m for m in models
            if search.lower() in m["name"].lower()
        ]
    
    # Display models in grid
    st.markdown(f"Found {len(filtered_models)} models")
    
    selected_models = []
    
    for model in filtered_models:
        col1, col2, col3, col4 = st.columns([1, 3, 2, 2])
        
        with col1:
            selected = st.checkbox(
                "Select",
                key=f"select_{model['name']}",
                label_visibility="collapsed",
            )
            if selected:
                selected_models.append(model["name"])
        
        with col2:
            st.markdown(f"**{model['name']}**")
        
        with col3:
            st.markdown(f"{model['size_mb']} MB")
        
        with col4:
            st.markdown(f"{model['last_modified']}")
    
    # Store selected models
    st.session_state["selected_models"] = selected_models
    
    if selected_models:
        st.success(f"Selected {len(selected_models)} models")


def generate_config() -> Dict[str, Any]:
    """Generate semabridge.yaml configuration."""
    
    source_config = st.session_state.get("source_config", {})
    selected_models = st.session_state.get("selected_models", [])
    
    config = {
        "source": {
            "type": source_config.get("type"),
            "workspace_id": source_config.get("workspace_id"),
        },
        "target": {
            "type": "snowflake",
            "deploy": True,
        },
        "models": selected_models,
        "model_name": "Generated Configuration",
        "logging": {
            "level": "INFO",
        },
    }
    
    return config


def render_config_preview(config: Dict[str, Any]):
    """Render configuration preview and save option."""
    
    st.markdown("#### Generated Configuration")
    
    # Display YAML
    yaml_str = yaml.dump(config, default_flow_style=False, sort_keys=False)
    st.code(yaml_str, language="yaml")
    
    # Save button
    col1, col2 = st.columns([1, 4])
    
    with col1:
        if st.button("💾 Save to Disk", type="primary"):
            save_config(config)
    
    with col2:
        st.markdown("*Configuration will be saved to current directory*")


def save_config(config: Dict[str, Any]):
    """Save configuration to semabridge.yaml."""
    
    try:
        output_path = Path.cwd() / "semabridge.yaml"
        
        with open(output_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        st.success(f"✓ Configuration saved to {output_path}")
    except Exception as e:
        st.error(f"Failed to save configuration: {e}")
```

---

## 4. CLI Integration

**File:** `src/semabridge/cli/main.py` (updated)

```python
"""
SemaBridge CLI with UI support.
"""

import typer
import subprocess
import sys
from pathlib import Path

app = typer.Typer()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    ui: bool = typer.Option(False, "--ui", help="Launch graphical user interface"),
    version: bool = typer.Option(False, "--version", help="Show version"),
):
    """
    SemaBridge - Semantic Model Synchronization Platform
    """
    
    if version:
        typer.echo("SemaBridge v1.0.0")
        raise typer.Exit()
    
    if ui:
        launch_ui()
        raise typer.Exit()
    
    # If no command and no flags, show help
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def launch_ui():
    """Launch Streamlit UI."""
    
    typer.echo("🌉 Launching SemaBridge UI...")
    
    # Find the UI app module
    try:
        import semabridge.ui.app as ui_app
        app_path = Path(ui_app.__file__)
        
        # Launch Streamlit
        subprocess.run([
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ])
        
    except Exception as e:
        typer.echo(f"❌ Failed to launch UI: {e}", err=True)
        raise typer.Exit(1)


# Add other commands here...
@app.command()
def sync():
    """Synchronize semantic models."""
    typer.echo("Running sync...")


if __name__ == "__main__":
    app()
```

---

## 5. Version Explorer Page

**File:** `src/semabridge/ui/pages/version_explorer.py`

```python
"""Version Explorer page for SemaBridge UI."""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import json

from semabridge.repository.duckdb_manager import DuckDBManager
from semabridge.repository.semantic_diff_engine import SemanticDiffEngine


def render():
    """Render version explorer page."""
    
    st.title("📚 Version Explorer")
    st.markdown("View and compare semantic model versions")
    
    # Initialize DuckDB connection
    try:
        db_manager = DuckDBManager()
        st.success("✓ Connected to version repository")
    except Exception as e:
        st.error(f"Failed to connect to repository: {e}")
        return
    
    # Tabs for different views
    tab1, tab2 = st.tabs(["Run History", "Version Comparison"])
    
    with tab1:
        render_run_history(db_manager)
    
    with tab2:
        render_version_comparison(db_manager)


def render_run_history(db_manager):
    """Render run history view."""
    
    st.markdown("### Sync Run History")
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        date_range = st.date_input(
            "Date Range",
            value=(datetime.now() - timedelta(days=30), datetime.now()),
        )
    
    with col2:
        status_filter = st.multiselect(
            "Status",
            ["Success", "Failed", "In Progress"],
            default=["Success", "Failed"],
        )
    
    with col3:
        model_filter = st.text_input("Model Name", placeholder="Filter by model name")
    
    # Fetch run history (mock data for now)
    runs = get_run_history(db_manager, date_range, status_filter, model_filter)
    
    # Display as table
    if runs:
        df = pd.DataFrame(runs)
        st.dataframe(
            df,
            use_container_width=True,
            column_config={
                "status": st.column_config.TextColumn(
                    "Status",
                    help="Run status",
                ),
                "timestamp": st.column_config.DatetimeColumn(
                    "Timestamp",
                    format="YYYY-MM-DD HH:mm:ss",
                ),
                "model_count": st.column_config.NumberColumn(
                    "Models",
                    help="Number of models processed",
                ),
            },
        )
    else:
        st.info("No runs found matching the filters")


def render_version_comparison(db_manager):
    """Render version comparison view."""
    
    st.markdown("### Compare Versions")
    
    # Get available versions
    versions = get_available_versions(db_manager)
    
    if len(versions) < 2:
        st.warning("Need at least 2 versions to compare")
        return
    
    # Version selection
    col1, col2 = st.columns(2)
    
    with col1:
        version_a = st.selectbox(
            "Version A (Older)",
            versions,
            format_func=lambda v: f"{v['tag']} - {v['timestamp']}",
        )
    
    with col2:
        version_b = st.selectbox(
            "Version B (Newer)",
            versions,
            format_func=lambda v: f"{v['tag']} - {v['timestamp']}",
            index=1 if len(versions) > 1 else 0,
        )
    
    # Compare button
    if st.button("🔍 Compare Versions", type="primary"):
        with st.spinner("Comparing versions..."):
            diff = compare_versions(db_manager, version_a, version_b)
            st.session_state["version_diff"] = diff
    
    # Display diff
    if "version_diff" in st.session_state:
        render_diff_view(st.session_state["version_diff"])


def render_diff_view(diff: dict):
    """Render side-by-side diff view."""
    
    st.markdown("### Differences")
    
    # Summary
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Added", diff.get("added_count", 0), delta=diff.get("added_count", 0))
    
    with col2:
        st.metric("Modified", diff.get("modified_count", 0))
    
    with col3:
        st.metric("Deleted", diff.get("deleted_count", 0), delta=-diff.get("deleted_count", 0))
    
    st.markdown("---")
    
    # Detailed changes
    if diff.get("changes"):
        for change in diff["changes"]:
            render_change_item(change)
    else:
        st.info("No differences found")


def render_change_item(change: dict):
    """Render a single change item."""
    
    change_type = change.get("type")
    object_type = change.get("object_type")
    object_name = change.get("object_name")
    
    # Color coding
    if change_type == "added":
        color = "green"
        icon = "➕"
    elif change_type == "deleted":
        color = "red"
        icon = "➖"
    else:
        color = "orange"
        icon = "🔄"
    
    with st.expander(f"{icon} {object_type}: {object_name}"):
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Before**")
            if change.get("old_value"):
                st.json(change["old_value"])
            else:
                st.markdown("*N/A*")
        
        with col2:
            st.markdown("**After**")
            if change.get("new_value"):
                st.json(change["new_value"])
            else:
                st.markdown("*N/A*")


def get_run_history(db_manager, date_range, status_filter, model_filter):
    """Fetch run history from DuckDB."""
    
    # Mock data for now
    return [
        {
            "run_id": "run_001",
            "timestamp": datetime.now() - timedelta(days=1),
            "status": "Success",
            "model_count": 15,
            "duration_seconds": 120,
        },
        {
            "run_id": "run_002",
            "timestamp": datetime.now() - timedelta(days=2),
            "status": "Success",
            "model_count": 12,
            "duration_seconds": 95,
        },
        {
            "run_id": "run_003",
            "timestamp": datetime.now() - timedelta(days=3),
            "status": "Failed",
            "model_count": 10,
            "duration_seconds": 45,
        },
    ]


def get_available_versions(db_manager):
    """Get list of available versions."""
    
    # Mock data for now
    return [
        {"tag": "v1.0", "timestamp": "2026-02-01 10:00:00"},
        {"tag": "v1.1", "timestamp": "2026-02-03 14:30:00"},
        {"tag": "v1.2", "timestamp": "2026-02-05 09:15:00"},
    ]


def compare_versions(db_manager, version_a, version_b):
    """Compare two versions and return diff."""
    
    # Mock diff for now
    return {
        "added_count": 5,
        "modified_count": 3,
        "deleted_count": 2,
        "changes": [
            {
                "type": "added",
                "object_type": "Column",
                "object_name": "CUSTOMER_SEGMENT",
                "old_value": None,
                "new_value": {"name": "CUSTOMER_SEGMENT", "type": "VARCHAR"},
            },
            {
                "type": "modified",
                "object_type": "Measure",
                "object_name": "Total Revenue",
                "old_value": {"expression": "SUM([Revenue])"},
                "new_value": {"expression": "SUM([Revenue]) * 1.1"},
            },
            {
                "type": "deleted",
                "object_type": "Table",
                "object_name": "LEGACY_SALES",
                "old_value": {"name": "LEGACY_SALES"},
                "new_value": None,
            },
        ],
    }
```

---

## 6. Testing Strategy

### 6.1 Build System Tests

```python
# tests/build/test_build_script.py

def test_build_script_exists():
    """Test that build script exists."""
    script_path = Path("scripts/build_exe.py")
    assert script_path.exists()

def test_build_produces_executable():
    """Test that build produces executable."""
    # Run build
    result = subprocess.run(
        ["python", "scripts/build_exe.py"],
        capture_output=True,
    )
    
    assert result.returncode == 0
    
    # Check executable exists
    exe_path = Path("dist/SemaBridge")
    assert exe_path.exists()
```

### 6.2 UI Tests

```python
# tests/ui/test_configuration_wizard.py

def test_configuration_wizard_renders():
    """Test that configuration wizard page renders."""
    from semabridge.ui.pages import configuration_wizard
    
    # Mock Streamlit
    with patch('streamlit.title'):
        configuration_wizard.render()

def test_model_discovery():
    """Test model discovery functionality."""
    models = discover_models("Fabric")
    
    assert len(models) > 0
    assert all("name" in m for m in models)
```

---

## 7. Deployment

### 7.1 Distribution

```bash
# Build for current platform
uv run python scripts/build_exe.py

# Distribute executable
# - Windows: dist/SemaBridge.exe
# - Linux: dist/SemaBridge
# - macOS: dist/SemaBridge
```

### 7.2 Installation

```bash
# No installation required - just run the executable
./SemaBridge --help

# Launch UI
./SemaBridge --ui
```

---

## 8. Future Enhancements

1. **Auto-update mechanism**: Check for updates and download new versions
2. **Custom themes**: Allow users to customize UI appearance
3. **Export reports**: Export diff reports as PDF/HTML
4. **Scheduled syncs**: Configure recurring sync jobs via UI
5. **Notifications**: Email/Slack notifications for sync completion
6. **Multi-user support**: Add authentication and user management
