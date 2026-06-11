import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\translator.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Add DAX keywords to forbidden list in _is_safe_llm_metric_sql
old_forbidden = '''            " OVER ",
        )'''
new_forbidden = '''            " OVER ",
            "CALCULATE(",
            "CALCULATE ",
            "FILTER(",
            "FILTER ",
            "ALL(",
            "ALL ",
            "ISBLANK(",
            "RELATED(",
            "RELATEDTABLE(",
            "SELECTEDVALUE(",
        )'''

if old_forbidden in content:
    content = content.replace(old_forbidden, new_forbidden)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched _is_safe_llm_metric_sql")
else:
    print("Could not find the target string to replace.")

