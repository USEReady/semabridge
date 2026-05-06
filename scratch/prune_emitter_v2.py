import re
import sys

def prune_file(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()

    methods_to_remove = {
        '_fetch_schema_metadata',
        '_fetch_model_table_metadata',
        '_ensure_source_tables_exist',
        '_verify_table_columns',
        '_drop_extra_columns',
        '_generate_create_or_replace_table_ddl',
        '_generate_date_dim_ddl',
        '_generate_create_table_ddl',
        '_collect_physical_source_columns',
        '_resolve_physical_column_name',
        'generate_ctas_sql',
        '_dataset_columns_for_ctas_sml',
        '_infer_type_from_values',
        '_fallback_type_from_name',
        '_build_cast_expression',
        '_infer_columns_from_table_samples',
        '_business_rule_type',
        '_normalize_declared_type',
        '_to_sml_datatype',
        '_to_osi_datatype',
        '_dataset_columns_for_ctas_osi',
        '_apply_inferred_types_ctas_sml',
        '_apply_inferred_types_ctas_osi',
        '_collect_physical_source_columns_osi',
        '_generate_sample_insert',
        'sync_measure_data',
        'generate_semantic_view_tiered',
        'generate_ddls',
        'generate_ddls_from_osi',
        '_preflight_check_osi',
        '_generate_semantic_view', # Missed this one maybe?
        '_generate_semantic_view_from_osi',
    }

    new_lines = []
    skipping = False
    
    for i, line in enumerate(lines):
        # Determine indentation
        indent = len(line) - len(line.lstrip())
        
        # We only care about top-level class methods (usually indent 4)
        match = re.match(r'^    def\s+(\w+)\(', line)
        if match:
            method_name = match.group(1)
            # Check if this is a proxy (already refactored)
            is_proxy = 'return self.' in line or (i+1 < len(lines) and 'return self.' in lines[i+1])
            
            # Special case: some proxies are multiline but very short.
            # Real implementations are long.
            # But safer: check line number or if it's in the proxy block.
            
            if method_name in methods_to_remove:
                # Keep proxies (they are usually before line 2400)
                if i < 2400:
                    skipping = False
                else:
                    skipping = True
                    print(f"Pruning: {method_name} at line {i+1}")
            else:
                skipping = False
        elif skipping and indent <= 4 and line.strip():
            # If we were skipping and we hit a line with 4 or less spaces that isn't empty,
            # it might be the next method or something else.
            # But wait, 'elif' might be indented.
            # A better way: if skipping, keep skipping until we hit '    def ' or 'class ' or 'if __name__' etc.
            if re.match(r'^    def\s+', line) or re.match(r'^class\s+', line) or re.match(r'^if\s+', line):
                skipping = False
                # Re-evaluate this line
                match = re.match(r'^    def\s+(\w+)\(', line)
                if match:
                    method_name = match.group(1)
                    if method_name in methods_to_remove and i >= 2400:
                         skipping = True
                         print(f"Pruning: {method_name} at line {i+1}")
            else:
                # Still skipping
                pass

        if not skipping:
            new_lines.append(line)

    with open(filepath, 'w') as f:
        f.writelines(new_lines)

if __name__ == '__main__':
    prune_file(sys.argv[1])
