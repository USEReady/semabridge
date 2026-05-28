import os
import filecmp

f1 = r"c:\Users\MANOJ\semabridge-working\src\semabridge\connectors\metrics_clause_builder.py"
f2 = r"c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\metrics_clause_builder.py"

print("f1 exists:", os.path.exists(f1))
print("f2 exists:", os.path.exists(f2))

if os.path.exists(f1) and os.path.exists(f2):
    print("Files are identical:", filecmp.cmp(f1, f2))
    if not filecmp.cmp(f1, f2):
        print("f1 size:", os.path.getsize(f1))
        print("f2 size:", os.path.getsize(f2))
