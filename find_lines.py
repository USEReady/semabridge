with open(r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\translator.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "def fix_common_llm_issues" in line:
        print(f"fix_common_llm_issues: {i+1}")
    elif "normalized_rule_sql = self.fix_common_llm_issues(rule_based_sql, dax_expression)" in line:
        print(f"call 1: {i+1}")
    elif "expr = self.fix_common_llm_issues(expr, dax_expression)" in line:
        print(f"call 2: {i+1}")
    elif "return self.fix_common_llm_issues(expr, dax_expression)" in line:
        print(f"call 3: {i+1}")
    elif "def _try_basic_dax_metric_fallback_expression" in line:
        print(f"_try_basic_dax_metric_fallback_expression: {i+1}")
