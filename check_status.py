import time
log_path = r'C:\Users\MANOJ\.gemini\antigravity-ide\brain\0d98ad24-11bc-46da-8aed-468d96514264\.system_generated\tasks\task-1198.log'
for _ in range(30):
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if "DDL[0] failed" in content:
                print("FAILED!")
                break
            if "SQL compilation error" in content:
                print("SQL compilation error!")
                break
            if "Semantic view created successfully" in content or "SUCCESS" in content or "Successfully executed DDL" in content:
                print("SUCCESS!")
                break
    except Exception:
        pass
    time.sleep(1)
else:
    print("Timeout waiting for DDL execution.")
