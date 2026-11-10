import os
import pytest
from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig

pbix_path = os.environ.get(
    "SEMABRIDGE_LIFE_EXPECTANCY_PBIX",
    r"C:\Users\manoj.s\Downloads\PBIX Files\Life expectancy v202009.pbix",
)
if not os.path.isfile(pbix_path):
    pytest.skip(f"Life expectancy .pbix not found at {pbix_path}", allow_module_level=True)
conn = LocalPBIXConnector({"pbix_path": pbix_path})
discovery = conn.discover()
raw_tmsl = discovery.get("raw_tmsl") or {}
rels = raw_tmsl.get("model", {}).get("relationships", []) or discovery.get("relationships", [])
print(f"Extracted relationships count in TMSL: {len(rels)}")
for r in rels:
    print(f"  {r.get('fromTable')}[{r.get('fromColumn')}] -> {r.get('toTable')}[{r.get('toColumn')}] (active={r.get('isActive', True)})")

osi = TMSLToOSIConverter().to_osi({"tmsl": raw_tmsl, "dataset_id": "Life_expectancy"})
sml = OSIToSMLConverter().from_osi(osi)
print(f"\nSML relationships count: {len(sml.relationships)}")
for r in sml.relationships:
    print(f"  SML rel: {getattr(r, 'from_dataset', None)}[{getattr(r, 'from_columns', None)}] -> {getattr(r, 'to_dataset', None)}[{getattr(r, 'to_columns', None)}]")


cfg = SnowflakeConfig(account="test", user="test", password="pwd", warehouse="wh", database="SEMABRIDGE", schema_name="LIFE_EXPECTANCY")
emitter = SnowflakeEmitter(cfg)
ddls = emitter.generate_ddls(sml)
ddl_text = ddls[0] if isinstance(ddls, (list, tuple)) else ddls

with open("output/debug/debug_generated_sql_Life_expectancy_v202009.sql", "w", encoding="utf-8") as f:
    f.write(ddl_text)

print("\n--- DDL Lines matching YEARS ---")
for line in ddl_text.splitlines():
    if "YEARS" in line:
        print("  ", line)

print("\n--- DDL RELATIONSHIPS clause ---")
in_rel = False
for line in ddl_text.splitlines():
    if "RELATIONSHIPS" in line:
        in_rel = True
    elif in_rel and ("DIMENSIONS" in line or "METRICS" in line):
        in_rel = False
    if in_rel:
        print("  ", line)
