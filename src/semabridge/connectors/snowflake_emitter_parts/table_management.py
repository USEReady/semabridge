"""Table management, creation, and verification utilities for Snowflake emitter.

Handles:
- Source table creation and verification
- Column collection and mapping
- Schema validation and evolution
- Physical vs calculated column detection
"""

from typing import Any, Dict, Optional, Tuple
from pathlib import Path
import re

from semabridge.utils.logger import get_logger
from semabridge.core.exceptions import ConnectorError

logger = get_logger(__name__)


def collect_physical_source_columns(
    emitter,
    dataset: "SMLDataset",
) -> dict[str, Any]:
    """Return ordered map of physical Snowflake column name -> SML column.

    All valid semantic columns are preserved. When multiple semantic columns
    normalize to the same Snowflake identifier, deterministic suffixes are
    appended (``_1``, ``_2``, ...) so deployment remains lossless.
    """
    valid_columns: list[Any] = []
    base_totals: dict[str, int] = {}
    for col in dataset.columns:
        col_name = col.unique_name

        if col_name.startswith("RowNumber") or col_name.startswith("_"):
            continue

        source_expr = getattr(col, 'source_expression', None)
        if source_expr and not emitter._is_physical_source_column(source_expr):
            logger.debug(
                "Skipping calculated column '%s' from physical source columns",
                col_name,
            )
            continue

        valid_columns.append(col)
        safe_base = emitter._sanitize_col_name(col_name)
        base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

    selected: dict[str, Any] = {}
    base_seen: dict[str, int] = {}
    signature_seen: dict[str, int] = {}
    namespace_key = emitter._duplicate_namespace_key()
    dataset_key = emitter._sanitize_alias(dataset.source_table or dataset.unique_name)
    for col in valid_columns:
        safe_base = emitter._sanitize_col_name(col.unique_name)
        next_idx = base_seen.get(safe_base, 0) + 1
        base_seen[safe_base] = next_idx

        if base_totals.get(safe_base, 0) > 1:
            signature_seed = emitter._build_duplicate_signature_seed(
                source_name=col.unique_name,
                source_expression=getattr(col, "source_expression", None),
                data_type=str(getattr(col, "data_type", "")),
            )
            sig_idx = signature_seen.get(signature_seed, 0) + 1
            signature_seen[signature_seed] = sig_idx
            source_signature = f"{signature_seed}::occ{sig_idx}"
            preferred_name = f"{safe_base}_{next_idx}"
            safe_name = emitter._resolve_persistent_duplicate_name(
                scope_type="column",
                namespace_key=namespace_key,
                dataset_key=dataset_key,
                normalized_base=safe_base,
                source_name=col.unique_name,
                source_signature=source_signature,
                preferred_name=preferred_name,
            )
        else:
            safe_name = safe_base

        while safe_name in selected:
            next_idx += 1
            base_seen[safe_base] = next_idx
            safe_name = f"{safe_base}_{next_idx}"

        if base_totals.get(safe_base, 0) > 1:
            logger.warning(
                "Resolved physical column collision on table '%s': '%s' normalized to '%s'; using '%s'",
                dataset.source_table or dataset.unique_name,
                col.unique_name,
                safe_base,
                safe_name,
            )

        selected[safe_name] = col

    return selected


def collect_physical_source_columns_osi(
    emitter,
    dataset: "OSIDataset",
) -> dict[str, Any]:
    """Return ordered map of physical Snowflake column name -> OSI column.

    Uses the same collision policy as SML physical columns: keep all by
    suffixing repeated sanitized names with ``_1``, ``_2``, ...
    """
    valid_columns: list[Any] = []
    base_totals: dict[str, int] = {}
    for col in dataset.columns:
        col_name = col.unique_name
        if col_name.startswith("RowNumber") or col_name.startswith("_"):
            continue
        source_expr = getattr(col, "source_expression", None)
        if source_expr and not emitter._is_physical_source_column(source_expr):
            continue

        valid_columns.append(col)
        safe_base = emitter._sanitize_col_name(col_name)
        base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

    selected: dict[str, Any] = {}
    base_seen: dict[str, int] = {}
    signature_seen: dict[str, int] = {}
    namespace_key = emitter._duplicate_namespace_key()
    dataset_key = emitter._sanitize_alias(dataset.source_table or dataset.unique_name)
    for col in valid_columns:
        safe_base = emitter._sanitize_col_name(col.unique_name)
        next_idx = base_seen.get(safe_base, 0) + 1
        base_seen[safe_base] = next_idx

        if base_totals.get(safe_base, 0) == 1:
            safe_name = safe_base
        else:
            signature_seed = emitter._build_duplicate_signature_seed(
                source_name=col.unique_name,
                source_expression=getattr(col, "source_expression", None),
                data_type=str(getattr(col, "data_type", "")),
            )
            sig_idx = signature_seen.get(signature_seed, 0) + 1
            signature_seen[signature_seed] = sig_idx
            source_signature = f"{signature_seed}::occ{sig_idx}"
            preferred_name = f"{safe_base}_{next_idx}"
            safe_name = emitter._resolve_persistent_duplicate_name(
                scope_type="column",
                namespace_key=namespace_key,
                dataset_key=dataset_key,
                normalized_base=safe_base,
                source_name=col.unique_name,
                source_signature=source_signature,
                preferred_name=preferred_name,
            )
        while safe_name in selected:
            next_idx += 1
            base_seen[safe_base] = next_idx
            safe_name = f"{safe_base}_{next_idx}"

        selected[safe_name] = col

    return selected


