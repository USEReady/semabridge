import os
import pytest
from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig

pbix_path = os.environ.get(
    "SEMABRIDGE_AI_SAMPLE_PBIX",
    r"C:\Users\manoj.s\Downloads\PBIX Files\Artificial Intelligence Sample.pbix",
)
if not os.path.isfile(pbix_path):
    pytest.skip(f"Artificial Intelligence Sample .pbix not found at {pbix_path}", allow_module_level=True)
conn = LocalPBIXConnector({"pbix_path": pbix_path})
discovery = conn.discover()
raw_tmsl = discovery.get("raw_tmsl") or {}
rels = raw_tmsl.get("model", {}).get("relationships", []) or discovery.get("relationships", [])
print(f"Raw TMSL relationships count: {len(rels)}")
for r in rels:
    print(f"  {r.get('fromTable')}[{r.get('fromColumn')}] -> {r.get('toTable')}[{r.get('toColumn')}] (active={r.get('isActive', True)})")

osi = TMSLToOSIConverter().to_osi({"tmsl": raw_tmsl, "dataset_id": "AI_Sample"})
sml = OSIToSMLConverter().from_osi(osi)
print(f"\nSML relationships count: {len(sml.relationships)}")
for r in sml.relationships:
    print(f"  {r.from_dataset}[{r.from_columns}] -> {r.to_dataset}[{r.to_columns}]")

cfg = SnowflakeConfig(account="test", user="test", password="pwd", warehouse="wh", database="SEMABRIDGE", schema_name="AI_SAMPLE")
emitter = SnowflakeEmitter(cfg)
ddls = emitter.generate_ddls(sml)
ddl_text = ddls[0] if isinstance(ddls, (list, tuple)) else ddls

print("\n--- DDL RELATIONSHIPS clause ---")
in_rel = False
for line in ddl_text.splitlines():
    if "RELATIONSHIPS" in line:
        in_rel = True
    elif in_rel and ("DIMENSIONS" in line or "METRICS" in line):
        in_rel = False
    if in_rel:
        print("  ", line)

print("\n--- DDL METRICS (checking FORECAST, REVENUE_IN_PIPELINE) ---")
for line in ddl_text.splitlines():
    if any(m in line for m in ["FORECAST", "REVENUE_IN_PIPELINE"]):
        print("  ", line)

