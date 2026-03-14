"""Configuration Wizard page for SemaBridge UI."""

import streamlit as st
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional

from semabridge.ui.ui_helpers import validate_credentials, format_file_size


def render():
    """Render configuration wizard page."""
    
    st.title("📝 Configuration Wizard")
    st.markdown("Create a new sync configuration")
    
    # Initialize session state
    if "source_config" not in st.session_state:
        st.session_state["source_config"] = {}
    if "target_config" not in st.session_state:
        st.session_state["target_config"] = {}
    if "discovered_models" not in st.session_state:
        st.session_state["discovered_models"] = []
    if "selected_models" not in st.session_state:
        st.session_state["selected_models"] = []
    
    # Step 1: Source Configuration
    st.markdown("### Step 1: Source Configuration")
    st.markdown("Configure the source system to extract semantic models from.")
    
    source_type = st.selectbox(
        "Select Source System",
        ["Fabric", "Snowflake", "Databricks"],
        help="Choose the platform to extract semantic models from",
    )
    
    if source_type == "Fabric":
        render_fabric_source_config()
    elif source_type == "Snowflake":
        render_snowflake_source_config()
    elif source_type == "Databricks":
        st.info("Databricks connector coming soon!")
    
    st.markdown("---")
    
    # Step 2: Target Configuration
    st.markdown("### Step 2: Target Configuration")
    st.markdown("Configure the target system to deploy semantic models to.")
    
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
    st.markdown("Discover and select semantic models to synchronize.")
    
    col1, col2 = st.columns([1, 3])
    
    with col1:
        if st.button("🔍 Discover Models", type="primary", use_container_width=True):
            with st.spinner("Discovering models..."):
                models = discover_models(source_type)
                st.session_state["discovered_models"] = models
                if models:
                    st.success(f"Found {len(models)} models")
                else:
                    st.warning("No models found")
    
    with col2:
        if st.session_state["discovered_models"]:
            st.info(
                f"✓ Discovered {len(st.session_state['discovered_models'])} models. "
                "Select models below."
            )
    
    if st.session_state["discovered_models"]:
        render_model_selection(st.session_state["discovered_models"])
    
    st.markdown("---")
    
    # Step 4: Generate Configuration
    st.markdown("### Step 4: Generate Configuration")
    st.markdown("Review and save your configuration.")
    
    col1, col2 = st.columns([1, 3])
    
    with col1:
        if st.button("✨ Generate Configuration", type="primary", use_container_width=True):
            if st.session_state.get("selected_models"):
                config = generate_config(source_type, target_type)
                st.session_state["generated_config"] = config
            else:
                st.error("Please select at least one model")
    
    if "generated_config" in st.session_state:
        render_config_preview(st.session_state["generated_config"])


def render_fabric_source_config():
    """Render Fabric source configuration form."""
    
    st.markdown("#### Microsoft Fabric Connection")
    
    col1, col2 = st.columns(2)
    
    with col1:
        tenant_id = st.text_input(
            "Tenant ID",
            help="Azure AD Tenant ID",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            key="fabric_tenant_id",
        )
        client_id = st.text_input(
            "Client ID",
            help="Azure AD Application (Client) ID",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            key="fabric_client_id",
        )
    
    with col2:
        workspace_id = st.text_input(
            "Workspace ID",
            help="Fabric Workspace ID",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            key="fabric_workspace_id",
        )
        client_secret = st.text_input(
            "Client Secret",
            type="password",
            help="Azure AD Client Secret (will not be stored in plain text)",
            key="fabric_client_secret",
        )
    
    # Store in session state
    st.session_state["source_config"] = {
        "type": "fabric",
        "tenant_id": tenant_id,
        "client_id": client_id,
        "workspace_id": workspace_id,
        "client_secret": client_secret,
    }
    
    # Validate
    if all([tenant_id, client_id, workspace_id, client_secret]):
        is_valid = validate_credentials(st.session_state["source_config"], "fabric")
        if is_valid:
            st.success("✓ Credentials provided")
        else:
            st.warning("Please fill all required fields")


