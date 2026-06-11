import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\snowflake_emitter.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update _identify_fact_table
new_identify_fact_table = '''    def _identify_fact_table(self, model):
        """
        Dynamically identify the fact table using DynamicSchemaExtractor.
        No hardcoded strings.
        """
        try:
            conn, owns_conn = self.connection_manager.get_connection()
            from semabridge.extractor.dynamic_extractor import DynamicSchemaExtractor
            extractor = DynamicSchemaExtractor(conn)
            schema = extractor.extract_full_schema()
            
            fact_tables = [(table, score) for table, score in schema.get('table_types', {}).items() if score == 'fact']
            if fact_tables:
                logger.info(f"💡 Identified fact table dynamically: {fact_tables[0][0]}")
                return fact_tables[0][0]
        except Exception as e:
            logger.warning(f"Dynamic schema extraction failed for fact table: {e}")
            
        # Fallback to model datasets relation counting
        datasets = getattr(model, "datasets", []) or []
        if not datasets:
            return None
            
        from_counts = {}
        for rel in getattr(model, "relationships", []) or []:
            from_ds = getattr(rel, "from_dataset", getattr(rel, "from_table", None))
            if from_ds:
                from_counts[from_ds] = from_counts.get(from_ds, 0) + 1
                
        if from_counts:
            max_from = max(from_counts, key=from_counts.get)
            logger.info(f"💡 Identified fact table: {max_from} (most relationships: {from_counts[max_from]})")
            return max_from
            
        logger.warning(f"⚠️ Using first dataset as fact table: {datasets[0].unique_name}")
        return datasets[0].unique_name'''

content = re.sub(
    r'    def _identify_fact_table\(self, model\):.*?        return datasets\[0\].unique_name',
    new_identify_fact_table,
    content,
    flags=re.DOTALL
)

# 2. Update _find_numeric_columns
new_find_numeric_columns = '''    def _find_numeric_columns(self, table_name: str) -> List[str]:
        """
        Dynamically identify numeric columns using DynamicSchemaExtractor.
        No hardcoded strings.
        """
        try:
            conn, owns_conn = self.connection_manager.get_connection()
            from semabridge.extractor.dynamic_extractor import DynamicSchemaExtractor
            extractor = DynamicSchemaExtractor(conn)
            return extractor.infer_numeric_columns(table_name)
        except Exception as e:
            logger.warning(f"Failed to infer numeric columns dynamically for {table_name}: {e}")
            return []'''

content = re.sub(
    r'    def _find_numeric_columns\(self, table_name: str\) -> List\[str\]:.*?        return list\(numeric_cols\)',
    new_find_numeric_columns,
    content,
    flags=re.DOTALL
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched snowflake_emitter.py for dynamic extraction")
