import sys
import os

sys.path.insert(0, r"d:\bolt\semabridge\src")

# Need to load any env vars that might be injected
from semabridge.repository.credential_manager import CredentialManager
from semabridge.core.settings import reload_settings

cm = CredentialManager()
cm.inject_credentials_to_env("snowflake")

s = reload_settings()
import json

print("SNOWFLAKE SETTINGS:")
print(json.dumps(s.snowflake.model_dump(exclude_unset=False), indent=2, default=str))

print("\nDUCKDB CREDENTIALS:")
for key, val in cm.get_all_credentials()["snowflake"].items():
    print(f"  {key}: {'*******' if 'pass' in key else val}")

print("\nOS ENVIRON:")
for k, v in sorted(os.environ.items()):
    if k.startswith("SNOWFLAKE_") or k.startswith("SEMABRIDGE_"):
        print(f"  {k}={v}")
