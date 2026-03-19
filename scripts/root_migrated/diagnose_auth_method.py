#!/usr/bin/env python3
"""
Diagnose the Fabric auth method selection issue.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 80)
print("FABRIC AUTH METHOD SELECTION DIAGNOSIS")
print("=" * 80)

from semabridge.repository.credential_manager import CredentialManager
from semabridge.core.settings import get_settings

cm = CredentialManager()

print("\n[STEP 1] Check for saved MSAL token")
print("-" * 80)
token_data = cm.get_msal_token()
if token_data and token_data.get("access_token"):
    print(f"[FOUND] Saved MSAL token exists")
    print(f"  Username: {token_data.get('account_username', 'unknown')}")
    print(f"  Has access_token: True")
    print(f"  Has refresh_token: {bool(token_data.get('refresh_token'))}")
else:
    print(f"[NOT FOUND] No saved MSAL token")

print("\n[STEP 2] Check for client_secret in environment")
print("-" * 80)
try:
    settings = get_settings()
    client_secret = settings.fabric.client_secret if settings.fabric else None
    if client_secret:
        print(f"[FOUND] client_secret in settings")
        print(f"  Value: {'***hidden***'}")
    else:
        print(f"[NOT FOUND] client_secret is None in settings")
except Exception as e:
    print(f"[ERROR] Could not read settings: {e}")

print("\n[STEP 3] Check environment variables directly")  
print("-" * 80)
env_vars = {
    "FABRIC_TENANT_ID": os.environ.get("FABRIC_TENANT_ID"),
    "FABRIC_CLIENT_ID": os.environ.get("FABRIC_CLIENT_ID"),
    "FABRIC_CLIENT_SECRET": "***SET***" if os.environ.get("FABRIC_CLIENT_SECRET") else "NOT SET",
    "FABRIC_WORKSPACE_ID": os.environ.get("FABRIC_WORKSPACE_ID"),
}

for key, val in env_vars.items():
    print(f"  {key}: {val}")

print("\n[STEP 4] What auth method is chosen?")
print("-" * 80)
auth_method = cm.get_fabric_auth_method()
print(f"Selected auth method: {auth_method}")

print("\n[STEP 5] THE PROBLEM")
print("-" * 80)

if token_data and token_data.get("access_token"):
    print("""
The issue is HERE:
1. There is a saved MSAL token from a previous login
2. The get_fabric_auth_method() checks for this FIRST
3. If it finds one, it ALWAYS chooses "interactive" mode
4. Even though you have FABRIC_CLIENT_SECRET set!

When discovery is called over HTTP:
  - It tries to use "interactive" auth
  - Interactive auth tries device-code flow
  - Device-code flow waits for terminal user input
  - HTTP request times out waiting forever
  - Frontend gets stuck showing "discovering..."

SOLUTION: Delete the saved MSAL token
""")
else:
    print(f"""
Current auth method: {auth_method}

If this is "service_principal":
  ✓ Discovery should work fine (no hang)
  
If this is "none":
  ✗ No credentials available - discovery will fail with 401
  → Set FABRIC_CLIENT_SECRET in .env
""")

print("\n[STEP 6] How to clear the MSAL token")
print("-" * 80)
print("""
Option A (Automatic):
  Run this Python command:
  
    python -c "from semabridge.repository.credential_manager import CredentialManager; CredentialManager().clear_msal_token(); print('MSAL token cleared')"

Option B (Manual):
  The token is likely stored in:
  - SQLite database in the app data folder
  - Or user credentials table
  
  You can also just restart completely and let it use service_principal auth.

After clearing:
  1. Restart backend: python run_backend.py
  2. Frontend discovery should work immediately
  3. No interactive auth needed
""")

print("\n" + "=" * 80)
