"""
SemaBridge Streamlit UI Application.

Launch with: semabridge --ui
"""

import streamlit as st
from pathlib import Path
import yaml

from semabridge.ui.pages import configuration_wizard, version_explorer
from semabridge.ui.ui_helpers import CUSTOM_CSS


def main():
    """Main UI application."""
    
    # Page configuration
    st.set_page_config(
        page_title="SemaBridge Control Center",
        page_icon="🌉",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    
    # Apply custom CSS for professional look
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    
    # Sidebar navigation
    st.sidebar.title("🌉 SemaBridge")
    st.sidebar.markdown("**Semantic Model Bridge**")
    st.sidebar.markdown("---")
    
    page = st.sidebar.radio(
        "Navigation",
        ["Configuration Wizard", "Version Explorer", "Settings"],
        label_visibility="collapsed",
    )
    
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        <div style="color: #6c757d; font-size: 0.8rem;">
        <b>SemaBridge v1.0</b><br>
        Semantic Model Synchronization Platform
        </div>
        """,
        unsafe_allow_html=True,
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
    
    # Try to load current config
    try:
        from semabridge.core.config_loader import get_project_file_path
        config_path = get_project_file_path("semabridge.yaml")
        if config_path.exists():
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            st.success(f"✓ Configuration loaded from {config_path}")
            st.json(config)
        else:
            st.info("No configuration file found in current directory.")
            st.markdown(
                "Use the **Configuration Wizard** to create a new configuration."
            )
    except Exception as e:
        st.error(f"Failed to load configuration: {e}")
    
    st.markdown("---")
    st.markdown("### About")
    st.markdown(
        """
        **SemaBridge** is a semantic model synchronization platform that enables 
        seamless conversion and deployment of semantic models across different 
        platforms (Microsoft Fabric, Snowflake, Databricks, etc.).
        
        **Features:**
        - Configuration wizard for easy setup
        - Version control and history tracking
        - Side-by-side diff comparison
        - Support for multiple connectors
        """
    )


if __name__ == "__main__":
    main()
