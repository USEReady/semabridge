import re

with open(r'C:\Users\MANOJ\.gemini\antigravity-ide\brain\0d98ad24-11bc-46da-8aed-468d96514264\.system_generated\tasks\task-1031.log', 'r', encoding='utf-8') as f:
    log_data = f.read()

# Find the CREATE OR REPLACE SEMANTIC VIEW block
match = re.search(r'CREATE OR REPLACE SEMANTIC VIEW.*?(?=ERROR    DDL\[0\] failed)', log_data, re.DOTALL)
if match:
    with open('ddl_dump.txt', 'w', encoding='utf-8') as out:
        out.write(match.group(0))
    print("Dumped DDL successfully.")
else:
    print("Could not find DDL.")
