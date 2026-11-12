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
    pytest.skip(f"PBIX file not found at {pbix_path}", allow_module_level=True)
conn = LocalPBIXConnector({"pbix_path": pbix_path})
raw = conn.discover()["raw_tmsl"]
osi = TMSLToOSIConverter().to_osi({"tmsl": raw, "dataset_id": "Life_expectancy"})
sml = OSIToSMLConverter().from_osi(osi)

cfg = SnowflakeConfig(account="test", user="test", password="pwd", warehouse="wh", database="SEMABRIDGE", schema_name="LIFE_EXPECTANCY")
emitter = SnowflakeEmitter(cfg)

# Hook into metrics_clause_builder to see failure
ddls = emitter.generate_ddls(sml)
ddl = ddls[0] if isinstance(ddls, (list, tuple)) else ddls
for line in ddl.splitlines():
    if "GDP_PER_CAPITA_USD" in line or "GDP Per Capita" in line:
        print("EMITTED LINE:", line)

