import ast
import re

from pathlib import Path

base_path = Path(__file__).parent.parent.parent
emitter_file = base_path / 'semabridge' / 'connectors' / 'snowflake_emitter.py'
builder_file = base_path / 'semabridge' / 'connectors' / 'ddl_builder.py'

with open(emitter_file, 'r', encoding='utf-8') as f:
    emitter_lines = f.readlines()

functions_to_move = [
    '_generate_semantic_view',
    '_generate_semantic_view_from_osi',
    '_migrate_numeric_leading_identifiers',
    '_prune_unresolved_metric_lines',
    '_select_semantic_view_ddl',
    '_guard_relationship_clause',
    '_sanitize_semantic_ddl_structure',
    '_remediate_semantic_ddl_invalid_identifier'
]

translator_helpers = [
    '_try_llm_metric_fallback_expression',
    '_try_basic_dax_metric_fallback_expression',
    '_validate_metric_column_references',
    '_normalize_metric_column_references',
    '_build_safe_sum_sql',
    '_extract_column_names_from_metric_expression',
]

with open(emitter_file, 'r', encoding='utf-8') as f:
    source = f.read()
tree = ast.parse(source)

funcs_info = {}
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'SnowflakeEmitter':
        for child in node.body:
            if isinstance(child, ast.FunctionDef) and child.name in functions_to_move:
                start = child.decorator_list[0].lineno if child.decorator_list else child.lineno
                end = child.end_lineno
                funcs_info[child.name] = (start, end)

extracted_code = {}
to_delete = sorted(funcs_info.values(), key=lambda x: x[0], reverse=True)

for name, (start, end) in funcs_info.items():
    code_lines = emitter_lines[start-1:end]
    code_str = "".join(code_lines)
    extracted_code[name] = code_str

for start, end in to_delete:
    del emitter_lines[start-1:end]

with open(emitter_file, 'w', encoding='utf-8') as f:
    f.writelines(emitter_lines)

for name in functions_to_move:
    if name not in extracted_code:
        continue
    code = extracted_code[name]
    
    for helper in translator_helpers:
        code = re.sub(r'\bself\.' + helper + r'\b', f'self.translator.{helper}', code)
    
    for helper in ['_resolve_primary_date_column', '_resolve_year_partition_column', '_resolve_month_dimension_column', '_resolve_year_dimension_column', '_resolve_sply_offset_key_column', '_resolve_ytd_order_column']:
        code = re.sub(r'\bSnowflakeEmitter\.' + helper + r'\b', f'self.translator.{helper}', code)
        code = re.sub(r'\bself\.' + helper + r'\b', f'self.translator.{helper}', code)
        
    def repl_self(m):
        attr = m.group(1)
        owned_attrs = functions_to_move + ['config', 'behavior', 'identifier_sanitizer', 'live_schema_metadata', '_delegate', 'translator']
        if attr in owned_attrs:
            return f'self.{attr}'
        return f'self._delegate.{attr}'
    
    code = re.sub(r'\bself\.([a-zA-Z_]\w*)', repl_self, code)
    code = re.sub(r'\bSnowflakeEmitter\._try_generate_flattened_view_cte\b', 'self._delegate._try_generate_flattened_view_cte', code)
    code = re.sub(r'\bSnowflakeEmitter\._is_simple_dax_aggregation_expression\b', 'self._delegate._is_simple_dax_aggregation_expression', code)
    code = re.sub(r'\bSnowflakeEmitter\._warn_non_sync_friendly_dax\b', 'self._delegate._warn_non_sync_friendly_dax', code)

    extracted_code[name] = code

with open(builder_file, 'r', encoding='utf-8') as f:
    builder_source = f.read()

tree_builder = ast.parse(builder_source)
to_delete_builder = []
for node in ast.walk(tree_builder):
    if isinstance(node, ast.ClassDef) and node.name == 'SemanticViewBuilder':
        for child in node.body:
            if isinstance(child, ast.FunctionDef) and child.name in functions_to_move:
                start = child.decorator_list[0].lineno if child.decorator_list else child.lineno
                to_delete_builder.append((start, child.end_lineno))

builder_lines = builder_source.split('\n')
for start, end in sorted(to_delete_builder, key=lambda x: x[0], reverse=True):
    del builder_lines[start-1:end]

new_builder_source = '\n'.join(builder_lines)
new_builder_source = new_builder_source.replace('self._delegate._generate_semantic_view(sml)', 'self._generate_semantic_view(sml)')
new_builder_source = new_builder_source.replace('self._delegate._generate_semantic_view_from_osi(osi)', 'self._generate_semantic_view_from_osi(osi)')

for name in functions_to_move:
    if name in extracted_code:
        new_builder_source += '\n' + extracted_code[name]

with open(builder_file, 'w', encoding='utf-8') as f:
    f.write(new_builder_source)

print("Done Refactoring!")
