#!/usr/bin/env python3
"""
Test the actual Fabric API call to see where the hang happens.
"""

import os
import sys
import asyncio
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 80)
print("TESTING FABRIC API CALL (Service Principal)")
print("=" * 80)

# Test 1: Can we build the token request?
print("\n[STEP 1] Getting service principal token...")
print("-" * 80)

from semabridge.core.settings import get_settings

settings = get_settings()
fabric_config = settings.fabric

print(f"Tenant ID: {fabric_config.tenant_id}")
print(f"Client ID: {fabric_config.client_id}")
print(f"Workspace ID: {fabric_config.workspace_id}")
print(f"Client Secret: {'***SET***' if fabric_config.client_secret else 'NOT SET'}")

# Test 2: Can we get a token via MSAL?
print("\n[STEP 2] Attempting to get token via MSAL...")
print("-" * 80)

try:
    import msal
    import time
    
    authority = f"https://login.microsoftonline.com/{fabric_config.tenant_id}"
    print(f"Authority: {authority}")
    
    start = time.time()
    app = msal.ConfidentialClientApplication(
        client_id=fabric_config.client_id,
        client_credential=fabric_config.client_secret.get_secret_value(),
        authority=authority,
    )
    init_time = time.time() - start
    print(f"MSAL app created in {init_time:.2f}s")
    
    print("Requesting token...")
    start = time.time()
    result = app.acquire_token_for_client(
        scopes=['https://api.fabric.microsoft.com/.default'],
    )
    token_time = time.time() - start
    print(f"Token request completed in {token_time:.2f}s")
    
    if 'access_token' in result:
        token = result['access_token']
        print(f"[OK] Got access token (length: {len(token)})")
    else:
        print(f"[ERROR] Token request failed")
        print(f"Response: {result}")
        sys.exit(1)
        
except Exception as e:
    print(f"[ERROR] {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Can we call the Fabric API?
print("\n[STEP 3] Calling Fabric API...")
print("-" * 80)

try:
    import httpx
    
    workspace_id = fabric_config.workspace_id
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/semanticModels"
    
    print(f"URL: {url}")
    print("Making request (timeout=10s)...")
    
    start = time.time()
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
    api_time = time.time() - start
    
    print(f"API call completed in {api_time:.2f}s")
    print(f"Status Code: {resp.status_code}")
    
    if resp.status_code == 200:
        models = resp.json().get("value", [])
        print(f"[OK] Got {len(models)} models from Fabric")
    else:
        print(f"[ERROR] API returned {resp.status_code}")
        print(f"Response: {resp.text[:500]}")
        
except httpx._exceptions.ReadTimeout:
    print(f"[TIMEOUT] Fabric API did not respond within 10 seconds")
except Exception as e:
    print(f"[ERROR] {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("TIMING SUMMARY")
print("=" * 80)
print(f"MSAL app init: {init_time:.2f}s")
print(f"Token request: {token_time:.2f}s")
print(f"Fabric API call: {api_time:.2f}s")
print(f"Total: {init_time + token_time + api_time:.2f}s")
