"""Verify the full JWT auth chain."""
import requests

BASE = "http://localhost:8000"

# Test 1: Protected endpoint without token → 401
r = requests.get(f"{BASE}/api/projects")
print(f"No auth -> {r.status_code} (expected 401)")
assert r.status_code == 401, f"Expected 401, got {r.status_code}"

# Test 2: Auto-login → get JWT
r2 = requests.post(f"{BASE}/auth/auto-login")
print(f"Auto-login -> {r2.status_code}")
assert r2.status_code == 200
data = r2.json()
token = data["access_token"]
print(f"  Token: {token[:40]}...")

# Test 3: Protected endpoint with token → 200
r3 = requests.get(f"{BASE}/api/projects", headers={"Authorization": f"Bearer {token}"})
print(f"With auth -> {r3.status_code} (expected 200)")
assert r3.status_code == 200, f"Expected 200, got {r3.status_code}"

# Test 4: /auth/me → user info
r4 = requests.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"})
user = r4.json()
print(f"User: {user['username']} (id={user['id']}, role={user['role']})")

print("\n=== ALL AUTH TESTS PASSED ===")
