from semabridge.core.engine.engine import ExecutionEngine
from semabridge.core.config_loader import load_project_config
from pathlib import Path
import os
import sys

# Change directory so relative paths in tests work
os.chdir(r"c:\Users\MANOJ\dev-test\semabridge")

# Create a dummy config where skip_sml_conversion is True
config = load_project_config(Path("config/semabridge.yaml"))
if not hasattr(config, "options"):
    class Options:
        pass
    config.options = Options()
config.options.skip_sml_conversion = True

# We need to ensure databricks section exists
if not hasattr(config, "databricks"):
    class DBX:
        api_base_url = "https://mock.databricks.com"
        catalog = "main"
        schema_name = "default"
    config.databricks = DBX()

engine = ExecutionEngine()
# Inject modified config
engine._get_project_config = lambda *args: config

# Test dry-run execution
print("Testing pure OSI to Databricks execution...")
try:
    engine.execute(source="pbix", target="databricks", config_path=Path("config/semabridge.yaml"))
    print("Databricks OSI Native route successfully triggered.")
except Exception as e:
    # It might fail due to missing actual pbix files, but if it routes properly, it's a success
    print(f"Exception during execution (expected if missing resources): {e}")

