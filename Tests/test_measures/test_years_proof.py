import os
import pytest
from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig

pbix_path = os.environ.get(
    "SEMABRIDGE_LIFE_EXPECTANCY_PBIX",
    os.path.join(os.environ.get("SEMABRIDGE_PBIX_DIR", ""), "Life expectancy v202009.pbix"),
)
if not os.path.isfile(pbix_path):
    pytest.skip(f"PBIX file not found at {pbix_path}", allow_module_level=True)
conn = LocalPBIXConnector({"pbix_path": pbix_path})
raw_tmsl = conn.discover().get("raw_tmsl") or {}
osi = TMSLToOSIConverter().to_osi({"tmsl": raw_tmsl, "dataset_id": "Life_expectancy"})
sml = OSIToSMLConverter().from_osi(osi)

years_ds = next(d for d in sml.datasets if d.unique_name == "Years")
cfg = SnowflakeConfig(account="test", user="test", password="pwd", warehouse="wh", database="SEMABRIDGE", schema_name="LIFE_EXPECTANCY")
emitter = SnowflakeEmitter(cfg)
sm = emitter.schema_manager

print("Current resolution of 'Years' on Years dataset:")
print("with model=sml:", sm._resolve_physical_column_name(years_ds, "Years", model=sml))
print("with model=None:", sm._resolve_physical_column_name(years_ds, "Years", model=None))

