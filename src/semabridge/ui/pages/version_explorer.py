"""Version Explorer page for SemaBridge UI."""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import json
from typing import List, Dict, Any, Optional


def render():
    """Render version explorer page."""
    
    st.title("📚 Version Explorer")
    st.markdown("View and compare semantic model versions")
    
    # Initialize DuckDB connection (placeholder)
    try:
        st.success("✓ Connected to version repository")
        db_connected = True
    except Exception as e:
        st.error(f"Failed to connect to repository: {e}")
        db_connected = False
        return
    
    # Tabs for different views
    tab1, tab2 = st.tabs(["Run History", "Version Comparison"])
    
    with tab1:
        render_run_history()
    
    with tab2:
        render_version_comparison()


def render_run_history():
    """Render run history view."""
    
    st.markdown("### Sync Run History")
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        date_range = st.date_input(
            "Date Range",
            value=(datetime.now() - timedelta(days=30), datetime.now()),
            help="Filter runs by date range",
        )
    
    with col2:
        status_filter = st.multiselect(
            "Status",
            ["Success", "Failed", "In Progress"],
            default=["Success", "Failed"],
            help="Filter by run status",
        )
    
    with col3:
        model_filter = st.text_input(
            "Model Name",
            placeholder="Filter by model name",
            help="Search for specific models",
        )
    
    # Fetch run history (mock data for now)
    runs = get_run_history(date_range, status_filter, model_filter)
    
    # Display as table
    if runs:
        df = pd.DataFrame(runs)
        
        st.markdown(f"**{len(runs)}** runs found")
        
        # Configure column display
        st.dataframe(
            df,
            use_container_width=True,
            column_config={
                "run_id": st.column_config.TextColumn(
                    "Run ID",
                    help="Unique run identifier",
                    width="small",
                ),
                "timestamp": st.column_config.DatetimeColumn(
                    "Timestamp",
                    format="YYYY-MM-DD HH:mm:ss",
                    width="medium",
                ),
                "status": st.column_config.TextColumn(
                    "Status",
                    help="Run status",
                    width="small",
                ),
                "model_count": st.column_config.NumberColumn(
                    "Models",
                    help="Number of models processed",
                    width="small",
                ),
                "source": st.column_config.TextColumn(
                    "Source",
                    help="Source system",
                    width="small",
                ),
                "target": st.column_config.TextColumn(
                    "Target",
                    help="Target system",
                    width="small",
                ),
            },
            hide_index=True,
        )
        
        # Export option
        if st.button("📥 Export to CSV"):
            csv = df.to_csv(index=False)
            st.download_button(
                label="Download CSV",
                data=csv,
                file_name=f"semabridge_history_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )
    else:
        st.info("No runs found matching the filters")


def render_version_comparison():
    """Render version comparison view."""
    
    st.markdown("### Compare Versions")
    
    # Get available versions
    versions = get_available_versions()
    
    if len(versions) < 2:
        st.warning("⚠️ Need at least 2 versions to compare")
        st.info("Run a sync operation to create version history")
        return
    
    # Version selection
    col1, col2 = st.columns(2)
    
    with col1:
        version_a_idx = st.selectbox(
            "Version A (Older)",
            range(len(versions)),
            format_func=lambda i: format_version_label(versions[i]),
            help="Select the older version to compare",
        )
        version_a = versions[version_a_idx]
    
    with col2:
        version_b_idx = st.selectbox(
            "Version B (Newer)",
            range(len(versions)),
            format_func=lambda i: format_version_label(versions[i]),
            index=min(1, len(versions) - 1),
            help="Select the newer version to compare",
        )
        version_b = versions[version_b_idx]
    
    # Compare button
    if st.button("🔍 Compare Versions", type="primary"):
        if version_a_idx == version_b_idx:
            st.error("Please select two different versions to compare")
        else:
            with st.spinner("Comparing versions..."):
                diff = compare_versions(version_a, version_b)
                st.session_state["current_diff"] = diff
    
    # Display diff if available
    if "current_diff" in st.session_state:
        render_diff_view(st.session_state["current_diff"], version_a, version_b)


def format_version_label(version: Dict[str, Any]) -> str:
    """
    Format version for display in dropdown.
    
    Args:
        version: Version dictionary
    
    Returns:
        Formatted label string
    """
    return f"{version['tag']} - {version['timestamp']} ({version['model_count']} models)"


def render_diff_view(diff: Dict[str, Any], version_a: Dict[str, Any], version_b: Dict[str, Any]):
    """
    Render side-by-side diff view.
    
    Args:
        diff: Diff data
        version_a: Older version
        version_b: Newer version
    """
    
    st.markdown("---")
    st.markdown("### Comparison Results")
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Additions", diff["summary"]["additions"], delta=None)
    
    with col2:
        st.metric("Deletions", diff["summary"]["deletions"], delta=None)
    
    with col3:
        st.metric("Modifications", diff["summary"]["modifications"], delta=None)
    
    with col4:
        total_changes = sum(diff["summary"].values())
        st.metric("Total Changes", total_changes, delta=None)
    
    st.markdown("---")
    
    # Detailed changes
    if diff["changes"]:
        st.markdown("#### Detailed Changes")
        
        # Filter options
        change_type_filter = st.multiselect(
            "Filter by change type",
            ["Addition", "Deletion", "Modification"],
            default=["Addition", "Deletion", "Modification"],
        )
        
        # Display changes
        for change in diff["changes"]:
            if change["type"] not in change_type_filter:
                continue
            
            render_change_item(change)
    else:
        st.info("No differences found between the selected versions")
    
    # Export option
    st.markdown("---")
    col1, col2 = st.columns([1, 3])
    
    with col1:
        if st.button("📥 Export Diff Report", use_container_width=True):
            export_diff_report(diff, version_a, version_b)


def render_change_item(change: Dict[str, Any]):
    """
    Render a single change item.
    
    Args:
        change: Change dictionary
    """
    
    # Color coding based on change type
    if change["type"] == "Addition":
        color = "#d4edda"  # Light green
        icon = "➕"
        border_color = "#28a745"
    elif change["type"] == "Deletion":
        color = "#f8d7da"  # Light red
        icon = "➖"
        border_color = "#dc3545"
    else:  # Modification
        color = "#fff3cd"  # Light yellow
        icon = "✏️"
        border_color = "#ffc107"
    
    # Render change card
    st.markdown(
        f"""
        <div style="background-color: {color}; padding: 1rem; border-radius: 4px; 
                    border-left: 4px solid {border_color}; margin-bottom: 1rem;">
            <strong>{icon} {change['type']}: {change['path']}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )
    
    # Show details in expandable section
    with st.expander("View Details"):
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


def get_run_history(
    date_range: tuple,
    status_filter: List[str],
    model_filter: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Fetch run history from repository.
    
    Args:
        date_range: Tuple of (start_date, end_date)
        status_filter: List of status values to include
        model_filter: Optional model name filter
    
    Returns:
        List of run records
    """
    
    # TODO: Integrate with actual DuckDB repository
    # For now, return mock data
    
    mock_runs = [
        {
            "run_id": "run_20260206_001",
            "timestamp": datetime(2026, 2, 6, 14, 30, 0),
            "status": "Success",
            "model_count": 5,
            "source": "Fabric",
            "target": "Snowflake",
        },
        {
            "run_id": "run_20260205_002",
            "timestamp": datetime(2026, 2, 5, 10, 15, 0),
            "status": "Success",
            "model_count": 3,
            "source": "Snowflake",
            "target": "Fabric",
        },
        {
            "run_id": "run_20260204_001",
            "timestamp": datetime(2026, 2, 4, 16, 45, 0),
            "status": "Failed",
            "model_count": 2,
            "source": "Fabric",
            "target": "Snowflake",
        },
        {
            "run_id": "run_20260203_003",
            "timestamp": datetime(2026, 2, 3, 9, 0, 0),
            "status": "Success",
            "model_count": 7,
            "source": "Fabric",
            "target": "Snowflake",
        },
    ]
    
    # Apply filters
    filtered_runs = mock_runs
    
    # Status filter
    if status_filter:
        filtered_runs = [r for r in filtered_runs if r["status"] in status_filter]
    
    # Date filter
    if len(date_range) == 2:
        start_date, end_date = date_range
        filtered_runs = [
            r for r in filtered_runs
            if start_date <= r["timestamp"].date() <= end_date
        ]
    
    # Model filter
    if model_filter:
        # In real implementation, this would filter by model name
        pass
    
    return filtered_runs


def get_available_versions() -> List[Dict[str, Any]]:
    """
    Get available versions for comparison.
    
    Returns:
        List of version records
    """
    
    # TODO: Integrate with actual DuckDB repository
    # For now, return mock data
    
    return [
        {
            "tag": "v1.0.3",
            "timestamp": "2026-02-06 14:30",
            "model_count": 5,
            "run_id": "run_20260206_001",
        },
        {
            "tag": "v1.0.2",
            "timestamp": "2026-02-05 10:15",
            "model_count": 3,
            "run_id": "run_20260205_002",
        },
        {
            "tag": "v1.0.1",
            "timestamp": "2026-02-03 09:00",
            "model_count": 7,
            "run_id": "run_20260203_003",
        },
    ]


def compare_versions(
    version_a: Dict[str, Any],
    version_b: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compare two versions and generate diff.
    
    Args:
        version_a: Older version
        version_b: Newer version
    
    Returns:
        Diff data
    """
    
    # TODO: Integrate with actual SemanticDiffEngine
    # For now, return mock diff data
    
    return {
        "summary": {
            "additions": 3,
            "deletions": 1,
            "modifications": 2,
        },
        "changes": [
            {
                "type": "Addition",
                "path": "models.Sales_V1.tables.ProductDim",
                "old_value": None,
                "new_value": {
                    "name": "ProductDim",
                    "columns": ["ProductID", "ProductName", "Category"],
                },
            },
            {
                "type": "Modification",
                "path": "models.Finance_Main.relationships",
                "old_value": {"count": 5},
                "new_value": {"count": 7},
            },
            {
                "type": "Deletion",
                "path": "models.Old_Model",
                "old_value": {"name": "Old_Model", "status": "deprecated"},
                "new_value": None,
            },
            {
                "type": "Addition",
                "path": "models.Marketing_Ops.measures.RevenueGrowth",
                "old_value": None,
                "new_value": {
                    "name": "RevenueGrowth",
                    "expression": "SUM([Revenue]) / SUM([PrevRevenue]) - 1",
                },
            },
            {
                "type": "Modification",
                "path": "models.HR_Secure.security.roles",
                "old_value": {"roles": ["Admin", "Viewer"]},
                "new_value": {"roles": ["Admin", "Viewer", "Analyst"]},
            },
        ],
    }


def export_diff_report(
    diff: Dict[str, Any],
    version_a: Dict[str, Any],
    version_b: Dict[str, Any],
):
    """
    Export diff report as JSON.
    
    Args:
        diff: Diff data
        version_a: Older version
        version_b: Newer version
    """
    
    report = {
        "comparison": {
            "version_a": version_a,
            "version_b": version_b,
            "timestamp": datetime.now().isoformat(),
        },
        "diff": diff,
    }
    
    report_json = json.dumps(report, indent=2)
    
    st.download_button(
        label="Download JSON Report",
        data=report_json,
        file_name=f"diff_{version_a['tag']}_to_{version_b['tag']}.json",
        mime="application/json",
    )
    
    st.success("✓ Diff report ready for download")
