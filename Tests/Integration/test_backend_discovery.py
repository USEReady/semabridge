#!/usr/bin/env python3
"""
Test if the frontend can reach the backend discovery endpoint
"""

import requests
import json

print("=" * 80)
print("TESTING FRONTEND → BACKEND DISCOVERY CALL")
print("=" * 80)

backend_url = "http://localhost:8000/api/discovery/fabric"

print(f"\nAttempting to call: {backend_url}")
print("-" * 80)

try:
    print("\n[STEP 1] Making request with NO auth headers...")
    response = requests.get(backend_url, timeout=5)
    print(f"Status Code: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    print(f"Response Body (first 500 chars):\n{response.text[:500]}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n[OK] SUCCESS! Got {len(data)} models")
        print(f"First model: {data[0] if data else 'N/A'}")
    else:
        print(f"\n[ER] Got status {response.status_code}")
        print(f"Error: {response.text}")
    
except requests.exceptions.ConnectionError as e:
    print(f"[ERROR] Cannot connect to backend!")
    print(f"Make sure backend is running on port 8000")
    print(f"  Command: python run_backend.py")
    print(f"Details: {e}")
except requests.exceptions.Timeout:
    print(f"[TIMEOUT] Request took longer than 5 seconds")
    print(f"Backend might be stuck waiting for credentials")
except Exception as e:
    print(f"[ERROR] {type(e).__name__}: {e}")

print("\n" + "=" * 80)
print("NEXT STEPS:")
print("=" * 80)
print("""
If you see:
  [OK] SUCCESS! Got 19 models
  → Backend and endpoint are working fine
  → Issue is on FRONTEND (browser, network, CORS, or JavaScript)

If you see a TIMEOUT:
  → Backend discovery endpoint is hanging
  → Likely waiting for credentials or Fabric API response

If you see a CONNECTION ERROR:
  → Backend is not running on port 8000
  → Start it with: python run_backend.py

If you see an error like "invalid credentials":
  → Authentication is not set up
  → Check: FABRIC_CLIENT_SECRET in .env
""")
