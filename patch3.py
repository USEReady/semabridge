import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\snowflake_emitter.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

create_enriched_code = '''
    def _create_enriched_view(self, model, cursor):
        \"\"\"Dynamically create enriched view with zero hardcoded assumptions.\"\"\"
        fact_table = self._identify_fact_table(model)
        if not fact_table:
            logger.warning("No fact table identified, skipping enriched view creation")
            return None
        
        # Get actual table metadata dynamically
        fact_dataset = self._get_dataset_by_name(model, fact_table)
        actual_table_name = getattr(fact_dataset, 'source_table', fact_table)
        safe_table = self._id.sanitize_table_name(actual_table_name)
        
        # Check if enriched view already exists (dynamic naming)
        possible_names = self._get_possible_enriched_view_names(safe_table)
        existing_view = self._find_existing_enriched_view(cursor, possible_names)
        
        if existing_view:
            logger.info(f"Found existing enriched view: {existing_view}")
            return existing_view
        
        # Verify base table exists before attempting view creation
        if not self._table_exists(cursor, safe_table):
            logger.warning(f"Base table {safe_table} doesn't exist, cannot create enriched view")
            return None
        
        # Build dynamic enriched view
        enriched_view_name = possible_names[0]  # Use preferred naming
        view_sql = self._build_dynamic_enriched_view_sql(
            model, fact_table, safe_table, enriched_view_name
        )
        
        if view_sql:
            try:
                cursor.execute(view_sql)
                logger.info(f"✅ Created enriched view: {enriched_view_name}")
                return enriched_view_name
            except Exception as e:
                logger.warning(f"Failed to create enriched view {enriched_view_name}: {e}")
                # Fallback to base table
                return safe_table
        
        return safe_table  # Fallback to base table

    def _get_possible_enriched_view_names(self, base_table: str) -> list:
        \"\"\"Generate possible enriched view names dynamically.\"\"\"
        return [
            f"{base_table}_ENRICHED",
            f"ENRICHED_{base_table}",
            f"VW_{base_table}",
            f"{base_table}_VW"
        ]

    def _find_existing_enriched_view(self, cursor, possible_names: list) -> Optional[str]:
        \"\"\"Check if any enriched view already exists.\"\"\"
        for view_name in possible_names:
            try:
                cursor.execute(f"SHOW VIEWS LIKE '{view_name}'")
                if cursor.fetchone():
                    return view_name
            except:
                continue
        return None

    def _table_exists(self, cursor, table_name: str) -> bool:
        \"\"\"Check if a table exists in the current schema.\"\"\"
        try:
            cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
            return bool(cursor.fetchone())
        except:
            return False

    def _build_dynamic_enriched_view_sql(self, model, fact_table, safe_table, enriched_view_name):
        fact_dataset = self._get_dataset_by_name(model, fact_table)
        fact_source_ref = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'
        fact_source_key = safe_table.upper()
        
        fact_cols = {
            str(c).upper()
            for c in self._live_schema_metadata.get(fact_source_key, set())
        }
        if not fact_cols and fact_dataset is not None:
            fact_cols = {
                self._resolve_model_column_name(model, fact_table, getattr(c, "unique_name", "")).upper()
                for c in getattr(fact_dataset, "columns", []) or []
                if getattr(c, "unique_name", None)
            }
            
        select_parts = [f"SELECT f.*"]
        if "UNITS" in fact_cols:
            select_parts.append(f'\\n        , (SELECT SUM("UNITS") FROM {fact_source_ref}) AS "TOTAL_UNITS_ALL"')
            
        # Add generic cross-dataset precomputed columns into the fact enriched view.
        if hasattr(self, 'semantic_view_builder'):
            suggestions = self.semantic_view_builder._precompute_suggestions(model)
            for detail in self.semantic_view_builder.get_precompute_details():
                if str(detail.get("target_dataset", "")).casefold() != str(fact_table).casefold():
                    continue
                projection = self._build_precomputed_column_select(
                    model=model,
                    target_dataset=detail["target_dataset"],
                    source_dataset=detail["source_dataset"],
                    source_column=detail["source_column"],
                    precomputed_column=detail["precomputed_column"],
                )
                if projection:
                    select_parts.append(projection)
                    
        select_clause = " ".join(select_parts)
        schema_ref = f'"{self.config.database}"."{self.config.schema_name}"'
        view_sql = f"CREATE OR REPLACE VIEW {schema_ref}.\"{enriched_view_name}\" AS \\n{select_clause} \\nFROM {fact_source_ref} f;"
        return view_sql
'''

content = re.sub(
    r'    def _create_enriched_view\(self, model, cursor\):.*?    def _validate_metric_column_references\(',
    create_enriched_code.strip('\n') + '\n\n    def _validate_metric_column_references(',
    content,
    flags=re.DOTALL
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched _create_enriched_view")
