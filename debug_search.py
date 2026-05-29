import glob, re

for filename in glob.glob("src/**/*.py", recursive=True):
    with open(filename, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    # Search for occurrences of dictionary style expression access on metric
    if ".get(" in content and "expression" in content:
        for line_num, line in enumerate(content.splitlines(), 1):
            if "expression" in line and (".get" in line or "[" in line):
                print(f"DICT_ACCESS in {filename}:{line_num}: {line.strip()}")
                
    # Search for expression or "NULL" patterns
    if "expression or" in content or "expression_or" in content or "AS NULL" in content:
        for line_num, line in enumerate(content.splitlines(), 1):
            if "expression or" in line or "AS NULL" in line:
                print(f"NULL_PATTERN in {filename}:{line_num}: {line.strip()}")
