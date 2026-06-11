import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\config\behavior.yaml'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace translation block
content = re.sub(
    r'  deterministic_translation_enabled: true\n  enable_llm_dax_translation: true\n  llm_provider: "openai"',
    r'''  translation:
    use_llm: false
    use_rule_based: true
    fallback_to_null: true
    skip_on_error: false''',
    content
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched behavior.yaml")
