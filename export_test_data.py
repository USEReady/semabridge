import sys
import json
import yaml
from pathlib import Path

# Ensure src is in the python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
from semabridge.utils.logger import setup_logging

def export_data():
    setup_logging(level="INFO")
    settings = get_settings()

    output_dir = Path("output/test_data")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("==================================================")
    print("Extracting Test Data from the Sync Pipeline")
    print("==================================================")

    # 1. Export from Snowflake
    try:
        print(f"\n[Snowflake] Connecting to: {settings.snowflake.database}.{settings.snowflake.schema_name}")
        sf = SnowflakeExtractor(settings.snowflake)
        if sf.test_connection():
            sf_data = sf.extract_all()
            sf_out_path = output_dir / "snowflake_test_data.yaml"
            with open(sf_out_path, "w", encoding="utf-8") as f:
                yaml.dump(sf_data, f, default_flow_style=False, sort_keys=False)
            print(f"SUCCESS: Exported Snowflake schema to: {sf_out_path}")
    except Exception as e:
        print(f"ERROR: Failed to extract Snowflake data: {e}")

    # 2. Export from Fabric
    try:
        if settings.fabric.workspace_id:
            print(f"\n[Fabric] Connecting to Workspace: {settings.fabric.workspace_id}")
            fb = FabricExtractor(settings.fabric)
            
            datasets = fb.list_semantic_models()
            if datasets:
                ds_id = datasets[0]["id"]
                ds_name = datasets[0].get("displayName", ds_id)
                print(f"[Fabric] Extracting TMSL for dataset: {ds_name} ({ds_id})")
                
                tmsl_data = fb.get_model_definition(ds_id)
                fb_out_path = output_dir / "fabric_tmsl_test_data.yaml"
                with open(fb_out_path, "w", encoding="utf-8") as f:
                    yaml.dump(tmsl_data, f, default_flow_style=False, sort_keys=False)
                print(f"SUCCESS: Exported Fabric TMSL to: {fb_out_path}")
            else:
                print("ERROR: No datasets found in the configured Fabric workspace.")
        else:
            print("\n[Fabric] Skipped: FABRIC_WORKSPACE_ID is not configured in .env.")
    except Exception as e:
        print(f"ERROR: Failed to extract Fabric data: {e}")

    print("\n==================================================")
    print("Done! You can now upload these files to the Comparator UI.")
    print("==================================================")

if __name__ == "__main__":
    export_data()
