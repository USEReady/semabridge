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
    from semabridge.utils.identifiers import IdentifierSanitizer, clean_pbix_model_name

    stem = clean_pbix_model_name(pbix_path) or Path(pbix_path).stem
    sanitizer = IdentifierSanitizer()
    table_safe = sanitizer.sanitize_table_name(stem)
    return get_target_deployment_name(table_safe, platform)


def resolve_pbix_deployment_base_names(pbix_paths: List[str]) -> Dict[str, str]:
    """Maps each PBIX path in a batch to its own clean, sanitized BASE name
    (the pre-platform-suffix name pbix.py's _convert_pbix_to_sml sets as
    OSIModel/SMLModel.unique_name -- get_target_deployment_name's "_SEMANTIC"
    /"_FB"/"_DBX" suffix is applied later, at DDL-emission time).

    Every file gets its own clean name by default (matching demo_version's
    single-file behavior: no leading hash/prefix). Disambiguation only
    kicks in when two files in THIS batch would otherwise produce the
    identical base name -- e.g. two different reports both saved as
    "Sales.pbix", or two PBIX files whose internal model happens to share a
    display name. The first occurrence (input order) keeps the clean,
    unsuffixed name; later occurrences get a short, readable "_2", "_3", ...
    suffix -- never a long UUID/hash, and never a hard validation error.

    This mirrors the collision-handling shape already used elsewhere in this
    codebase (see dimensions_clause_builder.py's alias _2/_3 fallback): try
    the base name first, only disambiguate when a genuine collision is
    actually detected.
    """
    from semabridge.utils.identifiers import IdentifierSanitizer, clean_pbix_model_name

    sanitizer = IdentifierSanitizer()
    base_names = [
        sanitizer.sanitize_table_name(clean_pbix_model_name(path) or Path(path).stem)
        for path in pbix_paths
    ]

    counts: Dict[str, int] = {}
    for name in base_names:
        counts[name] = counts.get(name, 0) + 1

    occurrence: Dict[str, int] = {}
    resolved: Dict[str, str] = {}
    for path, base_name in zip(pbix_paths, base_names):
        if counts[base_name] == 1:
            resolved[path] = base_name
            continue
        occurrence[base_name] = occurrence.get(base_name, 0) + 1
        n = occurrence[base_name]
        resolved[path] = base_name if n == 1 else f"{base_name}_{n}"
    return resolved


def find_pbix_view_name_collisions(
    pbix_paths: List[str], platform: str = "snowflake"
) -> Dict[str, List[str]]:
    """
    Groups PBIX file paths by the deployment view name they would each produce,
    returning only groups with more than one file — i.e. genuine collisions.

    Read-only diagnostic helper (e.g. for logging/telemetry) — actual
    disambiguation for a batch of PBIX files is resolve_pbix_deployment_base_names(),
    which auto-suffixes colliding names instead of erroring, so this function
    itself no longer gates project creation, dry-run, or deploy requests.

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
