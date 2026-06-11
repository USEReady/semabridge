import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\translator.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_loop = '''        for candidate_sql in candidate_expressions:
            expr = self._sanitize_sql_markdown(candidate_sql)
            if not expr or "SELECT" in expr.upper():
                continue

            if not self._is_scalar_metric_sql(expr):
                continue'''

new_loop = '''        for candidate_sql in candidate_expressions:
            expr = self._sanitize_sql_markdown(candidate_sql)
            if not expr or "SELECT" in expr.upper():
                continue

            if not self._is_scalar_metric_sql(expr):
                continue
                
            if not self._is_safe_llm_metric_sql(expr):
                continue'''

if old_loop in content:
    content = content.replace(old_loop, new_loop)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched _try_llm_metric_fallback_expression loop")
else:
    print("Could not find loop to replace.")
