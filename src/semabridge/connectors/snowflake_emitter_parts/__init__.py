"""Snowflake Emitter Parts Module.

This package organizes the large SnowflakeEmitter class into focused,
reusable modules for better maintainability.

Modules:
--------

- **table_management**: Table creation, column collection, schema validation
  - collect_physical_source_columns()
  - collect_physical_source_columns_osi()
  - verify_table_columns()
  - generate_create_table_ddl()
  - generate_date_dim_ddl()
  - generate_create_or_replace_table_ddl()
  - generate_sample_insert()

- **connection_management**: Session handling, retries, metadata queries
  - open_session()
  - close_session()
  - execute_with_retry()
  - fetch_schema_metadata()
  - check_semantic_view_exists()

- **identifier_utilities**: Naming, sanitization, alias resolution
  - sanitize_col_name()
  - sanitize_semantic_name()
  - to_snowflake_relationship_name()
  - get_safe_object_name()
  - sanitize_alias()
  - resolve_unique_table_alias()
  - resolve_unique_metric_alias()
  - resolve_unique_dimension_alias()
  - resolve_column_name_for_dataset()
  - resolve_metric_reference_name()
  - migrate_numeric_leading_identifiers()

- **schema_evolution**: Type inference, CTAS, schema evolution
  - get_source_column_types()
  - infer_type_from_values()
  - fallback_type_from_name()
  - normalize_declared_type()
  - business_rule_type()
  - build_cast_expression()
  - generate_ctas_sql()
  - infer_columns_from_table_samples()
  - evolve_schema()

- **measure_sync**: Measure materialization and syncing
  - sync_measure_data()
  - generate_semantic_view_tiered()

- **metric_helpers**: Metric validation and normalization (existing)
  - validate_metric_column_references()
  - normalize_metric_column_references()
  - try_basic_dax_metric_fallback_expression()
  - try_llm_metric_fallback_expression()
  - prune_unresolved_metric_lines()

- **renderers**: DDL and YAML generation (existing)
  - generate_ddls()
  - generate_ddls_from_osi()
  - generate_cortex_yaml()
  - generate_cortex_yaml_from_osi()

- **exceptions**: Snowflake emitter warnings and errors
  - MissingSourceTableWarning

- **yaml_utils**: YAML formatting helpers
  - IndentDumper
  - str_presenter

Usage:
------

The SnowflakeEmitter class imports functions from these modules to keep
the main class focused on orchestration and configuration.

Example migration for a new feature:
  1. Identify which module best fits the functionality
  2. Add the function to that module
  3. Update SnowflakeEmitter to import from the module
  4. Call the module function through the emitter instance

Benefits:
---------
✓ Reduced complexity: ~2700 lines split into ~400-600 lines per module
✓ Better testability: Each module can be tested independently
✓ Clearer responsibility: Each module handles specific concerns
✓ Easier maintenance: Logical organization improves navigation
✓ Reusability: Functions can be imported/used independently if needed
"""

from . import table_management
from . import connection_management
from . import identifier_utilities
from . import schema_evolution
from . import measure_sync
from . import metric_helpers
from . import exceptions
from . import yaml_utils
from . import renderers

__all__ = [
    "table_management",
    "connection_management",
    "identifier_utilities",
    "schema_evolution",
    "measure_sync",
    "metric_helpers",
    "exceptions",
    "yaml_utils",
    "renderers",
]

