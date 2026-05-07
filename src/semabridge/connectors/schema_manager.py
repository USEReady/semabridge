from __future__ import annotations

import re
import time
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    import snowflake.connector.cursor
    from semabridge.converter.measure_triage import TriageResult
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior, SnowflakeBehavior
from semabridge.core.exceptions import ConnectorError
from semabridge.formats.sml.models import SMLModel, SMLDataset, SMLMetric, SMLDimension, SMLRelationship, AggregationType, DataType
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.snowflake_emitter_parts.exceptions import MissingSourceTableWarning

if TYPE_CHECKING:
    from semabridge.intermediate.models import (
        OSIModel,
        OSIDataset,
        OSIMetric,
        OSIDimension,
        OSIAttribute,
        OSIColumn,
        OSIDataType
    )
else:
    try:
        from semabridge.intermediate.models import (
            OSIModel,
            OSIDataset,
            OSIMetric,
            OSIDimension,
            OSIAttribute,
            OSIColumn,
            OSIDataType,
        )
    except ImportError:
        OSIModel: TypeAlias = Any
        OSIDataset: TypeAlias = Any
        OSIMetric: TypeAlias = Any
        OSIDimension: TypeAlias = Any
        OSIAttribute: TypeAlias = Any
        OSIColumn: TypeAlias = Any
        OSIDataType = None

from semabridge.utils.logger import get_logger
logger = get_logger(__name__)

