import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\snowflake_emitter.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

if 'from semabridge.extractor.dynamic_extractor import DynamicSchemaExtractor' not in content:
    content = content.replace('from semabridge.connectors.snowflake_emitter_parts import renderers as _renderers',
                              'from semabridge.connectors.snowflake_emitter_parts import renderers as _renderers\nfrom semabridge.extractor.dynamic_extractor import DynamicSchemaExtractor\nfrom typing import List')

# Replace _identify_fact_table
new_identify_fact_table = '''    def _identify_fact_table(self, model):
        """Dynamic fact table detection."""
        conn, owns_conn = self.connection_manager.get_connection()
        extractor = DynamicSchemaExtractor(conn)
        schema = extractor.extract_full_schema()
        
        # Find table with highest fact score
        fact_tables = [
            (table, score) 
            for table, score in schema['table_types'].items() 
            if score == 'fact'
        ]
        
        if fact_tables:
            return fact_tables[0][0]  # Return first fact table
        
        return None  # No fact table found'''

content = re.sub(
    r'    def _identify_fact_table\(self, model\):.*?    def _identify_fact_dataset\(self, model, fact_table: str\):',
    new_identify_fact_table + '\n\n    def _identify_fact_dataset(self, model, fact_table: str):',
    content,
    flags=re.DOTALL
)

# Replace _find_numeric_columns
new_find_numeric_columns = '''    def _find_numeric_columns(self, table_name: str) -> List[str]:
        """Dynamic numeric column detection."""
        conn, owns_conn = self.connection_manager.get_connection()
        extractor = DynamicSchemaExtractor(conn)
        return extractor.infer_numeric_columns(table_name)'''

content = re.sub(
    r'    def _find_numeric_columns\(self, fact_cols: set, fact_source_ref: str, fact_dataset\) -> list:.*?    def _validate_metric_column_references',
    new_find_numeric_columns + '\n\n    def _validate_metric_column_references',
    content,
    flags=re.DOTALL
)

# Fix the call in _build_dynamic_enriched_view_sql
content = re.sub(
    r'numeric_cols = self\._find_numeric_columns\(fact_cols, fact_source_ref, fact_dataset\)',
    r'numeric_cols = self._find_numeric_columns(safe_table)',
    content
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched snowflake_emitter.py for DynamicSchemaExtractor")
