#!/usr/bin/env python3
"""
Check if backend is actually running and responsive
"""

import requests
import time

print("=" * 80)
print("CHECKING IF BACKEND IS RUNNING AND RESPONSIVE")
print("=" * 80)

endpoints_to_test = [
    ("GET", "/api/health", "Health check"),
    ("GET", "/api/config", "Config endpoint (no auth)"),
    ("GET", "/api/discovery/fabric", "Fabric discovery endpoint (main issue)"),
]

base_url = "http://localhost:8000"

for method, path, description in endpoints_to_test:
    url = base_url + path
    print(f"\n[TEST] {description}")
    print(f"{'─' * 60}")
    print(f"Method: {method}")
    print(f"URL: {url}")
    
    try:
        start = time.time()
        if method == "GET":
            response = requests.get(url, timeout=10)
        else:
            response = requests.post(url, timeout=10)
        elapsed = time.time() - start
        
        print(f"Status Code: {response.status_code}")
        print(f"Time: {elapsed:.2f}s")
        
        if response.status_code == 200:
            try:
                data = response.json()
                if isinstance(data, list):
                    print(f"Response: Array with {len(data)} items")
                    if data:
                        print(f"  First item: {str(data[0])[:100]}")
                else:
                    print(f"Response: {str(data)[:100]}")
            except:
                print(f"Response: {response.text[:100]}")
        else:
            print(f"Error: {response.text[:200]}")
            
    except requests.exceptions.Timeout:
        print(f"[TIMEOUT] No response after 10 seconds")
    except requests.exceptions.ConnectionError as e:
        print(f"[ERROR] Cannot connect to backend")
        print(f"Make sure backend is running on port 8000")
        print(f"Start with: python run_backend.py")
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")

print("\n" + "=" * 80)