class SnowflakeSchemaManager:
    def __init__(
        self,
        config: SnowflakeConfig,
        behavior: ConnectorBehavior,
        identifier_sanitizer: IdentifierSanitizer,
        connection_manager: Any,
        dup_name_repo: Any = None,
        
    ):
        self.config = config
        self.behavior = behavior
        self.sf_behavior = behavior.snowflake
        self._id = identifier_sanitizer
        self.connection_manager = connection_manager
        self._dup_name_repo = dup_name_repo
        self._verified_tables: set[str] = set()  # Temporary compatibility during phase 2 refactor

    def _execute_sql(self, cursor, sql: str, params: Any = None, context: str = "") -> Any:
        return self.connection_manager._execute_sql(cursor, sql, params, context=context)

    def _build_fixed_table_name(self, safe_table_name: str) -> str:
        """Create a collision-resistant temp table name for CTAS+SWAP."""
        suffix = f"{int(time.time() * 1000)}_{os.getpid()}"
        candidate = f"{safe_table_name}__FIXED_{suffix}"
        # Snowflake identifier max length is 255 characters.
        return candidate[:255]

    def _safe_table_name(self, name: str) -> str:
        return self._id.sanitize_table_name(name)

    @staticmethod
    def _quote_ident(value: str) -> str:
        """Quote a Snowflake identifier safely."""
        raw = str(value or "").replace('"', '""')
        return f'"{raw}"'

    def _schema_fqn(self) -> str:
        """Return fully-qualified schema name as "DB"."SCHEMA"."""
        db_name = str(self.config.database or "").strip()
        schema_name = str(self.config.schema_name or "").strip()
        # Accept accidental DB.SCHEMA input in schema_name without producing
        # malformed "DB"."DB.SCHEMA" references.
        if "." in schema_name and db_name:
            left, right = schema_name.split(".", 1)
            if left.strip().upper() == db_name.upper():
                schema_name = right.strip()
        return f"{self._quote_ident(db_name)}.{self._quote_ident(schema_name)}"

    def _sanitize_col_name(self, name: str) -> str:
        sanitized = self._id.sanitize_column(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _sanitize_alias(self, name: str) -> str:
        sanitized = self._id.sanitize_alias(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _get_safe_object_name(self, name: str) -> str:
        return self._sanitize_col_name(name)

    @staticmethod
    def _is_physical_source_column(source_expression: str) -> bool:
        return IdentifierSanitizer.is_physical_source_column(source_expression)

    def _build_duplicate_signature_seed(
        self,
        source_name: str,
        source_expression: Optional[str],
        data_type: Optional[str],
        aggregation: Optional[str] = None
    ) -> str:
        return "|".join([source_name or "", source_expression or "", data_type or "", aggregation or ""])

    def _duplicate_namespace_key(self, model_name: Optional[str] = None) -> str:
        parts = [
            self._id.sanitize_alias(self.config.database or "DB"),
            self._id.sanitize_alias(self.config.schema_name or "SCHEMA"),
        ]
        if model_name:
            parts.append(self._id.sanitize_alias(model_name))
        return ".".join(parts)

    def _resolve_persistent_duplicate_name(
        self,
        scope_type: str,
        namespace_key: str,
        dataset_key: str,
        normalized_base: str,
        source_name: str,
        source_signature: str,
        preferred_name: str
    ) -> str:
        if not self._dup_name_repo:
            return preferred_name
        try:
            return self._dup_name_repo.get_or_create_assigned_name(
                scope_type=scope_type,
                namespace_key=namespace_key,
                dataset_key=dataset_key,
                normalized_base=normalized_base,
                source_name=source_name,
                source_signature=source_signature,
                preferred_name=preferred_name,
            )
        except Exception as exc:
            logger.warning("Duplicate mapping failed for %s: %s", source_name, exc)
            return preferred_name

    def _ensure_source_tables_exist(self, cursor, sml: SMLModel) -> None:
        """
        Check if source tables exist in Snowflake, create them if missing.
        Also verify column structure matches and handle discrepancies:
        - Missing columns: recreate table
        - Extra columns: drop them using ALTER TABLE DROP COLUMN (preserves data)
        Tables are created with structure based on SML column definitions.

        Uses ``_verified_tables`` cache (P2b) to skip redundant SHOW TABLES
        queries for source tables already confirmed by a prior model deploy
        within the same session.
        """
        # Collect which datasets actually need checking (skip cached ones)
        datasets_to_check = []
        for dataset in sml.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            if safe_table_name in self._verified_tables:
                logger.debug(f"Table '{safe_table_name}' already verified this session â€” skipping")
                continue
            datasets_to_check.append(dataset)

        if not datasets_to_check:
            logger.debug("All source tables already verified â€” skipping SHOW TABLES")
            return

        # Only query Snowflake if we have datasets to check
        self._execute_sql(cursor, f"SHOW TABLES IN SCHEMA {self._schema_fqn()}", context="SHOW TABLES")
        existing_tables = {row[1].upper() for row in cursor.fetchall()}
        
        # Get list of existing views (to avoid collision)
        self._execute_sql(cursor, f"SHOW VIEWS IN SCHEMA {self._schema_fqn()}", context="SHOW VIEWS")
        existing_views = {row[1].upper() for row in cursor.fetchall()}
        
        all_existing = existing_tables | existing_views
        
        # Check each dataset's source table
        for dataset in datasets_to_check:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            quoted_table = f'"{safe_table_name}"'
            
            needs_creation = False
            
            if safe_table_name not in all_existing:
                needs_creation = True
                logger.info(f"Source table '{safe_table_name}' not found, creating...")
            else:
                # Table exists - verify columns match
                if self.sf_behavior.validate_column_schema:
                    missing_columns, extra_columns = self._verify_table_columns(cursor, safe_table_name, dataset)
                else:
                    missing_columns = set()
                    extra_columns = set()
                
                # Use EVOLVE (additive) approach for shared source tables
                if missing_columns:
                    logger.info(f"Table '{safe_table_name}' missing columns: {missing_columns}. Adding them...")
                    physical_cols = self._collect_physical_source_columns(dataset)
                    for col_name in missing_columns:
                        # Find canonical source column from the resolved physical map
                        orig_col = physical_cols.get(col_name)
                        if orig_col:
                            type_map = {
                                "STRING": "VARCHAR(500)", "INTEGER": "INTEGER", "FLOAT": "FLOAT",
                                "DECIMAL": "DECIMAL(18,2)", "BOOLEAN": "BOOLEAN", "DATETIME": "TIMESTAMP_NTZ",
                                "DATE": "DATE", "BINARY": "BINARY",
                            }
                            sf_type = type_map.get(orig_col.data_type.value, "VARCHAR(500)")
                            try:
                                self._execute_sql(
                                    cursor,
                                    f'ALTER TABLE {self.config.schema_name}.{quoted_table} ADD COLUMN "{col_name}" {sf_type}',
                                    context=f"ALTER TABLE ADD COLUMN {safe_table_name}.{col_name}",
                                )
                            except Exception as e:
                                logger.warning(f"Could not add column {col_name} to {safe_table_name}: {e}")
                                # Fallback: if ALTER fails (e.g. constraints), we might need recreation
                                # but for now we try to stay additive.
                
                # Skip dropping extra columns by default to allow sharing tables across models
                # self._drop_extra_columns(cursor, safe_table_name, extra_columns, dataset=dataset)

            
            if needs_creation:
                # Generate CREATE TABLE DDL
                if source_table.upper() == "DIM_DATE":
                    create_ddl = self._generate_date_dim_ddl(source_table)
                else:
                    create_ddl = self._generate_create_table_ddl(dataset, source_table)
                
                try:
                    self._execute_sql(cursor, create_ddl, context=f"CREATE TABLE {safe_table_name}")
                    logger.info(f"Created table: {safe_table_name}")

                    # Belt-and-suspenders: after CREATE TABLE IF NOT EXISTS
                    # the table may have already existed (created by a prior
                    # model's deployment or an external process) with a
                    # different column set. Verify columns match and
                    # rebuild if they don't.  This prevents the subsequent
                    # INSERT from hitting "invalid identifier" errors.
                    if source_table.upper() != "DIM_DATE":
                        missing_post, _ = self._verify_table_columns(
                            cursor, safe_table_name, dataset
                        )
                        if missing_post:
                            physical_cols = self._collect_physical_source_columns(dataset)
                            for col_name in missing_post:
                                # Find canonical source column from the resolved physical map
                                orig_col = physical_cols.get(col_name)
                                if orig_col:
                                    type_map = {
                                        "STRING": "VARCHAR(500)", "INTEGER": "INTEGER", "FLOAT": "FLOAT",
                                        "DECIMAL": "DECIMAL(18,2)", "BOOLEAN": "BOOLEAN", "DATETIME": "TIMESTAMP_NTZ",
                                        "DATE": "DATE", "BINARY": "BINARY",
                                    }
                                    sf_type = type_map.get(orig_col.data_type.value, "VARCHAR(500)")
                                    self._execute_sql(
                                        cursor,
                                        f'ALTER TABLE {self.config.schema_name}.{quoted_table} ADD COLUMN "{col_name}" {sf_type}',
                                        context=f"ALTER TABLE ADD COLUMN {safe_table_name}.{col_name}",
                                    )

                    
                    if source_table.upper() == "DIM_DATE":
                         logger.info("Populated DIM_DATE with generated data")
                    else:
                        # Insert sample data if this is an imported model
                        sample_insert = self._generate_sample_insert(dataset, source_table)
                        if sample_insert:
                            self._execute_sql(cursor, sample_insert, context=f"INSERT SAMPLE ROWS {safe_table_name}")
                            logger.info(f"Inserted sample data into: {safe_table_name}")
                        
                except Exception as e:
                    logger.error(
                        f"Failed to create required source table '{safe_table_name}': {e}. "
                        f"Downstream semantic view DDL will fail."
                    )
                    raise ConnectorError(
                        f"Cannot create source table '{safe_table_name}' in "
                        f"{self.config.database}.{self.config.schema_name}: {e}"
                    ) from e

            # Mark table as verified for this session (P2b cache)
            self._verified_tables.add(safe_table_name)
    
    def _verify_table_columns(self, cursor, table_name: str, dataset: SMLDataset) -> tuple:
        """
        Verify that a table's columns match the expected SML columns.
        
        Returns:
            tuple: (missing_columns: set, extra_columns: set)
                - missing_columns: Set of column names that are expected in SML but missing in Snowflake
                - extra_columns: Set of column names that exist in Snowflake but not in SML
        """
        try:
            self._execute_sql(cursor, f'DESC TABLE {self.config.schema_name}."{table_name}"', context=f"DESC TABLE {table_name}")
            existing_cols = {row[0] for row in cursor.fetchall()}
            
            # Check if model expects columns that don't exist
            expected_cols = set(self._collect_physical_source_columns(dataset).keys())
            
            missing = expected_cols - existing_cols
            if missing:
                logger.debug(f"Table {table_name} missing columns: {missing}")
            
            internal_cols = {'METADATA$ROW_ID', 'METADATA$IS_DELETED', 'METADATA$FILE_NAME', 
                           'METADATA$START_SCAN_TIME', 'METADATA$ACTION', 'METADATA$ROW_VERSION'}
            extra_cols = existing_cols - expected_cols - internal_cols
            
            return (missing, extra_cols)
        except Exception as e:
            logger.warning(f"Could not verify table {table_name}: {e}")
            # If table doesn't exist or error occurs, return all columns as missing
            all_expected = set(self._collect_physical_source_columns(dataset).keys())
            return (all_expected, set())

    def _drop_extra_columns(self, cursor, table_name: str, extra_columns: set,
                            dataset: Optional[SMLDataset] = None) -> None:
        """
        Drop extra columns from a table that are not in the SML model.
        
        When the DDL strategy is 'idempotent' (Mandate 2) and the guard
        triggers (would drop all columns), automatically recreates the
        table using CREATE OR REPLACE instead of silently returning.
        
        Args:
            cursor: Snowflake cursor
            table_name: Name of the table
            extra_columns: Set of column names to drop (uppercase)
            dataset: Optional SMLDataset for full recreation when needed
        """
        if not extra_columns:
            return
        
        # Guard: Snowflake forbids dropping ALL columns from a table.
        try:
            self._execute_sql(cursor, f'DESC TABLE {self.config.schema_name}."{table_name}"', context=f"DESC TABLE {table_name}")
            total_cols = {row[0] for row in cursor.fetchall()}
            if extra_columns >= total_cols:
                message = (
                    f"Refusing to drop all columns from existing table {table_name}; "
                    "full table recreation is disabled to preserve user data."
                )
                logger.warning(message)
                raise ConnectorError(message)
        except Exception as e:
            if isinstance(e, ConnectorError):
                raise
            logger.warning(f"Could not check column count for {table_name}: {e}")
        
        for col_name in extra_columns:
            try:
                ddl = f'ALTER TABLE {self.config.schema_name}."{table_name}" DROP COLUMN "{col_name}"'
                logger.info(f"Dropping extra column: {table_name}.{col_name}")
                self._execute_sql(cursor, ddl, context=f"DROP COLUMN {table_name}.{col_name}")
                logger.info(f"Successfully dropped column: {col_name}")
            except Exception as e:
                logger.warning(f"Could not drop column {col_name} from {table_name}: {e}")

    def _generate_date_dim_ddl(self, table_name: str) -> str:
        """
        Generate DDL for a Date Dimension using Snowflake Generator.
        Creates 20 years of data (10 past, 10 future).
        """
        safe_table = self._safe_table_name(table_name)
        schema = self.config.schema_name
        
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

    def _generate_create_table_ddl(self, dataset: SMLDataset, table_name: str) -> str:
        """Generate CREATE TABLE DDL from SML dataset definition."""
        # Data type mapping from SML/TMSL to Snowflake
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
        for safe_name, col in self._collect_physical_source_columns(dataset).items():
            sf_type = type_map.get(col.data_type.value, "VARCHAR(500)")
            col_defs.append(f'    "{safe_name}" {sf_type}')
        
        if not col_defs:
            # Fallback: create with a single ID column
            col_defs.append("    ID INTEGER")
        
        safe_table = self._safe_table_name(table_name)
        quoted_table = f'"{safe_table}"'
        ddl = f"CREATE TABLE IF NOT EXISTS {self.config.schema_name}.{quoted_table} (\n"
        ddl += ",\n".join(col_defs)
        ddl += "\n);"
        
        return ddl

    def _generate_sample_insert(self, dataset: SMLDataset, table_name: str) -> Optional[str]:
        """Generate INSERT statement with sample data for testing."""
        physical_cols = self._collect_physical_source_columns(dataset)
        if not physical_cols:
            return None

        columns = list(physical_cols.values())
        col_names = [f'"{col_name}"' for col_name in physical_cols.keys()]
        
        # Generate sample values based on data types
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
        
        safe_table = self._safe_table_name(table_name)
        cols_str = ", ".join(col_names)
        vals_str = ", ".join(sample_values)
        insert = f'INSERT INTO {self.config.schema_name}."{safe_table}" ({cols_str})\n'
        insert += f"SELECT {vals_str}\n"
        insert += f'WHERE NOT EXISTS (SELECT 1 FROM {self.config.schema_name}."{safe_table}" LIMIT 1);'
        
        return insert

    def _collect_physical_source_columns(self, dataset: SMLDataset) -> dict[str, Any]:
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
            if source_expr and not self._is_physical_source_column(source_expr):
                logger.debug(
                    "Skipping calculated column '%s' from physical source columns",
                    col_name,
                )
                continue

            valid_columns.append(col)
            safe_base = self._sanitize_col_name(col_name)
            base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

        selected: dict[str, Any] = {}
        base_seen: dict[str, int] = {}
        signature_seen: dict[str, int] = {}
        namespace_key = self._duplicate_namespace_key()
        dataset_key = self._sanitize_alias(dataset.source_table or dataset.unique_name)
        for col in valid_columns:
            safe_base = self._sanitize_col_name(col.unique_name)
            next_idx = base_seen.get(safe_base, 0) + 1
            base_seen[safe_base] = next_idx

            if base_totals.get(safe_base, 0) > 1:
                signature_seed = self._build_duplicate_signature_seed(
                    source_name=col.unique_name,
                    source_expression=getattr(col, "source_expression", None),
                    data_type=str(getattr(col, "data_type", "")),
                )
                sig_idx = signature_seen.get(signature_seed, 0) + 1
                signature_seen[signature_seed] = sig_idx
                source_signature = f"{signature_seed}::occ{sig_idx}"
                preferred_name = f"{safe_base}_{next_idx}"
                safe_name = self._resolve_persistent_duplicate_name(
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

    def _resolve_physical_column_name(self, dataset: SMLDataset, raw_col_name: str) -> str:
        """Resolve semantic/raw column name to canonical physical column name."""
        physical_cols = self._collect_physical_source_columns(dataset)

        for phys_name, col in physical_cols.items():
            if col.unique_name == raw_col_name:
                return phys_name

        base = self._sanitize_col_name(raw_col_name)
        if base in physical_cols:
            return base

        for phys_name, col in physical_cols.items():
            if self._sanitize_col_name(col.unique_name) == base:
                return phys_name

        return base

    def _collect_physical_source_columns_osi(self, dataset: OSIDataset) -> dict[str, Any]:
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
            if source_expr and not self._is_physical_source_column(source_expr):
                continue

            valid_columns.append(col)
            safe_base = self._sanitize_col_name(col_name)
            base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

        selected: dict[str, Any] = {}
        base_seen: dict[str, int] = {}
        signature_seen: dict[str, int] = {}
        namespace_key = self._duplicate_namespace_key()
        dataset_key = self._sanitize_alias(dataset.source_table or dataset.unique_name)
        for col in valid_columns:
            safe_base = self._sanitize_col_name(col.unique_name)
            next_idx = base_seen.get(safe_base, 0) + 1
            base_seen[safe_base] = next_idx

            if base_totals.get(safe_base, 0) == 1:
                safe_name = safe_base
            else:
                signature_seed = self._build_duplicate_signature_seed(
                    source_name=col.unique_name,
                    source_expression=getattr(col, "source_expression", None),
                    data_type=str(getattr(col, "data_type", "")),
                )
                sig_idx = signature_seen.get(signature_seed, 0) + 1
                signature_seen[signature_seed] = sig_idx
                source_signature = f"{signature_seed}::occ{sig_idx}"
                preferred_name = f"{safe_base}_{next_idx}"
                safe_name = self._resolve_persistent_duplicate_name(
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

    def _resolve_physical_column_name(self, dataset: SMLDataset, raw_col_name: str) -> str:
        """Resolve semantic/raw column name to canonical physical column name."""
        physical_cols = self._collect_physical_source_columns(dataset)

        for phys_name, col in physical_cols.items():
            if col.unique_name == raw_col_name:
                return phys_name

        base = self._sanitize_col_name(raw_col_name)
        if base in physical_cols:
            return base

        for phys_name, col in physical_cols.items():
            if self._sanitize_col_name(col.unique_name) == base:
                return phys_name

        return base

    def generate_ctas_sql(
        self,
        table_name: str,
        columns: list[dict[str, str]],
        schema_name: Optional[str] = None,
        source_types: Optional[dict[str, str]] = None,
        fixed_table_name: Optional[str] = None,
    ) -> str:
        """Generate CTAS SQL that applies safe datatype conversions.

        Args:
            table_name: Physical table name (unqualified).
            columns: List of {"name": <col_name>, "type": <normalized_type>}.
            schema_name: Optional schema override.
            source_types: Optional mapping of column name -> Snowflake source DATA_TYPE.
        """
        if not columns:
            raise ValueError("No columns inferred")

        varchar_types = {"VARCHAR", "STRING", "TEXT", "UNKNOWN", "VARIANT"}
        if all((c.get("type", "").upper() in varchar_types) for c in columns):
            logger.warning(
                "All columns are VARCHAR for %s. Generating pass-through CTAS.",
                table_name,
            )

        select_parts: list[str] = []
        source_types = source_types or {}
        for col in columns:
            name = col["name"]
            dtype = col["type"].upper()
            quoted_name = f'"{name}"'
            source_type = (source_types.get(name) or source_types.get(name.upper()) or "").upper()

            cast_expr = self._build_cast_expression(
                quoted_name=quoted_name,
                target_type=dtype,
                source_type=source_type,
                col_name=name,
            )
            select_parts.append(f"{cast_expr} AS {quoted_name}")

        select_sql = ",\n    ".join(select_parts)

        schema = schema_name or self.config.schema_name
        quoted_source = f'{schema}."{table_name}"'
        fixed_name = fixed_table_name or f"{table_name}__FIXED"
        quoted_fixed = f'{schema}."{fixed_name}"'
        return (
            f"CREATE OR REPLACE TABLE {quoted_fixed} AS\n"
            f"SELECT\n    {select_sql}\n"
            f"FROM {quoted_source};"
        )

    def _dataset_columns_for_ctas_sml(self, dataset: SMLDataset) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for col_name, col in self._collect_physical_source_columns(dataset).items():
            columns.append(
                {
                    "name": col_name,
                    "type": col.data_type.value.upper(),
                }
            )
        return columns

    @staticmethod
    def _infer_type_from_values(values: list[Any]) -> str:
        """Infer normalized type from sampled Python/Snowflake values."""
        if not values:
            return "VARCHAR"

        def _kind(v: Any) -> str:
            if isinstance(v, bool):
                return "BOOLEAN"
            if isinstance(v, int) and not isinstance(v, bool):
                return "INTEGER"
            if isinstance(v, (float, Decimal)):
                return "FLOAT"
            if isinstance(v, datetime):
                return "TIMESTAMP"
            if isinstance(v, date):
                return "DATE"

            s = str(v).strip()
            if not s:
                return "NULL"

            sl = s.lower()
            if sl in {"true", "false", "yes", "no", "y", "n", "t", "f"}:
                return "BOOLEAN"
            if re.fullmatch(r"[+-]?\d+", s):
                return "INTEGER"
            if re.fullmatch(r"[+-]?(?:\d+\.\d+|\d+\.\d*|\.\d+)", s):
                return "FLOAT"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return "DATE"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", s):
                return "TIMESTAMP"
            return "VARCHAR"

        kinds = {_kind(v) for v in values if v is not None}
        kinds.discard("NULL")
        if not kinds:
            return "VARCHAR"
        if kinds == {"BOOLEAN"}:
            return "BOOLEAN"
        if kinds == {"INTEGER"}:
            return "INTEGER"
        if kinds.issubset({"INTEGER", "FLOAT"}):
            return "FLOAT"
        if kinds == {"DATE"}:
            return "DATE"
        if kinds.issubset({"DATE", "TIMESTAMP"}):
            return "TIMESTAMP"
        return "VARCHAR"

    @staticmethod
    def _fallback_type_from_name(col_name: str) -> str:
        """Fallback inference for all-VARCHAR scenarios."""
        n = col_name.lower()
        tokens = [t for t in re.split(r"[^a-z0-9$]+", n.replace("_", " ")) if t]
        token_set = set(tokens)

        if any(k in n for k in ("timestamp", "datetime", "_ts")) or "ts" in token_set:
            return "TIMESTAMP"
        if "date" in n:
            return "DATE"
        # Duration-like columns (lead_time, processing_time, time_spent) should
        # default to numeric in fallback, not timestamp.
        if "time" in token_set:
            return "FLOAT"
        if any(k in n for k in ("amount", "price", "cost", "rate", "pct", "percent", "score", "revenue", "total", "qty", "quantity")):
            return "FLOAT"
        if any(k in n for k in ("is_", "has_", "flag", "active", "enabled", "deleted", "valid", "bool", "boolean")):
            return "BOOLEAN"
        # Use token-based numeric hints so words like ACCOUNT/COUNTRY do not
        # accidentally match "count".
        if any(k in token_set for k in ("count", "num", "year", "month", "day")):
            return "INTEGER"
        if any(k in token_set for k in ("id", "key")) or n.endswith("_id"):
            return "VARCHAR"
        return "VARCHAR"

    def _build_cast_expression(
        self,
        quoted_name: str,
        target_type: str,
        source_type: str,
        col_name: str
    ) -> str:
        """Build a safe cast expression for CTAS based on source and target types."""
        t = (target_type or "").upper()
        s = (source_type or "").upper()

        numeric_targets = {"INTEGER", "FLOAT", "DECIMAL", "NUMBER"}
        timestamp_targets = {"TIMESTAMP", "DATETIME", "TIME", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}
        boolean_targets = {"BOOLEAN", "BOOL"}

        is_source_number = any(k in s for k in ("NUMBER", "DECIMAL", "NUMERIC", "INT", "FLOAT", "DOUBLE", "REAL"))
        is_source_timestamp = "TIMESTAMP" in s
        is_source_date = s.startswith("DATE")
        is_source_varchar = any(k in s for k in ("VARCHAR", "TEXT", "STRING", "CHAR"))
        is_source_boolean = "BOOLEAN" in s or s == "BOOL"

        # Same type -> no cast.
        if s == t:
            return quoted_name

        # Guard unsafe conversions that error in Snowflake.
        if is_source_number and (t == "DATE" or t in timestamp_targets):
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name
        if is_source_timestamp and t in numeric_targets:
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name
        if is_source_date and t in numeric_targets:
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name

        if t in boolean_targets:
            if is_source_boolean:
                return quoted_name
            if is_source_number:
                return f"IFF({quoted_name} IS NULL, NULL, IFF({quoted_name} = 0, FALSE, TRUE))"
            if is_source_varchar:
                return (
                    f"IFF({quoted_name} IS NULL, NULL, "
                    f"IFF(LOWER(TRIM({quoted_name})) IN ('true','1','t','yes','y'), TRUE, "
                    f"IFF(LOWER(TRIM({quoted_name})) IN ('false','0','f','no','n'), FALSE, NULL)))"
                )
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name

        # Safe conversions.
        if is_source_timestamp and t == "DATE":
            return f"CAST({quoted_name} AS DATE)"
        if is_source_date and t in timestamp_targets:
            return f"TO_TIMESTAMP({quoted_name})"
        if is_source_varchar and t == "DATE":
            return f"TRY_TO_DATE({quoted_name})"
        if is_source_varchar and t in timestamp_targets:
            return f"TRY_TO_TIMESTAMP({quoted_name})"

        if t in numeric_targets:
            if is_source_number:
                return quoted_name
            # BOOLEAN -> numeric: TRY_TO_NUMBER(BOOLEAN) is invalid in Snowflake;
            # use IFF to convert TRUE->1, FALSE->0 instead.
            if is_source_boolean:
                return f"IFF({quoted_name} IS NULL, NULL, IFF({quoted_name}, 1, 0))"
            return f"TRY_TO_NUMBER({quoted_name})"
        if t == "DATE":
            if is_source_timestamp:
                return f"CAST({quoted_name} AS DATE)"
            if is_source_date:
                return quoted_name
            return f"TRY_TO_DATE({quoted_name})"
        if t in timestamp_targets:
            if is_source_timestamp:
                return quoted_name
            if is_source_date:
                return f"TO_TIMESTAMP({quoted_name})"
            return f"TRY_TO_TIMESTAMP({quoted_name})"

        return quoted_name

    def _get_source_column_types(self, cursor, safe_table_name: str) -> dict[str, str]:
        """Return Snowflake DATA_TYPE by column for the given physical table."""
        query = (
            "SELECT COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_CATALOG = %s AND TABLE_SCHEMA = %s AND TABLE_NAME = %s"
        )
        self._execute_sql(
            cursor,
            query,
            (
                self.config.database.upper(),
                self.config.schema_name.upper(),
                safe_table_name.upper(),
            ),
            context=f"INFORMATION_SCHEMA.COLUMNS {safe_table_name}",
        )
        rows = cursor.fetchall() or []
        # Preserve original case for column names so quoted references in CTAS
        # match the actual column names (Snowflake is case-sensitive for quoted identifiers).
        # Store both original-case and uppercase keys so lookups work either way.
        col_types: dict[str, str] = {}
        for name, dtype in rows:
            original = str(name)
            col_types[original] = str(dtype).upper()
            col_types[original.upper()] = str(dtype).upper()
        logger.debug("Source column types for %s: %s", safe_table_name, col_types)
        return col_types

    def _infer_columns_from_table_samples(
        self,
        cursor,
        safe_table_name: str,
        columns: list[dict[str, str]],
        sample_limit: int = 200
    ) -> tuple[list[dict[str, str]], list[str]]:
        """Resolve per-column types using model metadata first, sampling second."""
        if not columns:
            raise ValueError("No columns inferred")

        source_types = self._get_source_column_types(cursor, safe_table_name)
        source_names = list(source_types.keys())
        # Build a map from uppercase -> original-case for safe SELECT references
        original_case_map: dict[str, str] = {}
        for sn in source_names:
            upper = sn.upper()
            if upper not in original_case_map:
                original_case_map[upper] = sn

        def _norm(name: str) -> str:
            return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())

        resolved_pairs: list[tuple[str, str]] = []
        fallback_logs: list[str] = []
        for c in columns:
            requested = str(c["name"])
            requested_upper = requested.upper()
            source_name = None

            if requested_upper in source_types:
                # Use original-case name for the SELECT reference
                source_name = original_case_map.get(requested_upper, requested_upper)
            else:
                requested_norm = _norm(requested)
                matches = [src for src in source_names if _norm(src) == requested_norm]
                if len(matches) == 1:
                    source_name = matches[0]

            if source_name:
                resolved_pairs.append((requested, source_name))
            else:
                fallback_logs.append(
                    f"Skipped non-physical column during sampling: {safe_table_name}.{requested}"
                )

        if not resolved_pairs:
            logger.warning(
                "Skipping sample type inference for %s: no model columns matched physical source columns",
                safe_table_name,
            )
            return [], fallback_logs

        def _q(ident: str) -> str:
            escaped = str(ident).replace('"', '""')
            return f'"{escaped}"'

        col_refs = ", ".join([f"{_q(src)} AS {_q(req)}" for req, src in resolved_pairs])
        sample_sql = (
            f'SELECT {col_refs} FROM {self.config.schema_name}."{safe_table_name}" '
            f'LIMIT {sample_limit}'
        )
        # Be resilient to stale/mismatched identifiers in mixed mapping flows:
        # drop the offending projected column and retry sampling remaining columns.
        while True:
            try:
                self._execute_sql(cursor, sample_sql, context=f"SAMPLE QUERY {safe_table_name}")
                break
            except Exception as exc:
                text = str(exc or "")
                match = re.search(r"invalid identifier '([^']+)'", text, flags=re.IGNORECASE)
                invalid = str(match.group(1) if match else "").strip().replace('"', "")
                invalid_upper = invalid.upper()
                if not invalid_upper:
                    raise
                before = len(resolved_pairs)
                resolved_pairs = [
                    (req, src) for (req, src) in resolved_pairs
                    if str(req).upper() != invalid_upper and str(src).upper() != invalid_upper
                ]
                if len(resolved_pairs) == before:
                    raise
                fallback_logs.append(
                    f"Dropped invalid sampled identifier {safe_table_name}.{invalid_upper} and retried."
                )
                if not resolved_pairs:
                    logger.warning(
                        "Skipping sample type inference for %s after dropping invalid sampled identifiers",
                        safe_table_name,
                    )
                    return [], fallback_logs
                col_refs = ", ".join([f"{_q(src)} AS {_q(req)}" for req, src in resolved_pairs])
                sample_sql = (
                    f'SELECT {col_refs} FROM {self.config.schema_name}."{safe_table_name}" '
                    f'LIMIT {sample_limit}'
                )
        rows = cursor.fetchall() or []
        logger.info("Sample rows fetched for %s: %s", safe_table_name, len(rows))

        inferred: list[dict[str, str]] = []
        varchar_types = {"VARCHAR", "STRING", "TEXT", "UNKNOWN", "VARIANT"}

        sampled_columns = [
            c for c in columns if any(req == c["name"] for req, _src in resolved_pairs)
        ]

        for idx, col in enumerate(sampled_columns):
            declared_type = self._normalize_declared_type(col.get("type", ""))
            values = [r[idx] for r in rows if len(r) > idx and r[idx] is not None]
            logger.debug("%s.%s sample values: %s", safe_table_name, col["name"], values[:5])
            sampled_type = self._infer_type_from_values(values)
            business_type = self._business_rule_type(safe_table_name, col["name"])

            if business_type is not None:
                resolved_type = business_type
            elif declared_type not in varchar_types:
                resolved_type = declared_type
                if sampled_type != "VARCHAR" and sampled_type != declared_type:
                    logger.warning(
                        "Type mismatch for %s.%s (model=%s sample=%s); using model type",
                        safe_table_name,
                        col["name"],
                        declared_type,
                        sampled_type,
                    )
            else:
                resolved_type = sampled_type

            inferred.append({"name": col["name"], "type": resolved_type})
            logger.info(
                "Resolved datatype: %s.%s -> %s (model=%s sample=%s sampled_rows=%s non_null=%s)",
                safe_table_name,
                col["name"],
                resolved_type,
                declared_type,
                sampled_type,
                len(rows),
                len(values),
            )

        logger.info("Inferred types for %s:", safe_table_name)
        for col in inferred:
            logger.info("- %s -> %s", col["name"], col["type"])

        if all(c["type"] == "VARCHAR" for c in inferred):
            logger.warning(
                "All sampled columns resolved to VARCHAR for %s. Applying fallback name-pattern inference.",
                safe_table_name,
            )
            for c in inferred:
                fb = self._fallback_type_from_name(c["name"])
                if fb != "VARCHAR":
                    fallback_logs.append(f"{c['name']}: VARCHAR -> {fb} (name-pattern fallback)")
                    c["type"] = fb

        for entry in fallback_logs:
            logger.info("Fallback decision: %s", entry)

        if all(c["type"] == "VARCHAR" for c in inferred):
            logger.warning(
                "%s: only text-like columns detected after sampling/fallback; keeping VARCHAR types",
                safe_table_name,
            )

        return inferred, fallback_logs

    @staticmethod
    def _business_rule_type(
        table_name: str,
        col_name: str
    ) -> Optional[str]:
        """Business-rule layer for known Salesforce semantic fields."""
        t = (table_name or "").upper()
        c = (col_name or "").strip().upper()
        c_norm = re.sub(r"[^A-Z0-9]", "", c)

        if c_norm == "FIRMNESSOFFIRSTDELIVERYDATE":
            return "VARCHAR"
        if c == "DELETED":
            return "BOOLEAN"
        if "MODSTAMP" in c_norm:
            return "TIMESTAMP"
        if "VOLUME" in c_norm or "AMOUNT" in c_norm:
            return "FLOAT"
        if "DATE" in c_norm and not t.endswith("FIELDHISTORY"):
            return "DATE"

        return None

    @staticmethod
    def _normalize_declared_type(raw_type: str) -> str:
        """Normalize model-declared types to CTAS inference type family."""
        t = (raw_type or "").upper()
        mapping = {
            "INT64": "INTEGER",
            "INT": "INTEGER",
            "INTEGER": "INTEGER",
            "DOUBLE": "FLOAT",
            "FLOAT": "FLOAT",
            "DECIMAL": "DECIMAL",
            "NUMBER": "NUMBER",
            "BOOLEAN": "BOOLEAN",
            "BOOL": "BOOLEAN",
            "DATE": "DATE",
            "DATETIME": "TIMESTAMP",
            "TIMESTAMP": "TIMESTAMP",
            "TIMESTAMP_NTZ": "TIMESTAMP",
            "TIMESTAMP_LTZ": "TIMESTAMP",
            "TIMESTAMP_TZ": "TIMESTAMP",
            "STRING": "VARCHAR",
            "TEXT": "VARCHAR",
            "VARCHAR": "VARCHAR",
            "VARIANT": "VARCHAR",
        }
        return mapping.get(t, "VARCHAR")

    @staticmethod
    def _to_sml_datatype(inferred_type: str) -> DataType:
        mapping = {
            "INTEGER": DataType.INTEGER,
            "FLOAT": DataType.FLOAT,
            "DECIMAL": DataType.DECIMAL,
            "DATE": DataType.DATE,
            "TIMESTAMP": DataType.DATETIME,
            "BOOLEAN": DataType.BOOLEAN,
            "VARCHAR": DataType.STRING,
        }
        return mapping.get(inferred_type, DataType.STRING)

    @staticmethod
    def _to_osi_datatype(inferred_type: str):
        if OSIDataType is None:
            return None
        mapping = {
            "INTEGER": OSIDataType.INTEGER,
            "FLOAT": OSIDataType.FLOAT,
            "DECIMAL": OSIDataType.DECIMAL,
            "DATE": OSIDataType.DATE,
            "TIMESTAMP": OSIDataType.DATETIME,
            "BOOLEAN": OSIDataType.BOOLEAN,
            "VARCHAR": OSIDataType.STRING,
        }
        return mapping.get(inferred_type, OSIDataType.STRING)

    def _dataset_columns_for_ctas_osi(self, dataset: OSIDataset) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for col_name, col in self._collect_physical_source_columns_osi(dataset).items():
            columns.append(
                {
                    "name": col_name,
                    "type": col.data_type.value.upper(),
                }
            )
        return columns

    def _apply_inferred_types_ctas_sml(self, cursor, sml: SMLModel) -> None:
        """Apply inferred datatypes to physical SML source tables via CTAS + SWAP."""
        for dataset in sml.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            full_table = f'{self.config.schema_name}."{safe_table_name}"'
            fixed_table_name = self._build_fixed_table_name(safe_table_name)
            fixed_table = f'{self.config.schema_name}."{fixed_table_name}"'
            base_columns = self._dataset_columns_for_ctas_sml(dataset)
            if not base_columns:
                logger.info(
                    "Skipping CTAS type inference for dataset '%s': no physical columns inferred",
                    dataset.unique_name,
                )
                continue

            columns, _fallbacks = self._infer_columns_from_table_samples(
                cursor,
                safe_table_name,
                base_columns,
            )

            by_name = {c["name"]: c["type"] for c in columns}
            for col in dataset.columns:
                resolved = self._resolve_physical_column_name(dataset, col.unique_name)
                if resolved in by_name:
                    col.data_type = self._to_sml_datatype(by_name[resolved])

            if all(c["type"] == "VARCHAR" for c in columns):
                logger.warning(
                    "Skipping CTAS for %s: all inferred types are VARCHAR (valid text table)",
                    safe_table_name,
                )
                continue

            source_types = self._get_source_column_types(cursor, safe_table_name)
            ctas_sql = self.generate_ctas_sql(
                safe_table_name,
                columns,
                self.config.schema_name,
                source_types=source_types,
                fixed_table_name=fixed_table_name,
            )
            logger.info(f"Applying inferred datatypes via CTAS for table: {safe_table_name}")
            logger.debug(f"Generated CTAS SQL:\n{ctas_sql}")
            self._execute_sql(cursor, ctas_sql, context=f"CTAS {safe_table_name}")
            self._execute_sql(cursor, f"SELECT COUNT(*) FROM {full_table}", context=f"COUNT {safe_table_name}")
            original_count = cursor.fetchone()[0]
            self._execute_sql(cursor, f"SELECT COUNT(*) FROM {fixed_table}", context=f"COUNT {fixed_table_name}")
            fixed_count = cursor.fetchone()[0]
            if original_count != fixed_count:
                raise ConnectorError(
                    f"CTAS safety check failed for {safe_table_name}: original row count {original_count} does not match fixed row count {fixed_count}. Aborting swap."
                )
            self._execute_sql(cursor, f"ALTER TABLE {full_table} SWAP WITH {fixed_table}", context=f"SWAP TABLE {safe_table_name}")
            logger.info("Table swapped successfully: %s", safe_table_name)
            self._execute_sql(cursor, f"DROP TABLE IF EXISTS {fixed_table}", context=f"DROP TABLE {fixed_table_name}")

    def _apply_inferred_types_ctas_osi(self, cursor, osi: OSIModel) -> None:
        """Apply inferred datatypes to physical OSI source tables via CTAS + SWAP."""
        for dataset in osi.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            full_table = f'{self.config.schema_name}."{safe_table_name}"'
            fixed_table_name = self._build_fixed_table_name(safe_table_name)
            fixed_table = f'{self.config.schema_name}."{fixed_table_name}"'
            physical_cols = self._collect_physical_source_columns_osi(dataset)
            base_columns = self._dataset_columns_for_ctas_osi(dataset)
            if not base_columns:
                logger.info(
                    "Skipping CTAS type inference for dataset '%s': no physical columns inferred",
                    dataset.unique_name,
                )
                continue

            columns, _fallbacks = self._infer_columns_from_table_samples(
                cursor,
                safe_table_name,
                base_columns,
            )

            by_name = {c["name"]: c["type"] for c in columns}
            for phys_name, col in physical_cols.items():
                if phys_name in by_name:
                    mapped = self._to_osi_datatype(by_name[phys_name])
                    if mapped is not None:
                        col.data_type = mapped

            if all(c["type"] == "VARCHAR" for c in columns):
                logger.warning(
                    "Skipping CTAS for %s: all inferred types are VARCHAR (valid text table)",
                    safe_table_name,
                )
                continue

            source_types = self._get_source_column_types(cursor, safe_table_name)
            ctas_sql = self.generate_ctas_sql(
                safe_table_name,
                columns,
                self.config.schema_name,
                source_types=source_types,
                fixed_table_name=fixed_table_name,
            )
            logger.info(f"Applying inferred datatypes via CTAS for table: {safe_table_name}")
            logger.debug(f"Generated CTAS SQL:\n{ctas_sql}")
            self._execute_sql(cursor, ctas_sql, context=f"CTAS {safe_table_name}")
            self._execute_sql(cursor, f"SELECT COUNT(*) FROM {full_table}", context=f"COUNT {safe_table_name}")
            original_count = cursor.fetchone()[0]
            self._execute_sql(cursor, f"SELECT COUNT(*) FROM {fixed_table}", context=f"COUNT {fixed_table_name}")
            fixed_count = cursor.fetchone()[0]
            if original_count != fixed_count:
                raise ConnectorError(
                    f"CTAS safety check failed for {safe_table_name}: original row count {original_count} does not match fixed row count {fixed_count}. Aborting swap."
                )
            self._execute_sql(cursor, f"ALTER TABLE {full_table} SWAP WITH {fixed_table}", context=f"SWAP TABLE {safe_table_name}")
            logger.info("Table swapped successfully: %s", safe_table_name)
            self._execute_sql(cursor, f"DROP TABLE IF EXISTS {fixed_table}", context=f"DROP TABLE {fixed_table_name}")

    def evolve_schema(
        self,
        cursor: Any,
        table_name: str,
        new_columns: list[tuple[str, str]]
    ) -> dict[str, str]:
        """Incrementally evolve a Snowflake table schema.

        Compares the existing table structure against the required columns
        from the latest Semantic Snapshot and applies non-destructive changes:
          - New columns â†’ ALTER TABLE ADD COLUMN
          - Removed columns â†’ Renamed with ``_DEPRECATED_`` prefix (Soft Delete)

        Args:
            cursor: Active Snowflake cursor.
            table_name: Fully qualified table name.
            new_columns: List of (column_name, snowflake_type) tuples
                representing the desired schema.

        Returns:
            Dict summarising actions taken, e.g.
            {"added": ["Col_A"], "deprecated": ["Col_B"]}.
        """
        actions: dict[str, list[str]] = {"added": [], "deprecated": []}

        # Fetch existing columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            self._execute_sql(cursor, f"DESC TABLE {table_name}", context=f"DESC TABLE {table_name}")
            existing = {row[0].upper(): row[1] for row in cursor.fetchall()}
        except Exception:
            logger.warning(
                f"Table {table_name} does not exist â€” cannot evolve schema"
            )
            return actions

        desired_upper = {col[0].upper(): col[1] for col in new_columns}

        # ADD new columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for col_name, col_type in new_columns:
            if col_name.upper() not in existing:
                try:
                    ddl = (
                        f'ALTER TABLE {table_name} '
                        f'ADD COLUMN "{col_name.upper()}" {col_type}'
                    )
                    self._execute_sql(cursor, ddl, context=f"ALTER TABLE ADD COLUMN {table_name}.{col_name}")
                    actions["added"].append(col_name)
                    logger.info(f"Schema evolution: added column {col_name}")
                except Exception as e:
                    logger.error(f"Failed to add column {col_name}: {e}")

        # SOFT-DELETE removed columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for existing_col in existing:
            if (
                existing_col not in desired_upper
                and not existing_col.startswith("_DEPRECATED_")
            ):
                new_name = f"_DEPRECATED_{existing_col}"
                try:
                    ddl = (
                        f'ALTER TABLE {table_name} '
                        f'RENAME COLUMN "{existing_col}" TO "{new_name}"'
                    )
                    self._execute_sql(cursor, ddl, context=f"RENAME COLUMN {table_name}.{existing_col}")
                    actions["deprecated"].append(existing_col)
                    logger.info(
                        f"Schema evolution: soft-deleted {existing_col} â†’ {new_name}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to deprecate column {existing_col}: {e}"
                    )

        if actions["added"] or actions["deprecated"]:
            logger.info(
                f"Schema evolution complete for {table_name}: "
                f"+{len(actions['added'])} / "
                f"-{len(actions['deprecated'])} columns"
            )
        return actions
    def _fetch_schema_metadata(self, cursor) -> Dict[str, set]:
        """Fetch all table and column names from the current schema for pre-validation."""
        query = (
            "SELECT table_name, column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = %s"
        )
        self._execute_sql(cursor, query, (self.config.schema_name.upper(),), context="FETCH SCHEMA METADATA")
        
        metadata: Dict[str, set] = {}
        for row in cursor.fetchall():
            table = row[0].upper()
            column = row[1].upper()
            if table not in metadata:
                metadata[table] = set()
            metadata[table].add(column)
        return metadata

    def _fetch_model_table_metadata(self, cursor, datasets: list[Any]) -> Dict[str, set[str]]:
        """Fetch metadata only for tables referenced in the current model."""
        table_names = [
            (d.source_table or d.unique_name).upper()
            for d in datasets
        ]
        if not table_names:
            return {}

        placeholders = ", ".join(["%s"] * len(table_names))
        query = (
            f"SELECT table_name, column_name "
            f"FROM information_schema.columns "
            f"WHERE table_schema = %s AND table_name IN ({placeholders})"
        )
        params = (self.config.schema_name.upper(), *table_names)
        self._execute_sql(cursor, query, params, context="FETCH MODEL METADATA")

        metadata: Dict[str, set[str]] = {}
        for row in cursor.fetchall():
            table = row[0].upper()
            column = row[1].upper()
            if table not in metadata:
                metadata[table] = set()
            metadata[table].add(column)
        return metadata

    def _preflight_check_osi(self, cursor, osi: OSIModel) -> None:
        """Verify all referenced tables in OSI model exist in Snowflake."""
        self._execute_sql(cursor, f"SHOW TABLES IN SCHEMA {self._schema_fqn()}", context="PREFLIGHT SHOW TABLES")
        existing_tables = {row[1].upper() for row in cursor.fetchall()}
        
        self._execute_sql(cursor, f"SHOW VIEWS IN SCHEMA {self._schema_fqn()}", context="PREFLIGHT SHOW VIEWS")
        existing_views = {row[1].upper() for row in cursor.fetchall()}
        
        all_objects = existing_tables | existing_views
        
        missing = []
        for ds in osi.datasets:
            source = (ds.source_table or ds.unique_name).upper()
            if source not in all_objects:
                missing.append(source)
        
        if missing:
            logger.error(f"Preflight check failed: Missing tables/views: {missing}")
            if self.sf_behavior.create_missing_tables:
                logger.info("create_missing_tables=True: will attempt to create missing tables during deployment.")
            else:
                raise ConnectorError(f"Missing source objects in Snowflake: {missing}")

    def _drop_deprecated_views(self, cursor, model: Any) -> None:
        """
        Drop legacy semantic views ending in _SV.
        These are replaced by Cortex Analyst compatible views.
        """
        view_name = self._get_safe_object_name(model.unique_name or model.label)
        legacy_view = f"{self.config.database}.{self.config.schema_name}.{view_name}_SV"
        
        try:
            logger.info(f"Cleaning up legacy view: {legacy_view}")
            self._execute_sql(cursor, f"DROP VIEW IF EXISTS {legacy_view}", context=f"DROP VIEW {legacy_view}")
        except Exception as e:
            logger.warning(f"Failed to drop legacy view {legacy_view}: {e}")

    @staticmethod
    def _resolve_column_name_for_dataset(
        known_columns: set[str],
        candidate: str
    ) -> Optional[str]:
        """Resolve LLM-drifted column names to an actual dataset column."""
        if not known_columns:
            return None

        if candidate in known_columns:
            return candidate

        # underscore-insensitive match: IS_VAN_ARSDEL -> ISVANARSDEL
        compact = candidate.replace("_", "")
        for col in known_columns:
            if col.replace("_", "") == compact:
                return col
        return None

    @staticmethod
    def _is_history_table_name(name: str) -> bool:
        """Return True for Salesforce-like change-history tables."""
        upper = str(name or "").upper()
        return (
            upper.endswith("_HISTORY")
            or upper.endswith("HISTORY")
            or "FIELDHISTORY" in upper
        )

    @staticmethod
    def _resolve_history_snapshot_keys(physical_columns: list[str]) -> tuple[Optional[str], Optional[str]]:
        """Choose parent/timestamp columns for latest-row snapshoting."""
        if not physical_columns:
            return None, None

        by_upper = {str(col).upper(): col for col in physical_columns}

        parent_candidates = (
            "PARENT_ID", "PARENTID", "QUOTE_ID", "RECORD_ID", "ENTITY_ID", "ID",
        )
        timestamp_candidates = (
            "CREATED_DATE", "CREATEDDATE", "LAST_MODIFIED_DATE", "LASTMODIFIEDDATE",
            "SYSTEMMODSTAMP", "MODSTAMP", "TIMESTAMP",
        )

        parent_col = next((by_upper[c] for c in parent_candidates if c in by_upper), None)
        timestamp_col = next((by_upper[c] for c in timestamp_candidates if c in by_upper), None)
        return parent_col, timestamp_col

    def _build_history_snapshot_ddls_for_sml(
        self,
        sml: SMLModel
    ) -> tuple[list[str], dict[str, str]]:
        """Build helper latest-row views for history datasets to avoid join fan-out."""
        history_view_ddls: list[str] = []
        dataset_source_overrides: dict[str, str] = {}
        emitted_views: set[str] = set()

        for dataset in sml.datasets:
            source_name = dataset.source_table or dataset.unique_name
            if not self._is_history_table_name(source_name):
                continue

            physical_columns = list(self._collect_physical_source_columns(dataset).keys())
            parent_col, timestamp_col = self._resolve_history_snapshot_keys(physical_columns)
            if not parent_col or not timestamp_col:
                logger.info(
                    "History snapshot bypassed for dataset '%s': missing parent/timestamp columns",
                    dataset.unique_name,
                )
                continue

            safe_source = self._safe_table_name(source_name)
            safe_latest_view = self._safe_table_name(f"{safe_source}_LATEST")
            if safe_latest_view in emitted_views:
                dataset_source_overrides[dataset.unique_name] = safe_latest_view
                continue

            full_source = f'"{self.config.database}"."{self.config.schema_name}"."{safe_source}"'
            full_latest = f'"{self.config.database}"."{self.config.schema_name}"."{safe_latest_view}"'
            select_cols = ", ".join(f'"{col}"' for col in physical_columns)
            ddl = (
                f"CREATE OR REPLACE VIEW {full_latest} AS\n"
                f"SELECT {select_cols}\n"
                f"FROM {full_source}\n"
                f'QUALIFY ROW_NUMBER() OVER (PARTITION BY "{parent_col}" '
                f'ORDER BY "{timestamp_col}" DESC NULLS LAST) = 1'
            )

            history_view_ddls.append(ddl)
            emitted_views.add(safe_latest_view)
            dataset_source_overrides[dataset.unique_name] = safe_latest_view
            logger.info(
                "History dataset '%s' mapped to latest-snapshot view '%s' using (%s, %s)",
                dataset.unique_name,
                safe_latest_view,
                parent_col,
                timestamp_col,
            )

        return history_view_ddls, dataset_source_overrides

    def _build_history_snapshot_ddls_for_osi(
        self,
        osi: OSIModel
    ) -> tuple[list[str], dict[str, str]]:
        """OSI equivalent of history latest-row helper view generation."""
        history_view_ddls: list[str] = []
        dataset_source_overrides: dict[str, str] = {}
        emitted_views: set[str] = set()

        for dataset in osi.datasets:
            source_name = dataset.source_table or dataset.unique_name
            if not self._is_history_table_name(source_name):
                continue

            physical_columns = list(self._collect_physical_source_columns_osi(dataset).keys())
            parent_col, timestamp_col = self._resolve_history_snapshot_keys(physical_columns)
            if not parent_col or not timestamp_col:
                logger.info(
                    "History snapshot bypassed for OSI dataset '%s': missing parent/timestamp columns",
                    dataset.unique_name,
                )
                continue

            safe_source = self._safe_table_name(source_name)
            safe_latest_view = self._safe_table_name(f"{safe_source}_LATEST")
            if safe_latest_view in emitted_views:
                dataset_source_overrides[dataset.unique_name] = safe_latest_view
                continue

            full_source = f'"{self.config.database}"."{self.config.schema_name}"."{safe_source}"'
            full_latest = f'"{self.config.database}"."{self.config.schema_name}"."{safe_latest_view}"'
            select_cols = ", ".join(f'"{col}"' for col in physical_columns)
            ddl = (
                f"CREATE OR REPLACE VIEW {full_latest} AS\n"
                f"SELECT {select_cols}\n"
                f"FROM {full_source}\n"
                f'QUALIFY ROW_NUMBER() OVER (PARTITION BY "{parent_col}" '
                f'ORDER BY "{timestamp_col}" DESC NULLS LAST) = 1'
            )

            history_view_ddls.append(ddl)
            emitted_views.add(safe_latest_view)
            dataset_source_overrides[dataset.unique_name] = safe_latest_view
            logger.info(
                "OSI history dataset '%s' mapped to latest-snapshot view '%s' using (%s, %s)",
                dataset.unique_name,
                safe_latest_view,
                parent_col,
                timestamp_col,
            )

        return history_view_ddls, dataset_source_overrides
