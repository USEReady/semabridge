with open("tests/test_databricks_publisher.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

results = []
search_terms = ["corporate", "ioh", "dsi_calculation", "last_refreshed"]
for i, line in enumerate(lines):
    if any(term in line.lower() for term in search_terms):
        results.append(f"Line {i+1}: {line.strip()}")

with open("output/debug/search_test_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(results))
