import sys
sys.stdout.reconfigure(encoding='utf-8')
with open(r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\translator.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("--- BLOCK 1 ---")
print("".join(lines[27:54]))
print("--- BLOCK 2 ---")
print("".join(lines[339:342]))
print("--- BLOCK 3 ---")
print("".join(lines[435:437]))
print("--- BLOCK 4 ---")
print("".join(lines[481:483]))
print("--- BLOCK 5 ---")
print("".join(lines[148:175]))
