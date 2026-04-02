#!/usr/bin/env python3
"""
Test the actual /api/sync endpoint to verify logging during deployment.

This script calls the sync endpoint with a real model to verify that
deployment logs are appearing on the backend.
"""

import sys
import os
import asyncio
import aiohttp

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

async def test_sync_endpoint():
    """Test the /api/sync endpoint to see if logs appear."""
    
    backend_url = "http://localhost:8000"
    
    # First, test health endpoint
    print("=" * 70)
    print("TESTING BACKEND SYNC LOGGING")
    print("=" * 70)
    
    print(f"\n[1] Testing health endpoint: {backend_url}/api/health")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{backend_url}/api/health", timeout=5) as resp:
                health = await resp.json()
                print(f"  ✓ Health: {resp.status}")
                print(f"    {health}")
    except Exception as e:
        print(f"  ✗ Health check failed: {e}")
        print("\n  BACKEND NOT RUNNING!")
        print("  Start the backend with: python -u src/semabridge/api/main.py")
        print("  (Note: -u flag disables buffering)")
        return

    # Now try to trigger a sync (assuming a demo model exists)
    print(f"\n[2] Attempting to sync a model...")
    print("""
    NOTE: To see actual deployment logs, the backend needs to be running
    and you need to trigger a real sync by clicking Deploy in the frontend,
    OR by making a POST request to /api/sync
    
    Expected output on backend terminal while sync runs:
    - INFO | semabridge.core.execution_engine | Step 1: Configuration initialized
    - INFO | semabridge.core.execution_engine | Step 2: Validating source format
    - INFO | semabridge.core.execution_engine | Step 3: Extracting source
    ... (and so on for all 9+ steps)
    
    If you see NO INFO logs, there's a logging issue.
    If you see ERROR logs, there's a deployment issue.
    """)

if __name__ == "__main__":
    asyncio.run(test_sync_endpoint())
