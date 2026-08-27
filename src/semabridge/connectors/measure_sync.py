from __future__ import annotations
import logging
import datetime
import json
import re
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING, TypeAlias

from semabridge.utils.logger import get_logger
from semabridge.core.exceptions import ConnectorError
from semabridge.formats.sml.models import SMLModel, SMLMetric, DataType
from semabridge.connectors.schema_compatibility_validator import SchemaCompatibilityValidator

if TYPE_CHECKING:
    from semabridge.intermediate.models import OSIModel, OSIDataset, OSIMetric, OSIDataType
else:
    try:
        from semabridge.intermediate.models import OSIModel, OSIDataset, OSIMetric, OSIDataType
    except ImportError:
        OSIModel: TypeAlias = Any
        OSIDataset: TypeAlias = Any
        OSIMetric: TypeAlias = Any
        OSIDataType = None

logger = get_logger(__name__)

class MeasureSynchronizer:
    def __init__(self, config, behavior, identifier_sanitizer, schema_manager=None, connection_manager=None, translator=None):
        self.config = config
        self.behavior = behavior
        self.sf_behavior = behavior.snowflake
        self._id = identifier_sanitizer
        self.schema_manager = schema_manager
        self.connection_manager = connection_manager
        self.translator = translator

    
    def _build_safe_sum_sql(
        self,
        expr_sql: str,
        identifier_hint: Optional[str] = None,
        is_boolean_column: Optional[bool] = None,
        column_data_types: Optional[Dict[Any, str]] = None,
    ) -> str:
        if is_boolean_column is not None:
            is_flag = is_boolean_column
        else:
            is_flag = False
            if identifier_hint:
                hint = identifier_hint.strip().upper().replace('"', '')
                col_name = hint.split('.')[-1] if '.' in hint else hint
                flag_patterns = [
                    r'^IS_', r'^HAS_', r'^WAS_', r'^DID_', r'^DOES_',
                    r'_FLAG$', r'_FLG$', r'^DELETED$', r'_DELETED$'
                ]
                is_flag = any(re.search(p, col_name) for p in flag_patterns)

                type_lookup = column_data_types or getattr(self, "column_data_types", None) or {}
                if is_flag and type_lookup:
                    col_type = ""
                    for k, v in type_lookup.items():
                        k_str = str(k[1] if isinstance(k, tuple) else k).upper()
                        if k_str == col_name or k_str == hint:
                            col_type = str(v).lower()
                            break
                    if any(st in col_type for st in ("string", "varchar", "char", "text")):
                        is_flag = False

        if is_flag:
            return f"SUM(IFF({expr_sql} = 1 OR {expr_sql} = TRUE, 1, 0))"

        if expr_sql.strip().upper().endswith("::FLOAT"):
            return f"SUM({expr_sql})"
        return f"SUM({expr_sql}::FLOAT)"

    def _detect_boolean_columns(self, data: list[dict]) -> Dict[str, bool]:
        """Authoritative True/False per column, derived from the actual synced
        row values (Power BI's JSON API preserves true/false as native Python
        bool) — not from column naming. Every column present in `data` gets a
        definitive entry, overriding the name heuristic in _build_safe_sum_sql;
        only columns absent from the synced data fall through to that
        heuristic.
        """
        if not data:
            return {}
        return {
            self._id.sanitize_column(col): isinstance(val, bool)
            for col, val in data[0].items()
        }

    def generate_semantic_view_tiered(
        self,
        model_name: str,
        shadow_table: str,
        triage_results: Dict[str, Any],
        grain_dimensions: list[str],
        column_types: Optional[Dict[str, bool]] = None,
    ) -> str:
        """Generate a Snowflake VIEW that reconstructs measures per tier.

        The view never exposes the raw shadow table to users.  It provides
        business-friendly column aliases and reconstructs Tier 3 ratios.

        Args:
            model_name: Semantic model display name for view naming.
            shadow_table: Fully qualified shadow table reference.
            triage_results: Dict of metric_name → TriageResult.
            grain_dimensions: Dimension column names used in GROUP BY.
            column_types: Optional sanitized-column-name -> is_boolean map
                (see _detect_boolean_columns) used to build safe SUM
                expressions from real data types instead of column naming.

        Returns:
            A CREATE OR REPLACE VIEW DDL string.
        """
        column_types = column_types or {}
        view_name = f"V_{self._id.sanitize_table_name(model_name)}"
        full_view = (
            f"{self.config.database}.{self.config.schema_name}."
            f'"{view_name}"'
        )

        select_parts: list[str] = []
        group_by_parts: list[str] = []

        # Dimensions ─────────────────────────────────────────────────────────
        for dim in grain_dimensions:
            safe_dim = self._id.sanitize_column(dim)
            select_parts.append(f'    base."{safe_dim}"')
            group_by_parts.append(f'base."{safe_dim}"')

        # Measures ───────────────────────────────────────────────────────────
        for metric_name, triage in triage_results.items():
            safe = self._id.sanitize_column(metric_name)
            safe_expr = f'base."{safe}"'

            if triage.strategy.value == "passthrough":
                # Tier 1: simple pass-through aggregation
                select_parts.append(
                    f'    {self._build_safe_sum_sql(safe_expr, safe, is_boolean_column=column_types.get(safe))} AS "{safe}"'
                )

            elif triage.strategy.value == "aligned_history":
                # Tier 2: base value + companion columns
                select_parts.append(
                    f'    {self._build_safe_sum_sql(safe_expr, safe, is_boolean_column=column_types.get(safe))} AS "{safe}"'
                )
                for suffix in triage.aligned_measures:
                    alias = self._id.sanitize_column(f"{metric_name}{suffix}")
                    alias_expr = f'base."{alias}"'
                    select_parts.append(
                        f'    {self._build_safe_sum_sql(alias_expr, alias, is_boolean_column=column_types.get(alias))} AS "{alias}"'
                    )

            elif triage.strategy.value == "decomposition":
                if triage.components:
                    # Tier 3: reconstruct ratio from components
                    num_col = self._id.sanitize_column(f"{metric_name}_Num")
                    den_col = self._id.sanitize_column(f"{metric_name}_Denom")
                    num_expr = f'base."{num_col}"'
                    den_expr = f'base."{den_col}"'
                    select_parts.append(
                        f'    {self._build_safe_sum_sql(num_expr, num_col, is_boolean_column=column_types.get(num_col))} / '
                        f'NULLIF({self._build_safe_sum_sql(den_expr, den_col, is_boolean_column=column_types.get(den_col))}, 0) AS "{safe}"'
                    )
                else:
                    # Tier 3 without decomposition — pass-through
                    select_parts.append(
                        f'    {self._build_safe_sum_sql(safe_expr, safe, is_boolean_column=column_types.get(safe))} AS "{safe}"'
                    )

        select_block = ",\n".join(select_parts)
        group_block = ", ".join(group_by_parts)

        ddl = (
            f"CREATE OR REPLACE VIEW {full_view} AS\n"
            f"SELECT\n{select_block}\n"
            f"FROM {shadow_table} base\n"
            f"GROUP BY {group_block};"
        )

        logger.info(f"Generated tiered semantic view: {full_view}")
        return ddl

    def sync_measure_data(
        self,
        measure_name: str,
        data: list[dict],
        target_table: str = None,
        dimension_columns: list[str] = None,
        write_mode: str = "overwrite"
    ) -> bool:
        """
        Write materialized measure data to Snowflake.
        
        Creates a MEASURES_ prefixed table with the synced data from DAX query
        results. This allows complex measures to be pre-computed and queried
        directly in Snowflake.
        
        Args:
            measure_name: Original measure name (for metadata/logging)
            data: List of row dictionaries from DAX query
            target_table: Optional target table name (defaults to measure name)
            dimension_columns: Dimension column names for schema inference
            write_mode: "overwrite" to replace, "append" to add, "merge" for upsert
            
        Returns:
            True if successful
        """
        import snowflake.connector
        
        if not data:
            logger.warning(f"No data to sync for measure {measure_name}")
            return False
        
        # Generate table name from measure
        table_base = target_table or measure_name
        safe_table = f"MEASURES_{self._id.sanitize_table_name(table_base)}"
        full_table = f'{self.config.database}.{self.config.schema_name}."{safe_table}"'
        
        # Infer schema from first row
        first_row = data[0]
        columns = list(first_row.keys())
        
        # Map Python types to Snowflake types with smart inference
        type_map = []
        for col in columns:
            val = first_row[col]
            if isinstance(val, bool):
                type_map.append((col, "BOOLEAN"))
            elif isinstance(val, int):
                type_map.append((col, "INTEGER"))
            elif isinstance(val, float):
                type_map.append((col, "FLOAT"))
            elif isinstance(val, (list, dict)):
                type_map.append((col, "VARIANT"))
            else:
                # Check if it looks like a date
                str_val = str(val) if val else ""
                if len(str_val) == 10 and "-" in str_val:
                    type_map.append((col, "DATE"))
                else:
                    type_map.append((col, "VARCHAR(500)"))
        
        try:
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
            kwargs = get_snowflake_connect_kwargs(self.config)
            kwargs["session_parameters"] = {
                "QUERY_TAG": self.sf_behavior.query_tag or "Semabridge_MeasureSync"
            }
            conn = snowflake.connector.connect(**kwargs)
            cur = conn.cursor()
            
            try:
                # Build schema type map for this measure
                type_map = []
                for col in columns:
                    val = first_row[col]
                    if isinstance(val, bool):
                        type_map.append((col, "BOOLEAN"))
                    elif isinstance(val, int):
                        type_map.append((col, "INTEGER"))
                    elif isinstance(val, float):
                        type_map.append((col, "FLOAT"))
                    elif isinstance(val, (list, dict)):
                        type_map.append((col, "VARIANT"))
                    else:
                        # Check if it looks like a date
                        str_val = str(val) if val else ""
                        if len(str_val) == 10 and "-" in str_val:
                            type_map.append((col, "DATE"))
                        else:
                            type_map.append((col, "VARCHAR(500)"))
                
                # Prepare expected column info for validation
                expected_columns = {self._id.sanitize_column(c[0]): c[1] for c in type_map}
                col_defs = ", ".join([f'"{self._id.sanitize_column(c[0])}" {c[1]}' for c in type_map])
                
                # Check if table exists and validate compatibility
                validator = SchemaCompatibilityValidator(cur, self.config)
                validation_result = validator.validate_table(
                    table_name=safe_table,
                    join_columns=set(expected_columns.keys()),
                    join_column_types=expected_columns
                )
                
                if write_mode == "overwrite":
                    if validation_result.table_exists:
                        # Table exists - check if it's compatible
                        if not validation_result.is_compatible:
                            # Schema mismatch - abort to preserve data
                            error_msg = (
                                f"Cannot overwrite {full_table}: existing table has incompatible schema.\n"
                                f"{validation_result.error_message()}\n"
                                f"To proceed, manually drop the table or change the target table name."
                            )
                            raise ConnectorError(error_msg)
                        # Compatible - truncate and reuse
                        logger.info(f"Existing table {full_table} is compatible. Truncating and reusing.")
                        self.connection_manager._execute_sql(
                            cur, 
                            f"TRUNCATE TABLE {full_table}", 
                            context=f"TRUNCATE TABLE {full_table}"
                        )
                    else:
                        # Table doesn't exist - create it
                        logger.info(f"Creating new table {full_table}")
                        self.connection_manager._execute_sql(
                            cur,
                            f"CREATE TABLE IF NOT EXISTS {full_table} ({col_defs})",
                            context=f"CREATE TABLE IF NOT EXISTS {full_table}"
                        )
                        
                elif write_mode == "append":
                    if not validation_result.table_exists:
                        # Create table if it doesn't exist
                        logger.info(f"Creating new table {full_table} for append mode")
                        self.connection_manager._execute_sql(
                            cur,
                            f"CREATE TABLE IF NOT EXISTS {full_table} ({col_defs})",
                            context=f"CREATE TABLE IF NOT EXISTS {full_table}"
                        )
                    elif not validation_result.is_compatible:
                        # In append mode, also validate for compatibility
                        error_msg = (
                            f"Cannot append to {full_table}: existing table has incompatible schema.\n"
                            f"{validation_result.error_message()}"
                        )
                        raise ConnectorError(error_msg)
                    else:
                        logger.info(f"Appending to existing compatible table {full_table}")
                
                # Insert data in batches for performance
                batch_size = 10000
                total_inserted = 0
                
                for i in range(0, len(data), batch_size):
                    batch = data[i:i + batch_size]
                    
                    # Build column list
                    col_list = ", ".join([f'"{self._id.sanitize_column(c)}"' for c in columns])
                    
                    # Build VALUES clause
                    value_rows = []
                    for row in batch:
                        vals = []
                        for col in columns:
                            val = row.get(col)
                            if val is None:
                                vals.append("NULL")
                            elif isinstance(val, bool):
                                vals.append("TRUE" if val else "FALSE")
                            elif isinstance(val, (int, float)):
                                vals.append(str(val))
                            elif isinstance(val, (list, dict)):
                                vals.append(f"PARSE_JSON('{json.dumps(val)}')")
                            else:
                                # Escape single quotes in strings
                                escaped = str(val).replace("'", "''")
                                vals.append(f"'{escaped}'")
                        value_rows.append(f"({', '.join(vals)})")
                    
                    # Execute batch insert
                    insert_sql = f"INSERT INTO {full_table} ({col_list}) VALUES {', '.join(value_rows)}"
                    self.connection_manager._execute_sql(cur, insert_sql, context=f"INSERT INTO {full_table}")
                    total_inserted += len(batch)
                    
                    if len(data) > batch_size:
                        logger.debug(f"Inserted batch {i//batch_size + 1}: {len(batch)} rows")
                
                logger.info(f"Successfully synced {total_inserted} rows to {full_table}")
                
                # Add metadata about sync time
                try:
                    sync_time = datetime.datetime.utcnow().isoformat()
                    self.connection_manager._execute_sql(
                        cur,
                        f"COMMENT ON TABLE {full_table} IS 'DAX Measure: {measure_name} | Synced: {sync_time}Z'",
                        context=f"COMMENT ON TABLE {full_table}",
                    )
                except:
                    pass
                    
                return True

                
            finally:
                cur.close()
                conn.close()
                
        except Exception as e:
            logger.error(f"Failed to sync measure data: {e}")
            raise

    def sync_all_measures(
        self,
        sml: SMLModel,
        fabric_extractor,  # FabricExtractor instance
        dataset_id: str,
        grain_dimensions: list[str] | None = None
    ) -> dict:
        """Sync all syncable measures using the Universal Sync Protocol.

        Orchestrates the full triage → materialisation → evolution → view
        pipeline:

        1. Classify every metric via ``MeasureTriage``.
        2. Build a batch ``SUMMARIZECOLUMNS`` DAX query via
           ``MaterializationQueryBuilder``.
        3. Execute the query against Fabric.
        4. Write results to a shadow table via ``sync_measure_data``.
        5. Evolve the schema if the table already exists.
        6. Generate a tiered Semantic View.

        Falls back to per-metric sync when the batch path fails.

        Args:
            sml: The SML model containing metrics.
            fabric_extractor: Configured FabricExtractor instance.
            dataset_id: Fabric dataset ID for DAX queries.
            grain_dimensions: Override grain dimensions. Defaults to
                ``["'Date'[Year]"]``.

        Returns:
            Dict with sync results per measure.
        """
        from semabridge.converter.measure_triage import MeasureTriage
        from semabridge.converter.materialization_builder import (
            MaterializationQueryBuilder,
        )

        results: dict = {}
        grain = grain_dimensions or ["'Date'[Year]"]

        # Filter to syncable measures
        syncable = [m for m in sml.metrics if m.sync_enabled and not m.is_hidden]
        logger.info(f"Syncing {len(syncable)} measures (of {len(sml.metrics)} total)")

        if not syncable:
            logger.info("No syncable measures found — skipping")
            return results

        # ── Step 1: Triage ──────────────────────────────────────────────────
        triage = MeasureTriage()
        triage_results = triage.classify_all(syncable)

        # ── Step 2: Build batch DAX query ───────────────────────────────────
        builder = MaterializationQueryBuilder()
        try:
            batch_query = builder.build_query(
                metrics=syncable,
                triage_results=triage_results,
                grain_dimensions=grain,
            )
        except Exception as e:
            logger.warning(f"Batch query build failed: {e} — falling back to per-metric sync")
            batch_query = ""

        # ── Step 3: Execute & write ─────────────────────────────────────────
        if batch_query:
            try:
                data = fabric_extractor.execute_dax_query(dataset_id, batch_query)

                if data:
                    model_base = sml.unique_name or sml.label or "SEMABRIDGE"
                    self.sync_measure_data(
                        measure_name=model_base,
                        data=data,
                        target_table=f"SHADOW_{self._id.sanitize_table_name(model_base)}",
                        dimension_columns=grain,
                    )

                    # ── Step 4: Schema evolution ────────────────────────────
                    shadow_table = (
                        f"{self.config.database}.{self.config.schema_name}."
                        f'"MEASURES_SHADOW_{self._id.sanitize_table_name(model_base)}"'
                    )
                    # Determine desired columns from the first result row
                    if data:
                        new_cols = [
                            (self._id.sanitize_column(k), "VARCHAR(500)")
                            for k in data[0].keys()
                        ]
                        try:
                            import snowflake.connector
                            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
                            kwargs = get_snowflake_connect_kwargs(self.config)
                            conn = snowflake.connector.connect(**kwargs)
                            cur = conn.cursor()
                            try:
                                self.schema_manager.evolve_schema(cur, shadow_table, new_cols)
                            finally:
                                cur.close()
                                conn.close()
                        except Exception as evo_err:
                            logger.warning(f"Schema evolution skipped: {evo_err}")

                    # ── Step 5: Generate tiered view ────────────────────────
                    try:
                        view_ddl = self.generate_semantic_view_tiered(
                            model_name=model_base,
                            shadow_table=shadow_table,
                            triage_results=triage_results,
                            grain_dimensions=[
                                self._id.sanitize_column(d) for d in grain
                            ],
                            column_types=self._detect_boolean_columns(data),
                        )
                        logger.debug(f"View DDL:\n{view_ddl}")
                    except Exception as view_err:
                        logger.warning(f"Tiered view generation failed: {view_err}")

                    for m in syncable:
                        results[m.unique_name] = {"status": "success", "rows": len(data)}
                else:
                    for m in syncable:
                        results[m.unique_name] = {"status": "empty", "rows": 0}

                return results

            except Exception as batch_err:
                logger.warning(
                    f"Batch sync failed: {batch_err} — falling back to per-metric sync"
                )

        # ── Fallback: per-metric sync (original behaviour) ──────────────────
        for metric in syncable:
            try:
                dimensions = metric.group_by_dimensions or grain

                if metric.requires_time_intel or metric.partition_dimension:
                    partition_col = metric.partition_dimension or "'Date'[Year]"
                    partition_vals = fabric_extractor.get_date_dimension_values(
                        dataset_id, partition_col
                    )

                    if partition_vals:
                        data = fabric_extractor.execute_paginated_measure_sync(
                            dataset_id=dataset_id,
                            measure_name=f"[{metric.unique_name}]",
                            group_by_dimensions=dimensions,
                            partition_column=partition_col,
                            partition_values=partition_vals,
                        )
                    else:
                        data = fabric_extractor.execute_measure_sync_query(
                            dataset_id=dataset_id,
                            measure_name=f"[{metric.unique_name}]",
                            group_by_dimensions=dimensions,
                        )
                else:
                    data = fabric_extractor.execute_measure_sync_query(
                        dataset_id=dataset_id,
                        measure_name=f"[{metric.unique_name}]",
                        group_by_dimensions=dimensions,
                    )

                if data:
                    self.sync_measure_data(
                        measure_name=metric.unique_name,
                        data=data,
                        dimension_columns=dimensions,
                    )
                    results[metric.unique_name] = {
                        "status": "success",
                        "rows": len(data),
                    }
                else:
                    results[metric.unique_name] = {"status": "empty", "rows": 0}

            except Exception as e:
                logger.error(f"Failed to sync measure {metric.unique_name}: {e}")
                results[metric.unique_name] = {"status": "failed", "error": str(e)}

        # Summary
        success = sum(1 for r in results.values() if r.get("status") == "success")
        failed = sum(1 for r in results.values() if r.get("status") == "failed")
        logger.info(f"Measure sync complete: {success} success, {failed} failed")

        return results

    def sync_all_measures_from_osi(
        self,
        osi: OSIModel,
        fabric_extractor,
        dataset_id: str,
        grain_dimensions: list[str] | None = None
    ) -> dict:
        """Sync all syncable measures from an OSI model (no SML dependency).

        OSI-native counterpart of :meth:`sync_all_measures`.  Operates
        directly on ``OSIModel`` metrics which now carry ``sync_enabled``,
        ``group_by_dimensions``, ``requires_time_intel`` and
        ``partition_dimension`` fields set by the safety pipeline.

        Args:
            osi: The OSI model containing metrics.
            fabric_extractor: Configured FabricExtractor instance.
            dataset_id: Fabric dataset ID for DAX queries.
            grain_dimensions: Override grain dimensions.

        Returns:
            Dict with sync results per measure.
        """
        from semabridge.converter.measure_triage import MeasureTriage
        from semabridge.converter.materialization_builder import (
            MaterializationQueryBuilder,
        )

        results: dict = {}
        grain = grain_dimensions or ["'Date'[Year]"]

        # Filter to syncable metrics
        syncable = [m for m in osi.metrics if m.sync_enabled and not m.is_hidden]
        logger.info(f"[OSI] Syncing {len(syncable)} measures (of {len(osi.metrics)} total)")

        if not syncable:
            logger.info("No syncable measures found — skipping")
            return results

        # ── Step 1: Triage ──────────────────────────────────────────────────
        triage = MeasureTriage()
        triage_results = triage.classify_all(syncable)

        # ── Step 2: Build batch DAX query ───────────────────────────────────
        builder = MaterializationQueryBuilder()
        try:
            batch_query = builder.build_query(
                metrics=syncable,
                triage_results=triage_results,
                grain_dimensions=grain,
            )
        except Exception as e:
            logger.warning(f"Batch query build failed: {e} — falling back to per-metric sync")
            batch_query = ""

        # ── Step 3: Execute & write ─────────────────────────────────────────
        if batch_query:
            try:
                data = fabric_extractor.execute_dax_query(dataset_id, batch_query)

                if data:
                    model_base = osi.unique_name or osi.label or "SEMABRIDGE"
                    self.sync_measure_data(
                        measure_name=model_base,
                        data=data,
                        target_table=f"SHADOW_{self._id.sanitize_table_name(model_base)}",
                        dimension_columns=grain,
                    )

                    # ── Step 4: Schema evolution ────────────────────────────
                    shadow_table = (
                        f"{self.config.database}.{self.config.schema_name}."
                        f'"MEASURES_SHADOW_{self._id.sanitize_table_name(model_base)}"'
                    )
                    if data:
                        new_cols = [
                            (self._id.sanitize_column(k), "VARCHAR(500)")
                            for k in data[0].keys()
                        ]
                        try:
                            import snowflake.connector
                            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
                            kwargs = get_snowflake_connect_kwargs(self.config)
                            conn = snowflake.connector.connect(**kwargs)
                            cur = conn.cursor()
                            try:
                                self.schema_manager.evolve_schema(cur, shadow_table, new_cols)
                            finally:
                                cur.close()
                                conn.close()
                        except Exception as evo_err:
                            logger.warning(f"Schema evolution skipped: {evo_err}")

                    # ── Step 5: Generate tiered view ────────────────────────
                    try:
                        view_ddl = self.generate_semantic_view_tiered(
                            model_name=model_base,
                            shadow_table=shadow_table,
                            triage_results=triage_results,
                            grain_dimensions=[
                                self._id.sanitize_column(d) for d in grain
                            ],
                            column_types=self._detect_boolean_columns(data),
                        )
                        logger.debug(f"View DDL:\n{view_ddl}")
                    except Exception as view_err:
                        logger.warning(f"Tiered view generation failed: {view_err}")

                    for m in syncable:
                        results[m.unique_name] = {"status": "success", "rows": len(data)}
                else:
                    for m in syncable:
                        results[m.unique_name] = {"status": "empty", "rows": 0}

                return results

            except Exception as batch_err:
                logger.warning(
                    f"Batch sync failed: {batch_err} — falling back to per-metric sync"
                )

        # ── Fallback: per-metric sync ───────────────────────────────────────
        for metric in syncable:
            try:
                dimensions = metric.group_by_dimensions or grain

                if metric.requires_time_intel or metric.partition_dimension:
                    partition_col = metric.partition_dimension or "'Date'[Year]"
                    partition_vals = fabric_extractor.get_date_dimension_values(
                        dataset_id, partition_col
                    )

                    if partition_vals:
                        data = fabric_extractor.execute_paginated_measure_sync(
                            dataset_id=dataset_id,
                            measure_name=f"[{metric.unique_name}]",
                            group_by_dimensions=dimensions,
                            partition_column=partition_col,
                            partition_values=partition_vals,
                        )
                    else:
                        data = fabric_extractor.execute_measure_sync_query(
                            dataset_id=dataset_id,
                            measure_name=f"[{metric.unique_name}]",
                            group_by_dimensions=dimensions,
                        )
                else:
                    data = fabric_extractor.execute_measure_sync_query(
                        dataset_id=dataset_id,
                        measure_name=f"[{metric.unique_name}]",
                        group_by_dimensions=dimensions,
                    )

                if data:
                    self.sync_measure_data(
                        measure_name=metric.unique_name,
                        data=data,
                        dimension_columns=dimensions,
                    )
                    results[metric.unique_name] = {
                        "status": "success",
                        "rows": len(data),
                    }
                else:
                    results[metric.unique_name] = {"status": "empty", "rows": 0}

            except Exception as e:
                logger.error(f"Failed to sync measure {metric.unique_name}: {e}")
                results[metric.unique_name] = {"status": "failed", "error": str(e)}

        # Summary
        success = sum(1 for r in results.values() if r.get("status") == "success")
        failed = sum(1 for r in results.values() if r.get("status") == "failed")
        logger.info(f"Measure sync complete: {success} success, {failed} failed")

        return results

    def _should_use_direct_metric_aggregation(self, metric: Any) -> bool:
        """Prefer deterministic aggregation SQL for simple source-column metrics."""
        if not getattr(metric, "source_column", None) or not getattr(metric, "aggregation", None):
            return False

        complexity_tier = getattr(metric, "complexity_tier", 1) or 1
        if complexity_tier > 1:
            return False

        expr = (getattr(metric, "expression", None) or "").strip().upper()
        if not expr:
            return True
        
        return False

    def _validate_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None
    ) -> Tuple[bool, Optional[str]]:
        """Validate that metric SQL references only columns that exist in the schema."""
        import re
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        patterns = [
            r'(?:"(\w+)"|(\w+))\."([^"]+)"',                 # table."ColumnName"
            r'(?:"(\w+)"|(\w+))\.([A-Za-z_][A-Za-z0-9_]*)',    # table.ColumnName
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, metric_sql):
                alias = m.group(1) or m.group(2)
                col = m.group(3)
                if alias in alias_to_dataset:
                    ds_name = alias_to_dataset[alias]
                    known_cols = dataset_col_lookup.get(ds_name, set())
                    if col.upper() not in {c.upper() for c in known_cols}:
                        return False, f"Column '{col}' not found in dataset '{ds_name}' (alias '{alias}')"
                elif metric_names and alias in metric_names:
                    pass
                else:
                    return False, f"Unknown table alias or metric reference: '{alias}'"
        return True, None

    def _normalize_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
        preferred_table_alias: Optional[str] = None
    ) -> str:
        """Normalize column references in metric SQL to use unquoted uppercase identifiers."""
        import re
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        normalized_sql = metric_sql

        def _repl(m):
            alias = m.group(1) or m.group(2)
            col = m.group(3)
            if alias in alias_to_dataset:
                ds_name = alias_to_dataset[alias]
                known_cols = dataset_col_lookup.get(ds_name, set())
                actual_col = self.translator._resolve_column_name_for_dataset(known_cols, col)
                if actual_col:
                    return f'{alias}."{actual_col}"'
            return m.group(0)

        normalized_sql = re.sub(r'(?:"(\w+)"|(\w+))\."([^"]+)"', _repl, normalized_sql)
        normalized_sql = re.sub(r'(?:"(\w+)"|(\w+))\.([A-Za-z_][A-Za-z0-9_]*)', _repl, normalized_sql)
        return normalized_sql

    def _try_llm_metric_fallback_expression(
        self,
        *,
        metric: SMLMetric,
        metric_name: str,
        table_alias: str,
        alias_by_raw: Dict[str, str],
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_name_set: set[str],
        all_physical_col_names: set[str],
        emittable_metric_name_set: set[str],
        skipped_metric_names: set[str]
    ) -> Optional[str]:
        """Attempt LLM translation for a metric when deterministic handling fails."""
        if self.translator and hasattr(self.translator, "_try_llm_metric_fallback_expression"):
            return self.translator._try_llm_metric_fallback_expression(
                metric=metric,
                metric_name=metric_name,
                table_alias=table_alias,
                alias_by_raw=alias_by_raw,
                dataset_col_lookup=dataset_col_lookup,
                dataset_aliases=dataset_aliases,
                metric_name_set=metric_name_set,
                all_physical_col_names=all_physical_col_names,
                emittable_metric_name_set=emittable_metric_name_set,
                skipped_metric_names=skipped_metric_names,
            )

        dax_expression = (getattr(metric, "expression", None) or "").strip()
        if not dax_expression:
            return None

        candidate_expressions: list[str] = []
        try:
            from semabridge.converter.dax_rule_translator import is_simple_metric, rule_based_translation
            if is_simple_metric(dax_expression):
                local_expr = rule_based_translation(dax_expression, table_alias.lower())
                if local_expr:
                    candidate_expressions.append(local_expr)
        except Exception as ex:
            logger.debug(f"Local fallback unavailable for metric '{metric.unique_name}': {ex}")

        try:
            from semabridge.converter.gemini_dax_translator import get_gemini_translator
            translator = get_gemini_translator()
        except Exception as ex:
            logger.debug(f"LLM fallback unavailable for metric '{metric.unique_name}': {ex}")
            translator = None

        if translator and getattr(translator, "use_gemini", False) and getattr(translator, "api_key", None):
            schema_context = {ds_name: sorted(list(cols)) for ds_name, cols in dataset_col_lookup.items()}
            llm_result = translator.translate(
                dax=dax_expression,
                table_alias=table_alias.lower(),
                dataset_name=metric.dataset,
                metric_name=metric.unique_name,
                schema_context=schema_context,
            )
            if llm_result and llm_result.is_valid and llm_result.sql:
                candidate_expressions.append(llm_result.sql)

        for candidate_sql in candidate_expressions:
            expr = self.translator._sanitize_sql_markdown(candidate_sql)
            if not expr or "SELECT" in expr.upper():
                continue

            expr = self.identifier_sanitizer.resolve_dot_notation(
                expr,
                alias_by_raw,
                sanitize_col_fn=self.delegate._sanitize_col_name,
            )
            expr = self._normalize_metric_column_references(
                expr,
                metric.unique_name,
                dataset_col_lookup,
                dataset_aliases,
                metric_names=metric_name_set,
                preferred_table_alias=table_alias,
            )

            is_valid, _ = self._validate_metric_column_references(
                expr,
                metric.unique_name,
                dataset_col_lookup,
                dataset_aliases,
                metric_names=metric_name_set,
            )
            if not is_valid:
                continue

            unresolved_metric_refs = [
                r for r in re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr)
                if r in metric_name_set
                and r not in all_physical_col_names
                and (r not in emittable_metric_name_set or r in skipped_metric_names)
                and r != metric_name
            ]
            if unresolved_metric_refs:
                continue

            logger.info(f"Recovered metric '{metric.unique_name}' via fallback translation")
            return expr
        return None

    def _try_basic_dax_metric_fallback_expression(
        self,
        metric: SMLMetric,
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
        model: Optional[SMLModel] = None,
        dataset_by_name: Optional[Dict[str, SMLDataset]] = None
    ) -> Optional[str]:
        """Translate a small set of common DAX expressions without LLM."""
        raw_expr = (metric.expression or "").strip()
        if not raw_expr:
            return None

        import re
        expr = " ".join(raw_expr.split())
        known_cols = dataset_col_lookup.get(metric.dataset, set())

        if re.match(r"(?i)^COUNTROWS\(\s*'[^']+'\s*\)$", expr):
            return "COUNT(*)"

        m_blank = re.match(r"(?i)^COUNTBLANK\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$", expr)
        if m_blank:
            col = m_blank.group(1)
            actual_col = self.translator._resolve_column_name_for_dataset(known_cols, col)
            if actual_col:
                return f'COUNT_IF({table_alias}."{actual_col}" IS NULL)'

        m_dist = re.match(r"(?i)^DISTINCTCOUNT\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$", expr)
        if m_dist:
            col = m_dist.group(1)
            actual_col = self.translator._resolve_column_name_for_dataset(known_cols, col)
            if actual_col:
                return f'COUNT(DISTINCT {table_alias}."{actual_col}")'

        m_sum = re.match(r"(?i)^SUM\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$", expr)
        if m_sum:
            col = m_sum.group(1)
            actual_col = self.translator._resolve_column_name_for_dataset(known_cols, col)
            if actual_col:
                return self._build_safe_sum_sql(f'{table_alias}."{actual_col}"', actual_col)

        return None
