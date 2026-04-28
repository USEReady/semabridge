"""Measure sync and materialization utilities for Snowflake."""

from typing import Optional, Dict, Any

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def sync_measure_data(
    emitter,
    measure_name: str,
    data: list[dict],
    target_table: str = None,
    dimension_columns: list[str] = None,
    write_mode: str = "overwrite",
) -> bool:
    """Write materialized measure data to Snowflake.
    
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
    safe_table = f"MEASURES_{emitter._safe_table_name(table_base)}"
    full_table = f'{emitter.config.database}.{emitter.config.schema_name}."{safe_table}"'
    
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
        conn = snowflake.connector.connect(
            user=emitter.config.user,
            password=emitter.config.password.get_secret_value(),
            account=emitter.config.account,
            warehouse=emitter.config.warehouse,
            database=emitter.config.database,
            schema=emitter.config.schema_name,
            role=emitter.config.role,
            session_parameters={
                "QUERY_TAG": emitter.sf_behavior.query_tag or "Semabridge_MeasureSync"
            }
        )
        cur = conn.cursor()
        
        try:
            # Create or replace table based on write mode
            if write_mode == "overwrite":
                col_defs = ", ".join([f'"{emitter._sanitize_col_name(c[0])}" {c[1]}' for c in type_map])
                create_ddl = f"CREATE OR REPLACE TABLE {full_table} ({col_defs})"
                logger.debug(f"Creating table: {create_ddl}")
                emitter._execute_sql(cur, create_ddl, context=f"CREATE TABLE {full_table}")
            elif write_mode == "append":
                # Check if table exists, create if not
                try:
                    emitter._execute_sql(cur, f"DESC TABLE {full_table}", context=f"DESC TABLE {full_table}")
                except:
                    col_defs = ", ".join([f'"{emitter._sanitize_col_name(c[0])}" {c[1]}' for c in type_map])
                    emitter._execute_sql(cur, f"CREATE TABLE IF NOT EXISTS {full_table} ({col_defs})", context=f"CREATE TABLE IF NOT EXISTS {full_table}")
            
            # Insert data in batches for performance
            batch_size = 10000
            total_inserted = 0
            
            for i in range(0, len(data), batch_size):
                batch = data[i:i + batch_size]
                
                # Build column list
                col_list = ", ".join([f'"{emitter._sanitize_col_name(c)}"' for c in columns])
                
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
                            import json
                            vals.append(f"PARSE_JSON('{json.dumps(val)}')")
                        else:
                            # Escape single quotes in strings
                            escaped = str(val).replace("'", "''")
                            vals.append(f"'{escaped}'")
                    value_rows.append(f"({', '.join(vals)})")
                
                # Execute batch insert
                insert_sql = f"INSERT INTO {full_table} ({col_list}) VALUES {', '.join(value_rows)}"
                emitter._execute_sql(cur, insert_sql, context=f"INSERT INTO {full_table}")
                total_inserted += len(batch)
                
                if len(data) > batch_size:
                    logger.debug(f"Inserted batch {i//batch_size + 1}: {len(batch)} rows")
            
            logger.info(f"Successfully synced {total_inserted} rows to {full_table}")
            
            # Add metadata about sync time
            try:
                import datetime
                sync_time = datetime.datetime.utcnow().isoformat()
                emitter._execute_sql(
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


def generate_semantic_view_tiered(
    emitter,
    model_name: str,
    shadow_table: str,
    triage_results: "dict[str, TriageResult]",
    grain_dimensions: list[str],
) -> str:
    """Generate a Snowflake VIEW that reconstructs measures per tier.

    The view never exposes the raw shadow table to users. It provides
    business-friendly column aliases and reconstructs Tier 3 ratios.

    Args:
        model_name: Semantic model display name for view naming.
        shadow_table: Fully qualified shadow table reference.
        triage_results: Dict of metric_name → TriageResult.
        grain_dimensions: Dimension column names used in GROUP BY.

    Returns:
        A CREATE OR REPLACE VIEW DDL string.
    """
    from semabridge.connectors.snowflake_emitter_parts import identifier_utilities as id_utils
    
    view_name = f"V_{id_utils.safe_table_name_static(model_name)}"
    full_view = (
        f"{emitter.config.database}.{emitter.config.schema_name}."
        f'"{view_name}"'
    )

    select_parts: list[str] = []
    group_by_parts: list[str] = []

    # Dimensions
    for dim in grain_dimensions:
        safe_dim = emitter._sanitize_col_name(dim)
        select_parts.append(f'    base."{safe_dim}"')
        group_by_parts.append(f'base."{safe_dim}"')

    # Measures
    for metric_name, triage in triage_results.items():
        safe = emitter._sanitize_col_name(metric_name)
        safe_expr = f'base."{safe}"'

        if triage.strategy.value == "passthrough":
            # Tier 1: simple pass-through aggregation
            select_parts.append(
                f'    {emitter._build_safe_sum_sql(safe_expr, safe)} AS "{safe}"'
            )

        elif triage.strategy.value == "aligned_history":
            # Tier 2: base value + companion columns
            select_parts.append(
                f'    {emitter._build_safe_sum_sql(safe_expr, safe)} AS "{safe}"'
            )
            for suffix in triage.aligned_measures:
                alias = emitter._sanitize_col_name(f"{metric_name}{suffix}")
                alias_expr = f'base."{alias}"'
                select_parts.append(
                    f'    {emitter._build_safe_sum_sql(alias_expr, alias)} AS "{alias}"'
                )

        elif triage.strategy.value == "decomposition":
            if triage.components:
                # Tier 3: reconstruct ratio from components
                num_col = emitter._sanitize_col_name(f"{metric_name}_Num")
                den_col = emitter._sanitize_col_name(f"{metric_name}_Denom")
                num_expr = f'base."{num_col}"'
                den_expr = f'base."{den_col}"'
                select_parts.append(
                    f'    {emitter._build_safe_sum_sql(num_expr, num_col)} / '
                    f'NULLIF({emitter._build_safe_sum_sql(den_expr, den_col)}, 0) AS "{safe}"'
                )
            else:
                # Tier 3 without decomposition — pass-through
                select_parts.append(
                    f'    {emitter._build_safe_sum_sql(safe_expr, safe)} AS "{safe}"'
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
