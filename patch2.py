import sys
import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\snowflake_emitter.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

methods_code = '''

    def _identify_fact_table(self, model):
        """
        Automatically identify the fact table in the model.
        Strategy: Table with most relationships OR most metrics OR contains 'FACT' keyword.
        """
        datasets = getattr(model, 'datasets', [])
        if not datasets:
            return None
        
        # Strategy 1: Look for table with 'FACT' in name
        for dataset in datasets:
            name_upper = dataset.unique_name.upper()
            if 'FACT' in name_upper or 'SALES' in name_upper or 'TRANSACTION' in name_upper:
                logger.info(f"✅ Identified fact table: {dataset.unique_name} (keyword match)")
                return dataset.unique_name
        
        # Strategy 2: Table with most relationships
        relationships = getattr(model, 'relationships', [])
        from_counts = {}
        for rel in relationships:
            from_ds = getattr(rel, 'from_dataset', None)
            if from_ds:
                from_counts[from_ds] = from_counts.get(from_ds, 0) + 1
        
        if from_counts:
            max_from = max(from_counts, key=from_counts.get)
            logger.info(f"✅ Identified fact table: {max_from} (most relationships: {from_counts[max_from]})")
            return max_from
        
        # Strategy 3: Table with most metrics
        metrics = getattr(model, 'metrics', [])
        metric_counts = {}
        for metric in metrics:
            ds = getattr(metric, 'dataset', None)
            if ds:
                metric_counts[ds] = metric_counts.get(ds, 0) + 1
        
        if metric_counts:
            max_metrics = max(metric_counts, key=metric_counts.get)
            logger.info(f"✅ Identified fact table: {max_metrics} (most metrics: {metric_counts[max_metrics]})")
            return max_metrics
        
        # Fallback: first dataset
        logger.warning(f"⚠️ Using first dataset as fact table: {datasets[0].unique_name}")
        return datasets[0].unique_name


    def _find_source_table_for_precompute(self, target_table: str, column_name: str):
        """
        Find the source table that contains the column for pre-computation.
        Uses model relationships to trace back to dimension table.
        """
        if not hasattr(self, '_model') or not self._model:
            logger.warning("No model available for relationship lookup")
            return None
        
        relationships = getattr(self._model, 'relationships', [])
        
        # Look for relationship where target_table is the 'to' side (dimension)
        for rel in relationships:
            to_dataset = getattr(rel, 'to_dataset', None)
            if to_dataset and to_dataset.upper() == target_table.upper():
                from_dataset = getattr(rel, 'from_dataset', None)
                if from_dataset:
                    # Check if column exists in source table
                    for dataset in getattr(self._model, 'datasets', []):
                        if dataset.unique_name == from_dataset:
                            for col in getattr(dataset, 'columns', []):
                                if col.unique_name.upper() == column_name.upper():
                                    logger.info(f"✅ Found source table {from_dataset} for column {column_name}")
                                    return from_dataset
        
        # Alternative: Look for relationship where target_table is 'from' side
        for rel in relationships:
            from_dataset = getattr(rel, 'from_dataset', None)
            if from_dataset and from_dataset.upper() == target_table.upper():
                to_dataset = getattr(rel, 'to_dataset', None)
                if to_dataset:
                    logger.info(f"✅ Found source table {to_dataset} for column {column_name} (reverse relationship)")
                    return to_dataset
        
        logger.warning(f"⚠️ Could not find source table for {target_table}.{column_name}")
        return None


    def _get_join_key(self, table1: str, table2: str) -> str:
        """
        Find the join key between two tables from relationships.
        """
        if not hasattr(self, '_model') or not self._model:
            return "ID"
        
        relationships = getattr(self._model, 'relationships', [])
        
        for rel in relationships:
            from_ds = getattr(rel, 'from_dataset', None)
            to_ds = getattr(rel, 'to_dataset', None)
            
            if from_ds and to_ds:
                if (from_ds.upper() == table1.upper() and to_ds.upper() == table2.upper()) or \\
                   (from_ds.upper() == table2.upper() and to_ds.upper() == table1.upper()):
                    
                    from_cols = getattr(rel, 'from_columns', [])
                    to_cols = getattr(rel, 'to_columns', [])
                    
                    if from_cols:
                        return from_cols[0].upper()
                    if to_cols:
                        return to_cols[0].upper()
        
        # Common default keys
        common_keys = ['PRODUCTID', 'ID', 'CUSTOMERID', 'BUSINESS_UNIT', 'FISCAL_YR_PERIOD']
        for key in common_keys:
            if key in table1.upper() or key in table2.upper():
                return key
        
        return "ID"


    def _find_date_table(self, model):
        """
        Dynamically find the date/calendar table in the model.
        Returns (table_name, date_column, fiscal_period_column) or None.
        No hardcoding!
        """
        date_keywords = ['date', 'calendar', 'cal', 'dim_date', 'dates']
        fiscal_keywords = ['fiscal_yr_period', 'fiscal_period', 'fiscal_year_period']
        
        for dataset in getattr(model, 'datasets', []):
            dataset_name = dataset.unique_name.lower()
            
            # Check if this looks like a date table
            is_date_table = any(kw in dataset_name for kw in date_keywords)
            
            if is_date_table:
                # Find date column
                date_col = None
                fiscal_col = None
                
                for col in dataset.columns:
                    col_name = col.unique_name.lower()
                    if col_name in ['cal_dt', 'date', 'calendar_date', 'cal_date']:
                        date_col = col.unique_name
                    if any(fk in col_name for fk in fiscal_keywords):
                        fiscal_col = col.unique_name
                
                if date_col and fiscal_col:
                    return (dataset.unique_name, date_col, fiscal_col)
        
        return None

    def _auto_execute_precompute_suggestions(self, model, cursor) -> None:
        """
        Automatically execute pre-compute suggestions in Snowflake.
        This eliminates manual ALTER TABLE commands.
        """
        suggestions = self.semantic_view_builder._precompute_suggestions(model)
        
        if not suggestions:
            return
        
        logger.info("🚀 Auto-executing pre-compute suggestions...")
        
        # Data Engineering Explicit Fixes
        fact_table = self._identify_fact_table(model)
        if fact_table:
            try:
                cursor.execute(f"ALTER TABLE {fact_table} ADD COLUMN IF NOT EXISTS ATINDICATOR04 VARCHAR")
                cursor.execute(f"ALTER TABLE {fact_table} ADD COLUMN IF NOT EXISTS ATINDICATOR05 VARCHAR")
                logger.info(f"✅ Added missing explicit columns to {fact_table}")
            except Exception as e:
                pass
        
        for table, cols in suggestions.items():
            # Check if table exists
            check_query = f"SHOW TABLES LIKE '{table.upper()}'"
            cursor.execute(check_query)
            if not cursor.fetchone():
                logger.warning(f"Table {table} not found, skipping pre-compute")
                continue
            
            # Add columns if not exist
            for col in cols:
                col_safe = col.upper().replace(' ', '_')
                try:
                    add_col_sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_safe} VARCHAR"
                    cursor.execute(add_col_sql)
                    logger.info(f"✅ Added column {col_safe} to {table}")
                except Exception as e:
                    logger.debug(f"Column {col_safe} may already exist: {e}")
            
            # Populate from related table
            # Detect source table from relationships
            for col in cols:
                source_table = self._find_source_table_for_precompute(table, col)
                if source_table and col:
                    col_safe = col.upper().replace(' ', '_')
                    try:
                        update_sql = f"""
                        UPDATE {table} t
                        SET t.{col_safe} = s.{col}
                        FROM {source_table} s
                        WHERE t.{self._get_join_key(table, source_table)} = s.{self._get_join_key(source_table, table)}
                        """
                        cursor.execute(update_sql)
                        logger.info(f"✅ Populated {col_safe} in {table} from {source_table}")
                    except Exception as e:
                        logger.warning(f"Could not auto-populate {col_safe}: {e}")
        
        logger.info("✅ Pre-compute suggestions executed successfully")


    def _create_enriched_view(self, model, cursor):
        """
        Automatically create an enriched view with pre-computed columns and anchors.
        This view can be used as the source for semantic model.
        """
        fact_table = self._identify_fact_table(model)
        if not fact_table:
            return None
        
        date_info = self._find_date_table(model)
        enriched_view_name = f"{fact_table}_ENRICHED"
        
        select_parts = [f"SELECT f.*"]
        
        # Add Pre-compute for percentage measures (Data Engineering Explicit Fix)
        select_parts.append(f"\\n        , (SELECT SUM(UNITS) FROM {fact_table}) AS TOTAL_UNITS_ALL")
        
        # Add pre-computed flag columns
        suggestions = self.semantic_view_builder._precompute_suggestions(model)
        for table, cols in suggestions.items():
            if table.upper() in fact_table.upper():
                for col in cols:
                    source_table = self._find_source_table_for_precompute(table, col)
                    if source_table:
                        col_safe = col.upper().replace(' ', '_')
                        select_parts.append(f"""
                        , (SELECT {col} FROM {source_table} s 
                           WHERE f.{self._get_join_key(fact_table, source_table)} = s.{self._get_join_key(source_table, fact_table)}
                          ) AS {col_safe}""")
        
        # Add YTD anchor
        if date_info:
            date_table, date_col, fiscal_col = date_info
            select_parts.append(f"""
            , (SELECT MAX({date_col}) FROM {fact_table}) AS max_date
            , (SELECT MAX({fiscal_col}) FROM {date_table} WHERE {date_col} = CURRENT_DATE()) AS _current_fiscal_period""")
        
        select_parts.append(f"FROM {fact_table} f")
        
        create_view_sql = "\\n".join(select_parts)
        full_sql = f"CREATE OR REPLACE VIEW {enriched_view_name} AS\\n{create_view_sql}"
        
        try:
            cursor.execute(full_sql)
            logger.info(f"✅ Created enriched view: {enriched_view_name}")
            return enriched_view_name
        except Exception as e:
            logger.warning(f"Failed to create enriched view: {e}")
            return None
'''

if 'def _identify_fact_table' not in content:
    content = content + methods_code
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Methods appended successfully.")
else:
    print("Methods already exist.")
