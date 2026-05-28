import os

def patch_file(path):
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return
        
    print(f"Patching sanitizer at: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # 1. Add back display name reference normalization in sanitize_structure
    target1 = """        # METRICS: ensure at least one valid line.
        met_block = _find_block("METRICS (")
        if met_block:
            m_start, m_end = met_block
            met_items = _get_items(m_start, m_end)
            if not met_items:
                met_items = [
                    f'  {fallback_alias}."PLACEHOLDER_METRIC" AS NULL'
                ]
            _set_items(m_start, m_end, met_items)"""
            
    replacement1 = """        # METRICS: ensure at least one valid line.
        met_block = _find_block("METRICS (")
        if met_block:
            m_start, m_end = met_block
            met_items = _get_items(m_start, m_end)
            if not met_items:
                met_items = [
                    f'  {fallback_alias}."PLACEHOLDER_METRIC" AS NULL'
                ]
            else:
                met_items = self._normalize_metric_display_name_refs(met_items)
            _set_items(m_start, m_end, met_items)"""

    # 2. Always strip inner synonyms from expanded_expr in _inline_metric_references
    target2 = """                if expanded_expr != expr:
                    changed = True
                next_lines.append(f"{prefix}{expanded_expr}{suffix}")"""
                
    replacement2 = """                clean_expanded_expr = cls._strip_inner_synonyms(expanded_expr)
                if clean_expanded_expr != expr:
                    changed = True
                next_lines.append(f"{prefix}{clean_expanded_expr}{suffix}")"""

    if target1 in content:
        content = content.replace(target1, replacement1)
        print("  Successfully matched and replaced sanitize_structure METRICS block.")
    else:
        print("  Warning: Target 1 (METRICS block) not matched.")
        
    if target2 in content:
        content = content.replace(target2, replacement2)
        print("  Successfully matched and replaced _inline_metric_references loop end.")
    else:
        print("  Warning: Target 2 (inliner loop end) not matched.")

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Successfully wrote changes to {path}\n")

if __name__ == "__main__":
    patch_file(r"c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\semantic_ddl_sanitizer.py")
    patch_file(r"c:\Users\MANOJ\semabridge-working\src\semabridge\connectors\semantic_ddl_sanitizer.py")
