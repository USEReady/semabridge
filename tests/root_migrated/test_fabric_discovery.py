#!/usr/bin/env python3
"""
Diagnostic script to debug Fabric model discovery issues.

Tests:
1. Are Fabric credentials properly loaded?
2. Is auth method correctly detected?
3. Can FabricExtractor initialize?
4. Can FabricExtractor list models?
5. What's the actual error?
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 70)
print("FABRIC DISCOVERY DIAGNOSTIC")
print("=" * 70)

# Test 1: Environment variables
print("\n[TEST 1] Environment Variables")
print("-" * 70)

fabric_vars = {
    "FABRIC_TENANT_ID": os.environ.get("FABRIC_TENANT_ID", "NOT SET"),
    "FABRIC_CLIENT_ID": os.environ.get("FABRIC_CLIENT_ID", "NOT SET"),
    "FABRIC_CLIENT_SECRET": os.environ.get("FABRIC_CLIENT_SECRET", "NOT SET"),
    "FABRIC_WORKSPACE_ID": os.environ.get("FABRIC_WORKSPACE_ID", "NOT SET"),
}

for key, value in fabric_vars.items():
    masked = value[:20] + "..." if len(value) > 20 and value != "NOT SET" else value
    status = "✓" if value != "NOT SET" else "✗"
    print(f"{status} {key}: {masked}")

# Test 2: Settings loading
print("\n[TEST 2] Settings Loading")
print("-" * 70)

try:
    from semabridge.core.settings import get_settings
    settings = get_settings()
    print(f"✓ Settings loaded")
    print(f"  Fabric tenant_id: {settings.fabric.tenant_id[:16]}...")
    print(f"  Fabric client_id: {settings.fabric.client_id[:16]}...")
    print(f"  Fabric has client_secret: {settings.fabric.client_secret is not None}")
    print(f"  Fabric workspace_id: {settings.fabric.workspace_id}")
except Exception as e:
    print(f"✗ Failed to load settings: {e}")
    sys.exit(1)

# Test 3: Auth method detection
print("\n[TEST 3] Auth Method Detection")
print("-" * 70)

try:
    from semabridge.repository.credential_manager import CredentialManager
    cm = CredentialManager()
    auth_method = cm.get_fabric_auth_method()
    print(f"✓ Auth method detected: {auth_method}")
except Exception as e:
    print(f"✗ Failed to get auth method: {e}")
    sys.exit(1)

# Test 4: FabricExtractor initialization
print("\n[TEST 4] FabricExtractor Initialization")
print("-" * 70)

try:
    from semabridge.connectors.fabric_extractor import FabricExtractor
    extractor = FabricExtractor(settings.fabric)
    print(f"✓ FabricExtractor initialized successfully")
except Exception as e:
    print(f"✗ Failed to initialize FabricExtractor: {type(e).__name__}: {e}")
    sys.exit(1)

# Test 5: List models
print("\n[TEST 5] Listing Semantic Models")
print("-" * 70)

try:
    models = extractor.list_semantic_models()
    print(f"✓ Successfully retrieved {len(models)} semantic models")
    
    if models:
        print(f"\nModels:")
        for model in models[:5]:  # Show first 5
            model_id = model.get("id", "N/A")
            model_name = model.get("displayName", "N/A")
            print(f"  - {model_name} ({model_id})")
        
        if len(models) > 5:
            print(f"  ... and {len(models) - 5} more")
    else:
        print("⚠️  No models found in workspace")
        
except Exception as e:
    print(f"✗ Failed to list models: {type(e).__name__}")
    print(f"  Error: {str(e)[:200]}")
    import traceback
    print("\nFull traceback:")
    traceback.print_exc()

# Test 6: Direct API call test
print("\n[TEST 6] Testing API Endpoint Behavior")
print("-" * 70)

print(f"""
To test the actual /api/discovery/fabric endpoint:

1. Start backend with logs:
   python run_backend.py

2. In another terminal, query the endpoint:
   curl -X GET http://localhost:8000/api/discovery/fabric

3. Check the backend terminal for:
   - Auth method detected (should be "service_principal")
   - FabricExtractor initialization message
   - Number of models discovered
   - Any error messages

4. If you see errors, they will appear in the backend logs.
""")

print("\n" + "=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)

if all(v != "NOT SET" for v in fabric_vars.values()):
    print("\n✓ All environment variables are set")
    if auth_method == "service_principal":
        print("✓ Using service-principal authentication")
        print("✓ FabricExtractor should work")
    else:
        print(f"⚠️  Using auth method: {auth_method}")
else:
    print("\n✗ Some environment variables are missing")
