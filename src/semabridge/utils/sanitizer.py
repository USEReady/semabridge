"""
Module: sanitizer
Purpose: Normalize logical identifiers to safe Snowflake-compatible names.
Responsibilities:
- Transform arbitrary names into valid semantic identifiers.
- Guard against reserved keyword collisions in generated names.
"""

import re
from semabridge.utils.naming import SNOWFLAKE_RESERVED

def sanitize_sml_name(name: str) -> str:
    """
    Sanitize logical name for Snowflake SML YAML.
    1. Remove all double quotes.
    2. Replace any non-alphanumeric character (except _, $) with an underscore.
    3. Ensure the name starts with a letter or underscore.
    4. Append _LOGICAL if the name is a reserved SQL keyword like 'COLUMN', 'TABLE', or 'DATE'.
    """
    if not name:
        return "_EMPTY_LOGICAL"
        
    # 1. Remove all double quotes
    name = name.replace('"', "")
    
    # 2. Replace any non-alphanumeric character (except _, $) with an underscore
    name = re.sub(r'[^a-zA-Z0-9_$]', '_', name)
    
    # 3. Ensure the name starts with a letter or underscore
    if name and not re.match(r'^[a-zA-Z_]', name):
        name = '_' + name
        
    # 4. Append _LOGICAL if the name is a reserved SQL keyword
    reserved = {"COLUMN", "TABLE", "DATE"}
    if name.upper() in SNOWFLAKE_RESERVED or name.upper() in reserved:
        name = name + "_LOGICAL"
        
    return name
