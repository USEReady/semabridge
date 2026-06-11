import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\translator.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_forbidden = '''            " OVER ",
            " DROP ",
            " DELETE ",
            " TRUNCATE ",
            " INSERT ",
            " UPDATE ",
            " ALTER ",
            ";",
        )'''

new_forbidden = '''            " OVER ",
            " DROP ",
            " DELETE ",
            " TRUNCATE ",
            " INSERT ",
            " UPDATE ",
            " ALTER ",
            ";",
            "CALCULATE(",
            " CALCULATE ",
            "FILTER(",
            " FILTER ",
            "ALL(",
            " ALL ",
            "ISBLANK(",
            " ISBLANK ",
            "RELATED(",
            " RELATED ",
            "RELATEDTABLE(",
            " RELATEDTABLE ",
        )'''

if old_forbidden in content:
    content = content.replace(old_forbidden, new_forbidden)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched _is_safe_llm_metric_sql")
else:
    print("Could not find the target string to replace.")
