import re
import hashlib


def get_target_deployment_name(display_name: str, platform: str = "", max_length: int = 255) -> str:
    """
    Translates a human-readable display name into a strict, capitalized,
    platform-specific deployment name.
    
    Args:
        display_name: The semantic project name from the user config.
        platform: The target platform (e.g., 'snowflake', 'fabric').
        
    Returns:
        A deterministic, uppercase string safe for deployment.
    """
    # Replace spaces with underscores and drop illegal chars
    sanitized = display_name.replace(" ", "_")
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', sanitized)
    capitalized = sanitized.upper()

    # Platform-specific suffix
    suffix = ""
    if platform:
        p_upper = platform.upper()
        if p_upper == "SNOWFLAKE":
            suffix = "_SEMANTIC"
        elif p_upper == "FABRIC":
            suffix = "_FB"
        elif p_upper == "DATABRICKS":
            suffix = "_DBX"
        else:
            suffix = f"_{p_upper}"

    # Enforce max_length (Snowflake identifiers limited to 255)
    name = f"{capitalized}{suffix}" if suffix else capitalized
    if len(name) <= max_length:
        return name

    # Truncate deterministically: keep a hashed suffix to maintain uniqueness
    hash_digest = hashlib.sha1(display_name.encode("utf-8")).hexdigest()[:6].upper()
    # Reserve space for underscore + hash + possible extra underscore + suffix
    reserved = len(suffix) + 1 + len(hash_digest)
    allowed_base = max_length - reserved
    if allowed_base <= 0:
        # Fallback: return truncated hash + suffix
        return f"{hash_digest}{suffix}"[:max_length]

    truncated = capitalized[:allowed_base]
    # Construct final name: TRUNCATED_<HASH><SUFFIX>
    final = f"{truncated}_{hash_digest}{suffix}"
    return final[:max_length]
