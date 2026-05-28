path = r"c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\semantic_ddl_sanitizer.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()
    
# Print around line 335-345
lines = content.splitlines()
print(f"Total lines: {len(lines)}")
for idx in range(330, 350):
    if idx <= len(lines):
        print(f"Line {idx}: {repr(lines[idx-1])}")
