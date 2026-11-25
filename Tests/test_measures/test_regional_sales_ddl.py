import os
from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig

import pytest

def test_regional_sales_ddl():
    pbix_path = os.environ.get(
        "SEMABRIDGE_REGIONAL_SALES_PBIX",
        os.path.join(os.environ.get("SEMABRIDGE_PBIX_DIR", ""), "Regional Sales Sample.pbix"),
    )
    if not os.path.isfile(pbix_path):
        if os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true":
            pytest.fail(f"Required PBIX fixture missing in CI: {pbix_path}")
        pytest.skip(f"Regional Sales Sample .pbix not found at {pbix_path}")
    conn = LocalPBIXConnector({"pbix_path": pbix_path})
    discovery = conn.discover()
    raw_tmsl = discovery.get("raw_tmsl") or {}

    osi = TMSLToOSIConverter().to_osi({"tmsl": raw_tmsl, "dataset_id": "Regional_Sales"})
    sml = OSIToSMLConverter().from_osi(osi)

    cfg = SnowflakeConfig(account="test", user="test", password="pwd", warehouse="wh", database="SEMABRIDGE", schema_name="REGIONAL_SALES_SAMPLE")
    emitter = SnowflakeEmitter(cfg)
    ddls = emitter.generate_ddls(sml)
    ddl_text = ddls[0] if isinstance(ddls, (list, tuple)) else ddls

    out_path = "output/debug/debug_generated_sql_Regional_Sales_Sample.sql"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ddl_text)

    print(f"Generated {out_path} successfully")

