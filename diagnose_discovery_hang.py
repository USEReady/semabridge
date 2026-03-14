#!/usr/bin/env python3
"""
Diagnose why fabric discovery is hanging on the frontend.
"""

import os
import sys

# Set paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 80)
print("FABRIC DISCOVERY HANG DIAGNOSIS")
print("=" * 80)

from semabridge.repository.credential_manager import CredentialManager

print("\n[STEP 1] Check current authentication method")
print("-" * 80)

cm = CredentialManager()
auth_method = cm.get_fabric_auth_method()
print(f"Auth method detected: {auth_method}")

if auth_method == "none":
    print("\n[PROBLEM] Auth method is 'NONE'!")
    print("This means:")
    print("  - No FABRIC_CLIENT_SECRET environment variable")
    print("  - No FABRIC_ACCESS_TOKEN environment variable")
    print("  - No saved MSAL token from previous login")
    print("\nWhen frontend tries to discover, it will hang waiting for device-code auth!")

print("\n[STEP 2] Check environment variables")
print("-" * 80)

vars_to_check = [
    "FABRIC_TENANT_ID",
    "FABRIC_CLIENT_ID",
    "FABRIC_WORKSPACE_ID",
    "FABRIC_CLIENT_SECRET",
    "FABRIC_ACCESS_TOKEN",
]

for var in vars_to_check:
    value = os.environ.get(var, "NOT SET")
    if "SECRET" in var or "TOKEN" in var:
        display = "***SET***" if value != "NOT SET" else "NOT SET"
    else:
        display = value if value != "NOT SET" else "NOT SET"
    print(f"  {var}: {display}")

print("\n[STEP 3] Check saved MSAL token")
print("-" * 80)

token_data = cm.get_msal_token()
if token_data and "access_token" in token_data:
    print(f"  [OK] MSAL token found in cache")
    has_valid = cm.has_valid_token()
    print(f"  [{'OK' if has_valid else 'ER'}] Token valid: {has_valid}")
else:
    print(f"  [ER] NO MSAL token in cache")

print("\n[STEP 4] What happens when discovery is called?")
print("-" * 80)

if auth_method == "service_principal":
    print("  [OK] Service principal auth - will work without user interaction")
elif auth_method == "env_token":
    print("  [OK] Env token auth - will work without user interaction")
elif auth_method == "interactive":
    print("  [WARN] Interactive auth - will try device code flow")
    print("         But HTTP request can't show device code UI!")
    print("         Request will HANG waiting for user input")
elif auth_method == "none":
    print("  [ERROR] No auth - request will immediately fail with 401")
    print("          But frontend shows 'discovering...' anyway")

print("\n[STEP 5] Solution")
print("-" * 80)

print("\nTo fix this, you MUST provide Fabric credentials:")
print("\nOption A (FASTEST): Use Service Principal")
print("  1. Set in .env or environment:")
print("     FABRIC_TENANT_ID=<your-tenant-id>")
print("     FABRIC_CLIENT_ID=<your-client-id>")
print("     FABRIC_CLIENT_SECRET=<your-client-secret>")
print("     FABRIC_WORKSPACE_ID=<your-workspace-id>")
print("  2. Restart backend")
print("  3. Discovery will work automatically")

print("\nOption B: Use Service Principal via environment only")
print("  export FABRIC_TENANT_ID=...")
print("  export FABRIC_CLIENT_ID=...")
print("  export FABRIC_CLIENT_SECRET=...")
print("  export FABRIC_WORKSPACE_ID=...")
print("  python run_backend.py")

print("\nOption C: Use Device Code (interactive, needs terminal)")
print("  1. Start backend with: python run_backend.py")
print("  2. Frontend tries discovery")
print("  3. Backend will print device code in TERMINAL")
print("  4. Copy code and visit https://microsoft.com/devicelogin")
print("  5. Sign in with code")
print("  6. Frontend discovery completes")

print("\n" + "=" * 80)
print(f"\nCURRENT STATUS: auth_method = '{auth_method}'")
if auth_method == "none":
    print("ACTION REQUIRED: Set FABRIC_CLIENT_SECRET (or other credentials)")
else:
    print("OK: Credentials appear to be set")
print("=" * 80)
