import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\snowflake_emitter.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

auto_execute_code = '''
    def _auto_execute_precompute_suggestions(self, model, cursor) -> None:
        \"\"\"Auto-execute pre-compute with dynamic type inference.\"\"\"
        suggestions = self.semantic_view_builder._precompute_suggestions(model)
        
        if not suggestions:
            return
        
        logger.info("🚀 Auto-executing pre-compute suggestions...")
        
        # Get live schema metadata
        live_meta = self._get_live_schema_metadata(cursor)
        
        for target_table, columns in suggestions.items():
            table_upper = target_table.upper()
            existing_cols = live_meta.get(table_upper, set())
            
            # Filter columns that actually need creation
            cols_to_create = []
            for col in columns:
                col_upper = col.upper().replace(' ', '_')
                if col_upper not in existing_cols:
                    cols_to_create.append(col)
            
            if not cols_to_create:
                logger.info(f"All columns already exist in {target_table}")
                continue
            
            # Verify target table exists
            if not self._table_exists(cursor, target_table):
                logger.warning(f"Table {target_table} not found, skipping pre-compute")
                continue
            
            for col in cols_to_create:
                # Dynamically infer column type from source
                source_table = self._find_source_table_for_precompute(target_table, col)
                col_type = self._infer_column_type_dynamic(cursor, source_table, col) if source_table else "VARCHAR"
                
                # Add column with correct type
                col_safe = col.upper().replace(' ', '_')
                alter_sql = f'ALTER TABLE "{table_upper}" ADD COLUMN IF NOT EXISTS "{col_safe}" {col_type}'
                
                try:
                    cursor.execute(alter_sql)
                    logger.info(f"Added column {col_safe} ({col_type}) to {target_table}")
                    
                    # Populate data if source table exists
                    if source_table:
                        self._populate_precomputed_column(cursor, target_table, source_table, col, col_safe)
                        
                except Exception as e:
                    logger.warning(f"Could not add/populate {col_safe}: {e}")

    def _infer_column_type_dynamic(self, cursor, table_name: str, column_name: str) -> str:
        \"\"\"Dynamically infer column type from source table.\"\"\"
        if not table_name:
            return "VARCHAR"
        
        try:
            # Query INFORMATION_SCHEMA for actual data type
            query = f\"\"\"
            SELECT DATA_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = '{table_name.upper()}' 
            AND COLUMN_NAME = '{column_name.upper()}'
            \"\"\"
            cursor.execute(query)
            result = cursor.fetchone()
            if result:
                data_type = result[0]
                # Map to appropriate Snowflake type
                type_map = {
                    'INT': 'NUMBER',
                    'DECIMAL': 'NUMBER(38,9)',
                    'FLOAT': 'FLOAT',
                    'VARCHAR': 'VARCHAR(16777216)',
                    'DATE': 'DATE',
                    'TIMESTAMP': 'TIMESTAMP'
                }
                return type_map.get(data_type.upper(), 'VARCHAR')
        except Exception as e:
            logger.debug(f"Could not infer type for {table_name}.{column_name}: {e}")
        
        return "VARCHAR"

    def _populate_precomputed_column(self, cursor, target_table: str, source_table: str, 
                                      col_name: str, col_safe: str):
        \"\"\"Populate precomputed column using dynamic join resolution.\"\"\"
        # Find join relationship dynamically
        join_key = self._find_join_key_dynamic(cursor, target_table, source_table)
        
        if not join_key:
            logger.warning(f"No join key found between {target_table} and {source_table}")
            return
        
        # Build dynamic UPDATE statement
        update_sql = f\"\"\"
        UPDATE "{target_table.upper()}" t
        SET t."{col_safe}" = (
            SELECT s."{col_name.upper()}"
            FROM "{source_table.upper()}" s
            WHERE s."{join_key}" = t."{join_key}"
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM "{source_table.upper()}" s
            WHERE s."{join_key}" = t."{join_key}"
        )
        \"\"\"
        
        try:
            cursor.execute(update_sql)
            logger.info(f"Populated {col_safe} in {target_table} from {source_table}")
        except Exception as e:
            logger.warning(f"Could not populate {col_safe}: {e}")

    def _find_join_key_dynamic(self, cursor, target_table: str, source_table: str) -> Optional[str]:
        \"\"\"Helper to find join key dynamically.\"\"\"
        return self._get_join_key(target_table, source_table)
'''

content = re.sub(
    r'    def _auto_execute_precompute_suggestions\(self, model, cursor\) -> None:.*?    def _resolve_physical_col_name',
    auto_execute_code.strip('\n') + '\n\n    def _resolve_physical_col_name',
    content,
    flags=re.DOTALL
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched _auto_execute_precompute_suggestions")
