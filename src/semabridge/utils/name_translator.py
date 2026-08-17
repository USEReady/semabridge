import re
from pathlib import Path
from typing import Dict, List

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


def get_pbix_deployment_view_name(pbix_path: str, platform: str = "snowflake") -> str:
    """
    Derives the deployment view name for a single PBIX file from its OWN
    filename — never from the project name. This is what gives each file in a
    multi-PBIX project its own independent Snowflake semantic view.

    Reuses two already-proven utilities rather than inventing a new sanitizer:
      1. IdentifierSanitizer.sanitize_table_name() — handles leading digits,
         illegal characters, uppercasing (things get_target_deployment_name's
         own regex-strip doesn't handle, e.g. a filename starting with a digit).
      2. get_target_deployment_name() — the existing display-name -> deployment
         -name translation, applied here to the sanitized filename stem instead
         of a project name.

    Args:
        pbix_path: Full path (or bare filename) to the .pbix file.
        platform: Target platform, passed straight through to
            get_target_deployment_name (default "snowflake").

    Returns:
        The deployment view name, e.g. "C:/Reports/Sales Report.pbix" ->
        "SALES_REPORT_SEMANTIC" (platform="snowflake").
    """
    from semabridge.utils.identifiers import IdentifierSanitizer

    stem = Path(pbix_path).stem
    sanitizer = IdentifierSanitizer()
    table_safe = sanitizer.sanitize_table_name(stem)
    return get_target_deployment_name(table_safe, platform)


def find_pbix_view_name_collisions(
    pbix_paths: List[str], platform: str = "snowflake"
) -> Dict[str, List[str]]:
    """
    Groups PBIX file paths by the deployment view name they would each produce,
    returning only groups with more than one file — i.e. genuine collisions.

    This is a pure function with no side effects and no dependency on the
    request boundary it's called from, so it can (and should) be called from
    more than one place — see validate_no_pbix_view_name_collisions() and its
    two call sites (project-configuration time and job-construction time).

    Args:
        pbix_paths: Full paths to the configured .pbix files.
        platform: Target platform for name derivation (default "snowflake").

    Returns:
        Dict of {view_name: [colliding_paths]} — empty if no collisions.
    """
    by_view_name: Dict[str, List[str]] = {}
    for path in pbix_paths:
        view_name = get_pbix_deployment_view_name(path, platform)
        by_view_name.setdefault(view_name, []).append(path)
    return {name: paths for name, paths in by_view_name.items() if len(paths) > 1}


def validate_no_pbix_view_name_collisions(pbix_paths: List[str], platform: str = "snowflake") -> None:
    """
    Raises ValidationError if any two configured PBIX files would deploy to the
    same Snowflake (or other platform) semantic view name.

    Deliberately does NOT auto-suffix a disambiguated name — consistent with
    this codebase's "fail visibly, never silently guess" principle (see e.g.
    the CAST(NULL AS DOUBLE) sentinel fix and the anchor_flag_map wiring fix).
    A silent auto-suffix risks masking a genuine user mistake (e.g. the same
    report exported twice and selected twice) as if it were an intentional,
    correctly-named deploy. The user must see the collision and rename one of
    the files (or its configured display name) themselves.
    """
    from semabridge.domain.exceptions import ValidationError

    collisions = find_pbix_view_name_collisions(pbix_paths, platform)
    if not collisions:
        return

    parts = []
    for view_name, paths in collisions.items():
        filenames = ", ".join(f"'{Path(p).name}'" for p in paths)
        parts.append(f"{filenames} would all deploy to the same view name '{view_name}'")
    detail = "; ".join(parts)
    raise ValidationError(
        f"Naming collision detected: {detail}. Rename one of these files (or its "
        "display name) so each produces a distinct semantic view name, then retry."
    )
