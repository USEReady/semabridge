import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\tables_clause_builder.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the body of _build_source_query_with_anchors to simply return source_fq
new_body = '''    def _build_source_query_with_anchors(self, source_fq: str, fact_table: str, model: Any) -> str:
        """
        Snowflake Cortex Semantic Views do NOT support inline CTEs/subqueries in the TABLES clause.
        We must return the source fully qualified name directly.
        """
        return source_fq
'''

content = re.sub(
    r'    def _build_source_query_with_anchors\(self, source_fq: str, fact_table: str, model: Any\) -> str:.*?\)"""',
    new_body,
    content,
    flags=re.DOTALL
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched tables_clause_builder.py")
