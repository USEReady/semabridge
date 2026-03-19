#!/usr/bin/env python3
"""
Test the FULL discovery pipeline - backend API + frontend expectations.

This will help us identify exactly where the Fabric discovery is breaking.
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 70)
print("FULL FABRIC DISCOVERY PIPELINE TEST")
print("=" * 70)

# Test 1: Backend discovery works
print("\n[STEP 1] Testing Backend Discovery Endpoint")
print("-" * 70)

from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor

settings = get_settings()
extractor = FabricExtractor(settings.fabric)

try:
    models = extractor.list_semantic_models()
    print(f"✓ Backend successfully discovered {len(models)} models")
    print(f"\nModels found:")
    for m in models[:3]:
        print(f"  - {m['displayName']} (ID: {m['id']})")
    if len(models) > 3:
        print(f"  ... and {len(models) - 3} more")
except Exception as e:
    print(f"✗ Backend discovery failed: {e}")
    sys.exit(1)

# Test 2: Test what the API endpoint returns
print("\n[STEP 2] Testing What The API Endpoint Returns")
print("-" * 70)

print("""
The /api/discovery/fabric endpoint should return JSON in this format:

[
  {
    "id": "151be242-fffc-4091-8e6f-51428eb307ca",
    "name": "Competitive Marketing Analysis",
    "type": "semantic_model",
    "status": "Available"
  },
  ...
]

To test this manually:
1. Start backend: python run_backend.py
2. Open another terminal and run:
   curl -X GET http://localhost:8000/api/discovery/fabric
3. You should see the JSON response with all models
""")

# Test 3: The frontend call
print("\n[STEP 3] Frontend Discovery Flow")
print("-" * 70)

print("""
When you open the frontend and click "Source Browser":

1. SourceBrowser component mounts
2. It calls: api.getDiscovery('fabric', activeWorkspaceId)
3. This hits: GET /discovery/fabric
4. Response should contain an array of models
5. Models should render in the UI tree

EXPECTED FLOW:
├── Root-Fabric
│   ├── annual
│   ├── continent
│   ├── Competitive Marketing Analysis  ← You should see this!
│   ├── Core_Finance_v1
│   └── ... and 15 more models
""")

# Test 4: Network inspection
print("\n[STEP 4] How To Debug Using Browser DevTools")
print("-" * 70)

print("""
STEPS:
1. Open http://localhost:5174 in Chrome/Edge/Firefox
2. Press F12 to open DevTools
3. Go to the Network tab
4. Click on the "Source Browser" tab (or similar)
5. Look for a request to:
   - http://localhost:8000/api/discovery/fabric
   
CHECK:
├─ Status Code: Should be 200
├─ Response: Should show JSON array with models
├─ Headers: Check for CORS errors (red)
└─ Timing: Should complete in <2 seconds

IF YOU SEE ERRORS:
• 404: Endpoint doesn't exist (check backend)
• 401: Authentication issue
• 500: Backend error (check backend logs)
• No request at all: Frontend not calling the endpoint

ERROR RESPONSES:
If response is an error object like:
{
  "detail": "No valid Fabric credentials found..."
}

Then auth method detection failed. Check:
1. FABRIC_CLIENT_SECRET is set in .env
2. .env file is actually loaded by backend
3. FabricExtractor can authenticate
""")

# Test 5: Data format verification
print("\n[STEP 5] Verifying Response Format")
print("-" * 70)

# Simulate what the API returns
api_response = [
    {
        "id": m["id"],
        "name": m["displayName"],
        "type": "semantic_model",
        "status": "Available"
    }
    for m in models
]

import json
print("✓ Sample API response (first 2 models):")
print(json.dumps(api_response[:2], indent=2))

# Test 6: Frontend rendering check
print("\n[STEP 6] Frontend Rendering Check")
print("-" * 70)

print("""
Once you verify the API returns data:

1. Check if models appear in the UI
2. If not, check browser console (F12) for JavaScript errors
3. Look for logged items:
   - "Discovered X models from fabric"
   - Or errors like "Failed to discover models"

COMPONENT LOCATIONS:
- Source Browser: frontend/src/components/SourceBrowser.jsx (line 360)
- API calls: frontend/src/utils/api.js (line 39: getDiscovery function)
- Context: frontend/src/context/* (handles workspace)
""")

print("\n" + "=" * 70)
print("NEXT STEPS:")
print("=" * 70)

print("""
1. VERIFY BACKEND IS WORKING:
   python test_fabric_discovery.py
   ✓ Should show "Successfully retrieved X semantic models"

2. START THE BACKEND:
   python run_backend.py
   
3. TEST THE API MANUALLY:
   curl http://localhost:8000/api/discovery/fabric
   ✓ Should return JSON array with models

4. CHECK FRONTEND:
   - Open http://localhost:5174
   - Open DevTools (F12)
   - Go to Network tab
   - Look at "Discover Models" or "Source Browser" action
   - Check if /api/discovery/fabric request is made
   - Verify response is 200 with models

5. IF STILL NOT WORKING:
   - Check browser console for JavaScript errors
   - Check backend terminal for Python errors
   - Enable detailed logging: set LOG_LEVEL=DEBUG in .env
   - Report the exact error message from one of these sources
""")

print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE - Ready for frontend testing!")
print("=" * 70)
