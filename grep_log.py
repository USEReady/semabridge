with open(r'C:\Users\MANOJ\.gemini\antigravity-ide\brain\0d98ad24-11bc-46da-8aed-468d96514264\.system_generated\tasks\task-1031.log', 'r', encoding='utf-8') as f:
    for line in f:
        if "TOTAL_UNITS_YTD_SPLY" in line or "TOTAL_UNITS_YTD" in line:
            print(line.strip())
