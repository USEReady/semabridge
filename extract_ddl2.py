with open(r'C:\Users\MANOJ\.gemini\antigravity-ide\brain\0d98ad24-11bc-46da-8aed-468d96514264\.system_generated\tasks\task-1031.log', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "CREATE OR REPLACE SEMANTIC VIEW" in line:
        with open('ddl_dump.txt', 'w', encoding='utf-8') as out:
            out.writelines(lines[i:i+150])
        print("Dumped.")
        break
