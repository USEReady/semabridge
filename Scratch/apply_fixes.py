import os
import shutil
import re

def apply_fixes():
    working_sanitizer = r"c:\Users\MANOJ\semabridge-working\src\semabridge\connectors\semantic_ddl_sanitizer.py"
    dev_test_sanitizer = r"c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\semantic_ddl_sanitizer.py"
    
    # 1. Copy advanced sanitizer to dev-test
    if os.path.exists(working_sanitizer):
        print(f"Copying {working_sanitizer} to {dev_test_sanitizer}...")
        shutil.copy(working_sanitizer, dev_test_sanitizer)
    else:
        print("Working sanitizer file not found!")
        
    # 2. Patch metrics_clause_builder.py in both folders
    metrics_paths = [
        r"c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\metrics_clause_builder.py",
        r"c:\Users\MANOJ\semabridge-working\src\semabridge\connectors\metrics_clause_builder.py"
    ]
    
    for path in metrics_paths:
        if os.path.exists(path):
            print(f"Patching {path}...")
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Find the inlining block and replace ref_expr with clean_ref_expr
            target_str = """                        ref_expr = expr_by_name.get(ref_name)
                        if ref_expr:
                            expanded_expr = re.sub(rf'"{re.escape(ref_name)}"', f'({ref_expr})', expanded_expr)"""
                            
            replacement_str = """                        ref_expr = expr_by_name.get(ref_name)
                        if ref_expr:
                            # Strip nested/inner WITH SYNONYMS clauses to avoid Snowflake DDL syntax errors
                            clean_ref_expr = re.sub(
                                r"\\s+WITH\\s+SYNONYMS\\s*=\\s*\\((?:[^()']|'(?:''|[^'])*')*\\)",
                                "",
                                ref_expr,
                                flags=re.IGNORECASE
                            )
                            expanded_expr = re.sub(rf'"{re.escape(ref_name)}"', f'({clean_ref_expr})', expanded_expr)"""
            
            if target_str in content:
                content = content.replace(target_str, replacement_str)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"Successfully patched {path}")
            else:
                # Let's search with regex or other formatting
                print(f"Target string not found in {path}")
        else:
            print(f"Metrics builder file not found at: {path}")

if __name__ == "__main__":
    apply_fixes()
