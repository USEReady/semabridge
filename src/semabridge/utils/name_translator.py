import re

def get_target_deployment_name(display_name: str, platform: str = "") -> str:
    """
    Translates a human-readable display name into a strict, capitalized,
    platform-specific deployment name.
    
    Args:
        display_name: The semantic project name from the user config.
        platform: The target platform (e.g., 'snowflake', 'fabric').
        
    Returns:
        A deterministic, uppercase string safe for deployment.
    """
    # Replace spaces with underscores
    sanitized = display_name.replace(' ', '_')
    # Remove any non-alphanumeric/underscore characters
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', sanitized)
    # Capitalize
    capitalized = sanitized.upper()
    
    # Apply platform-specific suffixes if provided, to ensure multiple
    # models deployed from the same project get distinct names
    if platform:
        p_upper = platform.upper()
        if p_upper == "SNOWFLAKE":
            return f"{capitalized}_SEMANTIC"
        elif p_upper == "FABRIC":
            return f"{capitalized}_FB"
        elif p_upper == "DATABRICKS":
            return f"{capitalized}_DBX"
        else:
            return f"{capitalized}_{p_upper}"
            
    return capitalized
