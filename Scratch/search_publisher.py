with open("src/semabridge/connectors/databricks_publisher.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "def " in line:
        print(f"Line {i+1}: {line.strip()}")
