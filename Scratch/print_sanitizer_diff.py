import difflib
import os

f1_path = r"c:\Users\MANOJ\semabridge-working\src\semabridge\connectors\semantic_ddl_sanitizer.py"
f2_path = r"c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\semantic_ddl_sanitizer.py"

def decode_file(path):
    with open(path, 'rb') as f:
        content = f.read()
    for encoding in ['utf-16le', 'utf-16', 'utf-8', 'latin-1']:
        try:
            return content.decode(encoding)
        except Exception:
            continue
    return content.decode('utf-8', errors='ignore')

if os.path.exists(f1_path) and os.path.exists(f2_path):
    f1_lines = decode_file(f1_path).splitlines(keepends=True)
    f2_lines = decode_file(f2_path).splitlines(keepends=True)
    
    diff = list(difflib.unified_diff(
        f2_lines, f1_lines, 
        fromfile='dev-test/semantic_ddl_sanitizer.py', 
        tofile='semabridge-working/semantic_ddl_sanitizer.py'
    ))
    
    out_diff = r"c:\Users\MANOJ\dev-test\semabridge\Scratch\sanitizer_diff.txt"
    with open(out_diff, 'w', encoding='utf-8') as f:
        f.writelines(diff)
    print(f"Diff written to {out_diff} successfully.")
else:
    print("Files not found")