def render_snowflake_source_config():
    """Render Snowflake source configuration form."""
    
    st.markdown("#### Snowflake Connection")
    
    col1, col2 = st.columns(2)
    
    with col1:
        account = st.text_input(
            "Account",
            help="Snowflake account identifier (e.g., myaccount.us-east-1)",
            placeholder="myaccount.us-east-1",
            key="sf_account",
        )
        user = st.text_input(
            "User",
            help="Snowflake username",
            key="sf_user",
        )
        database = st.text_input(
            "Database",
            help="Snowflake database name",
            key="sf_database",
        )
    
    with col2:
        password = st.text_input(
            "Password",
            type="password",
            help="Snowflake password (will not be stored in plain text)",
            key="sf_password",
        )
        warehouse = st.text_input(
            "Warehouse",
            help="Snowflake warehouse name",
            key="sf_warehouse",
        )
        schema = st.text_input(
            "Schema",
            help="Snowflake schema name",
            value="PUBLIC",
            key="sf_schema",
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
    
    # Validate
    if all([account, user, password, warehouse, database]):
        is_valid = validate_credentials(st.session_state["source_config"], "snowflake")
        if is_valid:
            st.success("✓ Credentials provided")
        else:
            st.warning("Please fill all required fields")


def render_snowflake_target_config():
    """Render Snowflake target configuration form."""
    
    st.markdown("#### Snowflake Target")
    
    use_same = st.checkbox(
        "Use same connection as source",
        value=True,
        help="Use the same Snowflake credentials as the source",
    )
    
    if use_same:
        st.info("Using same Snowflake connection as source")
        st.session_state["target_config"] = st.session_state.get("source_config", {})
    else:
        st.warning("Separate target configuration not yet implemented")


def render_fabric_target_config():
    """Render Fabric target configuration form."""
    
    st.markdown("#### Microsoft Fabric Target")
    
    use_same = st.checkbox(
        "Use same connection as source",
        value=True,
        help="Use the same Fabric credentials as the source",
    )
    
    if use_same:
        st.info("Using same Fabric connection as source")
        st.session_state["target_config"] = st.session_state.get("source_config", {})
    else:
        st.warning("Separate target configuration not yet implemented")


def discover_models(source_type: str) -> List[Dict[str, Any]]:
    """
    Discover available models from source.
    
    Args:
        source_type: Type of source system
    
    Returns:
        List of discovered models with metadata
    """
    
    # TODO: Integrate with actual connectors
    # For now, return mock data based on source type
    
    if source_type == "Fabric":
        return [
            {"name": "Sales_V1", "size_mb": 150.5, "last_modified": "2026-02-01"},
            {"name": "Finance_Main", "size_mb": 220.3, "last_modified": "2026-02-03"},
            {"name": "HR_Secure", "size_mb": 48.7, "last_modified": "2026-01-28"},
            {"name": "Marketing_Ops", "size_mb": 125.9, "last_modified": "2026-02-05"},
            {"name": "Inventory_Analytics", "size_mb": 89.2, "last_modified": "2026-02-04"},
        ]
    elif source_type == "Snowflake":
        return [
            {"name": "SALES_MODEL", "size_mb": 180.0, "last_modified": "2026-02-02"},
            {"name": "INVENTORY_MODEL", "size_mb": 95.5, "last_modified": "2026-01-30"},
            {"name": "CUSTOMER_360", "size_mb": 310.8, "last_modified": "2026-02-06"},
        ]
    
    return []


def render_model_selection(models: List[Dict[str, Any]]):
    """
    Render model selection interface.
    
    Args:
        models: List of discovered models
    """
    
    st.markdown("#### Available Models")
    
    # Search/filter
    col1, col2 = st.columns([2, 1])
    
    with col1:
        search = st.text_input(
            "🔍 Search models",
            placeholder="Enter model name or pattern",
            key="model_search",
        )
    
    with col2:
        sort_by = st.selectbox(
            "Sort by",
            ["Name", "Size", "Last Modified"],
            key="sort_by",
        )
    
    # Filter models
    filtered_models = models
    if search:
        filtered_models = [
            m for m in models
            if search.lower() in m["name"].lower()
        ]
    
    # Sort models
    if sort_by == "Size":
        filtered_models = sorted(filtered_models, key=lambda x: x["size_mb"], reverse=True)
    elif sort_by == "Last Modified":
        filtered_models = sorted(filtered_models, key=lambda x: x["last_modified"], reverse=True)
    else:  # Name
        filtered_models = sorted(filtered_models, key=lambda x: x["name"])
    
    # Display count
    st.markdown(f"**{len(filtered_models)}** models found")
    
    # Display models in grid with header
    header_col1, header_col2, header_col3, header_col4 = st.columns([0.5, 3, 1.5, 1.5])
    with header_col1:
        st.markdown("**Select**")
    with header_col2:
        st.markdown("**Model Name**")
    with header_col3:
        st.markdown("**Size**")
    with header_col4:
        st.markdown("**Last Modified**")
    
    st.markdown("---")
    
    # Track selected models
    selected_models = []
    
    for idx, model in enumerate(filtered_models):
        col1, col2, col3, col4 = st.columns([0.5, 3, 1.5, 1.5])
        
        with col1:
            selected = st.checkbox(
                "Select",
                key=f"select_{model['name']}_{idx}",
                label_visibility="collapsed",
            )
            if selected:
                selected_models.append(model["name"])
        
        with col2:
            st.markdown(f"**{model['name']}**")
        
        with col3:
            st.markdown(format_file_size(model["size_mb"]))
        
        with col4:
            st.markdown(model["last_modified"])
    
    # Store selected models
    st.session_state["selected_models"] = selected_models
    
    if selected_models:
        st.success(f"✓ Selected {len(selected_models)} model(s): {', '.join(selected_models[:3])}{'...' if len(selected_models) > 3 else ''}")


def generate_config(source_type: str, target_type: str) -> Dict[str, Any]:
    """
    Generate semabridge.yaml configuration.
    
    Args:
        source_type: Type of source system
        target_type: Type of target system
    
    Returns:
        Configuration dictionary
    """
    
    source_config = st.session_state.get("source_config", {})
    selected_models = st.session_state.get("selected_models", [])
    
    # Build configuration
    config = {
        "model_name": "SemaBridge Configuration",
        "source": {
            "type": source_config.get("type", source_type.lower()),
        },
        "target": {
            "type": target_type.lower(),
            "deploy": True,
        },
        "models": selected_models if selected_models else ["*"],
        "logging": {
            "level": "INFO",
        },
    }
    
    # Add source-specific config (excluding secrets)
    if source_type == "Fabric":
        config["source"].update({
            "workspace_id_env": "FABRIC_WORKSPACE_ID",
            "tenant_id_env": "FABRIC_TENANT_ID",
            "client_id_env": "FABRIC_CLIENT_ID",
            "client_secret_env": "FABRIC_CLIENT_SECRET",
        })
    elif source_type == "Snowflake":
        config["source"].update({
            "account_env": "SNOWFLAKE_ACCOUNT",
            "user_env": "SNOWFLAKE_USER",
            "password_env": "SNOWFLAKE_PASSWORD",
            "warehouse_env": "SNOWFLAKE_WAREHOUSE",
            "database_env": "SNOWFLAKE_DATABASE",
            "schema": source_config.get("schema", "PUBLIC"),
        })
    
    return config


def render_config_preview(config: Dict[str, Any]):
    """
    Render configuration preview and save option.
    
    Args:
        config: Configuration dictionary
    """
    
    st.markdown("#### Generated Configuration")
    
    # Display YAML
    yaml_str = yaml.dump(config, default_flow_style=False, sort_keys=False)
    st.code(yaml_str, language="yaml")
    
    # Important note about environment variables
    st.info(
        "ℹ️ **Note:** Credentials are referenced as environment variables (ending in `_env`). "
        "You'll need to set these environment variables before running the sync."
    )
    
    # Save button
    col1, col2, col3 = st.columns([1, 1, 3])
    
    with col1:
        if st.button("💾 Save to Disk", type="primary", use_container_width=True):
            save_config(config)
    
    with col2:
        if st.button("📋 Copy YAML", use_container_width=True):
            st.code(yaml_str, language="yaml")
            st.info("Copy the YAML above and save manually")


def save_config(config: Dict[str, Any]):
    """
    Save configuration to semabridge.yaml.
    
    Args:
        config: Configuration dictionary
    """
    
    try:
        output_path = Path.cwd() / "semabridge.yaml"
        
        with open(output_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        st.success(f"✓ Configuration saved to `{output_path}`")
        
        # Show next steps
        st.markdown("#### Next Steps")
        st.markdown(
            """
            1. Set environment variables for your credentials
            2. Run `semabridge semantic-sync` to start synchronization
            3. Check version history in the Version Explorer
            """
        )
    except Exception as e:
        st.error(f"❌ Failed to save configuration: {e}")
