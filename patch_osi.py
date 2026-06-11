import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\converter\osi_to_sml.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the first translation call
content = re.sub(
    r'translation = self\.dax_translator\.translate\(\s*expression,\s*safe_alias,\s*osi_metric\.dataset,\s*metric_name=metric\.unique_name\s*\)',
    r'''from semabridge.translator.rule_based_dax_translator import HybridTranslator
            hybrid = HybridTranslator(llm_translator=self.dax_translator)
            sql, method = hybrid.translate(expression, metric.unique_name, safe_alias)
            class DummyTranslation:
                def __init__(self, s, m):
                    self.sql = s
                    self.is_success = True
                    self.tier = 5 if m == 'llm' else 1 if m == 'rule_based' else 6
            translation = DummyTranslation(sql, method)''',
    content
)

# Replace the second translation call
content = re.sub(
    r'translation = self\.dax_translator\.translate\(\s*metric\.expression,\s*safe_alias,\s*metric\.dataset,\s*metric_name=metric\.unique_name,\s*metrics_context=sml\.metrics,?\s*\)',
    r'''from semabridge.translator.rule_based_dax_translator import HybridTranslator
                hybrid = HybridTranslator(llm_translator=self.dax_translator)
                sql, method = hybrid.translate(metric.expression, metric.unique_name, safe_alias)
                class DummyTranslation:
                    def __init__(self, s, m):
                        self.sql = s
                        self.is_success = True
                        self.tier = 5 if m == 'llm' else 1 if m == 'rule_based' else 6
                translation = DummyTranslation(sql, method)''',
    content
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched osi_to_sml.py")
