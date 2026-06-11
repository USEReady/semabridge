import json
from pathlib import Path

out_dir = Path(r"c:\Users\MANOJ\dev-test\semabridge\output\intermediate_artifacts")

with open(out_dir / "1_raw_tmsl.json", "r", encoding="utf-8") as f:
    tmsl = json.load(f).get("model", {})

with open(out_dir / "3_osi_model.json", "r", encoding="utf-8") as f:
    osi = json.load(f)

with open(out_dir / "4_sml_model.json", "r", encoding="utf-8") as f:
    sml = json.load(f)

print("--- TMSL ---")
print(f"Tables: {len(tmsl.get('tables', []))}")
print(f"Relationships: {len(tmsl.get('relationships', []))}")
measures_tmsl = sum(len(t.get('measures', [])) for t in tmsl.get('tables', []))
columns_tmsl = sum(len(t.get('columns', [])) for t in tmsl.get('tables', []))
print(f"Measures: {measures_tmsl}")
print(f"Columns: {columns_tmsl}")

print("\n--- OSI ---")
print(f"Datasets: {len(osi.get('datasets', []))}")
print(f"Metrics: {len(osi.get('metrics', []))}")
print(f"Relationships: {len(osi.get('relationships', []))}")
columns_osi = sum(len(d.get('columns', [])) for d in osi.get('datasets', []))
print(f"Columns: {columns_osi}")

print("\n--- SML ---")
print(f"Datasets: {len(sml.get('datasets', []))}")
print(f"Metrics: {len(sml.get('metrics', []))}")
print(f"Relationships: {len(sml.get('relationships', []))}")
columns_sml = sum(len(d.get('columns', [])) for d in sml.get('datasets', []))
print(f"Columns: {columns_sml}")
