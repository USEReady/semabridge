import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\snowflake_emitter.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1 & 2: _get_join_key and _find_date_table replacements
part1_code = '''    def _get_join_key(self, table1: str, table2: str) -> str:
        """
        Find the join key between two tables dynamically from relationships.
        No hardcoded defaults.
        """
        if not hasattr(self, '_model') or not self._model:
            return None  # Return None instead of "ID" hardcode
        
        relationships = getattr(self._model, 'relationships', [])
        
        for rel in relationships:
            from_ds = getattr(rel, 'from_dataset', None)
            to_ds = getattr(rel, 'to_dataset', None)
            
            if from_ds and to_ds:
                if (from_ds.upper() == table1.upper() and to_ds.upper() == table2.upper()) or \
                   (from_ds.upper() == table2.upper() and to_ds.upper() == table1.upper()):
                    
                    from_cols = getattr(rel, 'from_columns', [])
                    to_cols = getattr(rel, 'to_columns', [])
                    
                    if from_cols:
                        return from_cols[0].upper()
                    if to_cols:
                        return to_cols[0].upper()
        
        # Try to infer from column name patterns
        inferred = self._infer_join_key_from_names(table1, table2)
        if inferred:
            return inferred
        
        logger.warning(f"No join key found between {table1} and {table2}")
        return None

    def _infer_join_key_from_names(self, table1: str, table2: str) -> Optional[str]:
        """Infer join key from table naming patterns."""
        clean1 = table1.upper().replace('DIM_', '').replace('FACT_', '').rstrip('S')
        clean2 = table2.upper().replace('DIM_', '').replace('FACT_', '').rstrip('S')
        
        candidates = [
            f"{clean1}ID",
            f"{clean2}ID",
            "ID",
            f"{clean1}_ID",
            f"{clean2}_ID",
        ]
        
        for candidate in candidates:
            if self._column_exists_in_table(table1, candidate) or self._column_exists_in_table(table2, candidate):
                return candidate
        
        return None

    def _column_exists_in_table(self, table_name: str, column_name: str) -> bool:
        """Check if a column exists in a table using live schema metadata."""
        table_upper = table_name.upper()
        if table_upper in self._live_schema_metadata:
            return column_name.upper() in self._live_schema_metadata[table_upper]
        return False

    def _find_date_table(self, model):
        """
        Dynamically find the date/calendar table in the model.
        Uses metadata patterns, not hardcoded names.
        """
        for dataset in getattr(model, 'datasets', []):
            dataset_name = dataset.unique_name.lower()
            
            date_indicators = ['date', 'calendar', 'time', 'period']
            is_date_table = any(indicator in dataset_name for indicator in date_indicators)
            
            if is_date_table or self._has_date_columns(dataset):
                date_col = self._find_date_column(dataset)
                fiscal_col = self._find_fiscal_column(dataset)
                
                if date_col:
                    return (dataset.unique_name, date_col, fiscal_col)
        
        return None

    def _has_date_columns(self, dataset) -> bool:
        """Check if dataset has any date-type columns."""
        for col in getattr(dataset, 'columns', []):
            data_type = getattr(col, 'data_type', None)
            if data_type and str(data_type).upper() in ['DATE', 'DATETIME', 'TIMESTAMP']:
                return True
            col_name = col.unique_name.lower()
            if any(pattern in col_name for pattern in ['date', 'cal', 'dt']):
                return True
        return False

    def _find_date_column(self, dataset):
        """Find the primary date column in a dataset."""
        for col in getattr(dataset, 'columns', []):
            col_name = col.unique_name.lower()
            data_type = getattr(col, 'data_type', None)
            if data_type and str(data_type).upper() in ['DATE', 'DATETIME', 'TIMESTAMP']:
                return col.unique_name
            if col_name in ['date', 'cal_date', 'calendar_date', 'transaction_date']:
                return col.unique_name
        return None

    def _find_fiscal_column(self, dataset):
        """Find fiscal period column if exists."""
        for col in getattr(dataset, 'columns', []):
            col_name = col.unique_name.lower()
            if any(pattern in col_name for pattern in ['fiscal', 'period', 'quarter', 'year']):
                if 'date' not in col_name:
                    return col.unique_name
        return None
'''
content = re.sub(
    r'    def _get_join_key\(self, table1: str, table2: str\) -> str:.*?    def _auto_execute_precompute_suggestions\(self, model, cursor\) -> None:',
    part1_code.rstrip() + '\n\n    def _auto_execute_precompute_suggestions(self, model, cursor) -> None:',
    content,
    flags=re.DOTALL
)

# 3: _find_join_key_dynamic deletion
content = re.sub(
    r'    def _find_join_key_dynamic\(self, cursor, target_table: str, source_table: str\) -> Optional\[str\]:.*?    def _resolve_physical_col_name',
    '    def _resolve_physical_col_name',
    content,
    flags=re.DOTALL
)

# 4: _build_dynamic_enriched_view_sql replacement
part4_code = '''    def _build_dynamic_enriched_view_sql(self, model, fact_table, safe_table, enriched_view_name):
        """Build enriched view SQL dynamically - no hardcoded columns."""
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
        
        select_parts = ["SELECT f.*"]
        
        # DYNAMIC: Find numeric columns that make sense for aggregation
        numeric_cols = self._find_numeric_columns(fact_cols, fact_source_ref, fact_dataset)
        for col in numeric_cols[:3]:  # Limit to top 3 to avoid huge views
            agg_name = f"TOTAL_{col}_ALL"
            select_parts.append(
                f'        , (SELECT SUM("{col}") FROM {fact_source_ref}) AS "{agg_name}"'
            )
        
        # Add precomputed columns from suggestions
        if hasattr(self, 'semantic_view_builder'):
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
        
        select_clause = "\\n".join(select_parts)
        schema_ref = f'"{self.config.database}"."{self.config.schema_name}"'
        view_sql = f\'CREATE OR REPLACE VIEW {schema_ref}."{enriched_view_name}" AS\\n{select_clause}\\nFROM {fact_source_ref} f;\'
        return view_sql

    def _find_numeric_columns(self, fact_cols: set, fact_source_ref: str, fact_dataset) -> list:
        """Dynamically find columns suitable for aggregation."""
        numeric_hints = ['UNITS', 'AMOUNT', 'QUANTITY', 'SALES', 'REVENUE', 'PRICE', 'COST']
        numeric_cols = []
        
        for col in fact_cols:
            col_upper = col.upper()
            # Check column name patterns
            if any(hint in col_upper for hint in numeric_hints):
                numeric_cols.append(col)
            # Also check data type if available
            elif fact_dataset:
                for ds_col in getattr(fact_dataset, 'columns', []):
                    if ds_col.unique_name.upper() == col_upper:
                        data_type = getattr(ds_col, 'data_type', None)
                        if data_type and str(data_type).upper() in ['INTEGER', 'DECIMAL', 'FLOAT', 'NUMBER']:
                            numeric_cols.append(col)
                            break
        
        return numeric_cols
'''
content = re.sub(
    r'    def _build_dynamic_enriched_view_sql\(self, model, fact_table, safe_table, enriched_view_name\):.*?    def _validate_metric_column_references\(',
    part4_code.rstrip() + '\n\n    def _validate_metric_column_references(',
    content,
    flags=re.DOTALL
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched snowflake_emitter.py successfully.")
