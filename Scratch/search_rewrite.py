with open("src/semabridge/connectors/databricks_publisher.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

results = []
for i, line in enumerate(lines):
    if "_rewrite_metric_view_measure_expression" in line:
        results.append(f"Line {i+1}: {line.strip()}")

with open("output/debug/search_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(results))
