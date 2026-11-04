import sys, os
sys.path.insert(0, os.path.abspath("src"))

from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from pbixray import PBIXRay
import json

pbix_path = r"C:\Users\manoj.s\Downloads\PBIX Files\Artificial Intelligence Sample.pbix"
ray = PBIXRay(pbix_path)
tmsl_dict = json.loads(ray.tmsl)

osi_model = TMSLToOSIConverter().convert(tmsl_dict)
sml_model = OSIToSMLConverter().convert(osi_model)

for m in sml_model.metrics:
    if m.unique_name in ("Revenue In Pipeline", "Forecast Adjustment Value"):
        print(f"\n--- Metric: {m.unique_name} ---")
        print(f"Dataset: {m.dataset}")
        print(f"Sync enabled: {m.sync_enabled}")
        print(f"SQL Expression: {m.sql_expression}")
        print(f"Failure Reason: {m.sync_failure_reason}")
