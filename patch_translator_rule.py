import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\translator.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_code = '''                is_valid, issues = self._validate_metric_column_references(
                    normalized_rule_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_name_set
                )
                if is_valid and not issues:'''

new_code = '''                is_valid, issues = self._validate_metric_column_references(
                    normalized_rule_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_name_set
                )
                if is_valid and not issues and self._is_safe_llm_metric_sql(normalized_rule_sql):'''

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched rule-based validation check")
else:
    print("Could not find the rule-based validation check.")
