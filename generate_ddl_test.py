import json
import logging
import asyncio
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

logging.basicConfig(level=logging.INFO)

async def main():
    file_path = r"c:\Users\MANOJ\dev-test\semabridge\output\debug\f7914a07-7232-46d4-aedd-fc5cf5b426f1\raw_fabric_model.json"
    with open(file_path, "r", encoding="utf-8-sig") as f:
        tmsl_data = json.load(f)
    
    source_data = {
        "tmsl": {"model": tmsl_data.get("model", tmsl_data)},
        "workspace_id": "test",
        "dataset_id": "test",
        "display_name": "Competitive Marketing Analysis"
    }

    tmsl_converter = TMSLToOSIConverter()
    osi_model = tmsl_converter.to_osi(source_data)
    
    sml_converter = OSIToSMLConverter()
    sml_model = sml_converter.from_osi(osi_model)

    emitter = SnowflakeEmitter()
    ddl_statements = emitter.generate_ddls(sml_model)
    for i, ddl in enumerate(ddl_statements):
        with open(f"test_ddl_{i}.sql", "w", encoding="utf-8") as f:
            f.write(ddl)
        print(f"Wrote test_ddl_{i}.sql")

if __name__ == "__main__":
    asyncio.run(main())
