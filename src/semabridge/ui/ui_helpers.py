"""
UI Helper Utilities.

Shared utilities for the SemaBridge Streamlit UI.
"""

from typing import Any, Dict

# Professional color palette
COLORS = {
    "primary": "#0066cc",
    "primary_hover": "#0052a3",
    "success": "#28a745",
    "error": "#dc3545",
    "warning": "#ffc107",
    "info": "#17a2b8",
    "background": "#f5f7fa",
    "text_primary": "#1a1a1a",
    "text_secondary": "#6c757d",
    "border": "#dee2e6",
}

# Custom CSS for professional styling
CUSTOM_CSS = """
<style>
    .main {
        background-color: #f5f7fa;
    }
    
    .stButton>button {
        background-color: #0066cc;
        color: white;
        border-radius: 4px;
        padding: 0.5rem 1rem;
        border: none;
        font-weight: 500;
    }
    
    .stButton>button:hover {
        background-color: #0052a3;
    }
    
    h1, h2, h3 {
        color: #1a1a1a;
        font-weight: 600;
    }
    
    .stTextInput>div>div>input {
        border-radius: 4px;
    }
    
    .stSelectbox>div>div>select {
        border-radius: 4px;
    }
    
    .stDataFrame {
        border-radius: 4px;
    }
    
    /* Remove Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Professional card styling */
    .card {
        background: white;
        padding: 1.5rem;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        margin-bottom: 1rem;
    }
</style>
"""


def validate_credentials(credentials: Dict[str, Any], connector_type: str) -> bool:
    """
    Validate connector credentials.
    
    Args:
        credentials: Dictionary of credential key-value pairs
        connector_type: Type of connector (fabric, snowflake, etc.)
    
    Returns:
        True if credentials are valid, False otherwise
    """
    if connector_type == "fabric":
        required_fields = ["tenant_id", "client_id", "workspace_id", "client_secret"]
    elif connector_type == "snowflake":
        required_fields = ["account", "user", "password", "warehouse", "database"]
    else:
        return False
    
    # Check all required fields are present and non-empty
    for field in required_fields:
        value = credentials.get(field, "")
        if not value or not str(value).strip():
            return False
    
    return True


def format_file_size(size_mb: float) -> str:
    """
    Format file size for display.
    
    Args:
        size_mb: Size in megabytes
    
    Returns:
        Formatted size string
    """
    if size_mb < 1:
        return f"{size_mb * 1024:.1f} KB"
    elif size_mb < 1024:
        return f"{size_mb:.1f} MB"
    else:
        return f"{size_mb / 1024:.2f} GB"


def sanitize_model_name(name: str) -> str:
    """
    Sanitize model name for safe usage.
    
    Args:
        name: Model name
    
    Returns:
        Sanitized name
    """
    # Remove any potentially unsafe characters
    import re
    return re.sub(r'[^\w\-_\. ]', '', name)