def verify_table_columns(
    emitter,
    cursor,
    table_name: str,
    dataset,
) -> Tuple[set, set]:
    """Verify that a table's columns match the expected SML columns.
    
    Returns:
        tuple: (missing_columns: set, extra_columns: set)
    """
    try:
        emitter._execute_sql(
            cursor,
            f'DESC TABLE {emitter.config.schema_name}."{table_name}"',
            context=f"DESC TABLE {table_name}"
        )
        existing_cols = {row[0] for row in cursor.fetchall()}
        
        expected_cols = set(collect_physical_source_columns(emitter, dataset).keys())
        
        missing = expected_cols - existing_cols
        if missing:
            logger.debug(f"Table {table_name} missing columns: {missing}")
        
        internal_cols = {
            'METADATA$ROW_ID', 'METADATA$IS_DELETED', 'METADATA$FILE_NAME',
            'METADATA$START_SCAN_TIME', 'METADATA$ACTION', 'METADATA$ROW_VERSION'
        }
        extra_cols = existing_cols - expected_cols - internal_cols
        
        return (missing, extra_cols)
    except Exception as e:
        logger.warning(f"Could not verify table {table_name}: {e}")
        all_expected = set(collect_physical_source_columns(emitter, dataset).keys())
        return (all_expected, set())


def generate_create_table_ddl(
    emitter,
    dataset,
    table_name: str,
) -> str:
    """Generate CREATE TABLE DDL from SML dataset definition."""
    type_map = {
        "STRING": "VARCHAR(500)",
        "INTEGER": "INTEGER",
        "FLOAT": "FLOAT",
        "DECIMAL": "DECIMAL(18,2)",
        "BOOLEAN": "BOOLEAN",
        "DATETIME": "TIMESTAMP_NTZ",
        "DATE": "DATE",
        "BINARY": "BINARY"
    }
    
    col_defs = []
    for safe_name, col in collect_physical_source_columns(emitter, dataset).items():
        sf_type = type_map.get(col.data_type.value, "VARCHAR(500)")
        col_defs.append(f'    "{safe_name}" {sf_type}')
    
    if not col_defs:
        col_defs.append("    ID INTEGER")
    
    safe_table = emitter._safe_table_name(table_name)
    quoted_table = f'"{safe_table}"'
    ddl = f"CREATE TABLE IF NOT EXISTS {emitter.config.schema_name}.{quoted_table} (\n"
    ddl += ",\n".join(col_defs)
    ddl += "\n);"
    
    return ddl


def generate_date_dim_ddl(
    emitter,
    table_name: str,
) -> str:
    """Generate DDL for a Date Dimension using Snowflake Generator."""
    safe_table = emitter._safe_table_name(table_name)
    schema = emitter.config.schema_name
    
    return f"""
    CREATE TABLE IF NOT EXISTS {schema}."{safe_table}" AS
    SELECT
      DATEADD(DAY, SEQ4(), DATEADD(YEAR, -10, CURRENT_DATE())) AS DATE,
      YEAR(DATE) AS YEAR,
      QUARTER(DATE) AS QUARTER,
      MONTH(DATE) AS MONTH,
      MONTHNAME(DATE) AS MONTHNAME,
      DAYOFWEEK(DATE) AS DAYOFWEEK,
      DAYNAME(DATE) AS DAYNAME
    FROM TABLE(GENERATOR(ROWCOUNT => 7300));
    """


def generate_create_or_replace_table_ddl(
    emitter,
    dataset,
    table_name: str,
) -> str:
    """Generate safe CREATE TABLE DDL that preserves any existing table."""
    type_map = {
        "STRING": "VARCHAR(500)",
        "INTEGER": "INTEGER",
        "FLOAT": "FLOAT",
        "DECIMAL": "DECIMAL(18,2)",
        "BOOLEAN": "BOOLEAN",
        "DATETIME": "TIMESTAMP_NTZ",
        "DATE": "DATE",
        "BINARY": "BINARY",
    }

    safe_table = emitter._safe_table_name(table_name)
    schema = emitter.config.schema_name

    col_defs = []
    for col_name, col in collect_physical_source_columns(emitter, dataset).items():
        sf_type = type_map.get(col.data_type.value, "VARCHAR(500)")
        col_defs.append(f'    "{col_name}" {sf_type}')

    if not col_defs:
        col_defs.append('    "ID" VARCHAR')

    cols_block = ",\n".join(col_defs)
    return f'CREATE TABLE IF NOT EXISTS {schema}."{safe_table}" (\n{cols_block}\n);'


def generate_sample_insert(
    emitter,
    dataset,
    table_name: str,
) -> Optional[str]:
    """Generate INSERT statement with sample data for testing."""
    physical_cols = collect_physical_source_columns(emitter, dataset)
    if not physical_cols:
        return None

    columns = list(physical_cols.values())
    col_names = [f'"{col_name}"' for col_name in physical_cols.keys()]
    
    sample_values = []
    for col in columns:
        dtype = col.data_type.value
        if dtype in ("INTEGER", "FLOAT", "DECIMAL"):
            sample_values.append("1")
        elif dtype == "BOOLEAN":
            sample_values.append("TRUE")
        elif dtype in ("DATETIME", "DATE"):
            sample_values.append("CURRENT_DATE()")
        else:
            sample_values.append(f"'Sample_{col.unique_name[:20]}'")
    
    safe_table = emitter._safe_table_name(table_name)
    cols_str = ", ".join(col_names)
    vals_str = ", ".join(sample_values)
    insert = f'INSERT INTO {emitter.config.schema_name}."{safe_table}" ({cols_str})\n'
    insert += f"SELECT {vals_str}\n"
    insert += f'WHERE NOT EXISTS (SELECT 1 FROM {emitter.config.schema_name}."{safe_table}" LIMIT 1);'
    
    return insert
