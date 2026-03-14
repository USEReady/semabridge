"""
Configuration Schema Definition.

Defines the strict schema for semabridge.yaml configuration files.
All valid keys, types, and validation rules are centralized here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
from difflib import get_close_matches


@dataclass
class FieldSchema:
    """Schema definition for a single field."""
    required: bool = False
    field_type: Literal["string", "boolean", "integer", "object", "array", "any"] = "any"
    enum: Optional[Tuple[str, ...]] = None
    fields: Optional[Dict[str, "FieldSchema"]] = None
    description: str = ""
    env_var_only: bool = False  # If True, only env var references allowed
    deprecated: bool = False
    deprecated_message: str = ""
    example: str = ""


# ============================================================================
# SCHEMA DEFINITION
# ============================================================================

SOURCE_SCHEMA = FieldSchema(
    required=True,
    field_type="object",
    description="Source connector configuration",
    fields={
        "type": FieldSchema(
            required=True,
            field_type="string",
            enum=("snowflake", "fabric", "snowflake_semantic_view", "local_pbix"),
            description="Source connector type",
            example="type: snowflake",
        ),
        "dataset_id": FieldSchema(
            required=False,
            field_type="string",
            description="Fabric dataset ID (required for fabric source)",
            example="dataset_id: abc123-def456",
        ),
        "workspace_id": FieldSchema(
            required=False,
            field_type="string",
            description="Fabric workspace ID",
        ),
        "database": FieldSchema(
            required=False,
            field_type="string",
            description="Snowflake database name",
        ),
        "schema_name": FieldSchema(
            required=False,
            field_type="string",
            description="Snowflake schema name",
        ),
    },
)

TARGET_SCHEMA = FieldSchema(
    required=False,
    field_type="object",
    description="Target connector configuration",
    fields={
        "type": FieldSchema(
            required=True,
            field_type="string",
            enum=("snowflake", "fabric", "snowflake_semantic_view"),
            description="Target connector type",
            example="type: fabric",
        ),
        "deploy": FieldSchema(
            required=False,
            field_type="boolean",
            description="Whether to deploy to target",
            example="deploy: true",
        ),
        "workspace_id": FieldSchema(
            required=False,
            field_type="string",
            description="Fabric workspace ID for target",
        ),
        "database": FieldSchema(
            required=False,
            field_type="string",
            description="Snowflake database for target",
        ),
        "schema_name": FieldSchema(
            required=False,
            field_type="string",
            description="Snowflake schema for target",
        ),
    },
)

SYNC_SCHEMA = FieldSchema(
    required=False,
    field_type="object",
    description="Sync configuration",
    fields={
        "direction": FieldSchema(
            required=True,
            field_type="string",
            enum=("source_to_target", "target_to_source"),
            description="Direction of synchronization",
            example="direction: source_to_target",
        ),
    },
)

AUTH_SCHEMA = FieldSchema(
    required=False,
    field_type="object",
    description="Authentication configuration (env var references only)",
    fields={
        "username_env": FieldSchema(
            required=False,
            field_type="string",
            description="Environment variable name for username",
            example="username_env: SEMABRIDGE_USERNAME",
        ),
        "password_env": FieldSchema(
            required=False,
            field_type="string",
            description="Environment variable name for password",
            example="password_env: SEMABRIDGE_PASSWORD",
        ),
    },
)

LOGGING_SCHEMA = FieldSchema(
    required=False,
    field_type="object",
    description="Logging configuration",
    fields={
        "level": FieldSchema(
            required=False,
            field_type="string",
            enum=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
            description="Logging level",
            example="level: INFO",
        ),
        "format": FieldSchema(
            required=False,
            field_type="string",
            enum=("text", "json"),
            description="Log output format",
            example="format: text",
        ),
    },
)

# Top-level schema
CONFIG_SCHEMA: Dict[str, FieldSchema] = {
    "source": SOURCE_SCHEMA,
    "target": TARGET_SCHEMA,
    "sync": SYNC_SCHEMA,
    "sync_direction": FieldSchema(
        required=False,
        field_type="string",
        enum=("source_to_target", "target_to_source"),
        description="Sync direction (alternative to sync.direction)",
        example="sync_direction: source_to_target",
    ),
    "auth": AUTH_SCHEMA,
    "logging": LOGGING_SCHEMA,
    "model_name": FieldSchema(
        required=True,
        field_type="string",
        description="Name of the semantic model",
        example="model_name: CustomerProfitability",
    ),
    "version_tag": FieldSchema(
        required=False,
        field_type="string",
        description="Version tag for this run",
        example="version_tag: v1.0.0",
    ),
    "policy_path": FieldSchema(
        required=False,
        field_type="string",
        description="Path to behavior policy YAML file",
        example="policy_path: policies/production.yaml",
    ),
}

# Sensitive field patterns (for detecting plain-text credentials)
SENSITIVE_PATTERNS = [
    "password", "secret", "credential", "token", "api_key",
    "client_secret", "private_key"
]


def get_all_known_keys(
    schema: Dict[str, FieldSchema],
    prefix: str = "",
) -> Set[str]:
    """
    Get all known keys from schema (flattened with dot notation).
    
    Returns:
        Set of valid key paths like {"source", "source.type", "logging.level"}
    """
    keys = set()
    for key, field_schema in schema.items():
        full_key = f"{prefix}.{key}" if prefix else key
        keys.add(full_key)
        
        if field_schema.fields:
            keys.update(get_all_known_keys(field_schema.fields, full_key))
    
    return keys


# Pre-computed set of all known keys for quick lookup
ALL_KNOWN_KEYS = get_all_known_keys(CONFIG_SCHEMA)


def suggest_similar_key(unknown_key: str, known_keys: Set[str] = None) -> Optional[str]:
    """
    Suggest a similar known key for a typo.
    
    Args:
        unknown_key: The unknown key that was found
        known_keys: Set of valid keys (defaults to ALL_KNOWN_KEYS)
        
    Returns:
        Suggested key if similar, None otherwise
    """
    if known_keys is None:
        known_keys = ALL_KNOWN_KEYS
    
    # Get close matches
    matches = get_close_matches(unknown_key, known_keys, n=1, cutoff=0.6)
    return matches[0] if matches else None


def get_schema_for_path(path: str) -> Optional[FieldSchema]:
    """
    Get the schema definition for a given path.
    
    Args:
        path: Dot-separated path like "source.type" or "logging.level"
        
    Returns:
        FieldSchema if found, None otherwise
    """
    parts = path.split(".")
    current_schema = CONFIG_SCHEMA
    
    for i, part in enumerate(parts):
        if part not in current_schema:
            return None
        
        field_schema = current_schema[part]
        
        if i == len(parts) - 1:
            return field_schema
        
        if field_schema.fields:
            current_schema = field_schema.fields
        else:
            return None
    
    return None


def get_known_keys_at_level(parent_path: str = "") -> Set[str]:
    """
    Get all valid keys at a specific level in the schema.
    
    Args:
        parent_path: Parent path (empty for root level)
        
    Returns:
        Set of valid keys at that level
    """
    if not parent_path:
        return set(CONFIG_SCHEMA.keys())
    
    schema = get_schema_for_path(parent_path)
    if schema and schema.fields:
        return set(schema.fields.keys())
    
    return set()
