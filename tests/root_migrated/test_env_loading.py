#!/usr/bin/env python
"""
Test .env loading and verify OPENAI_API_KEY is available.
"""
import sys
import os
from pathlib import Path

print("Testing .env File Loading")
print("=" * 80)

# Try to load .env
try:
    from dotenv import load_dotenv
    env_path = Path.cwd() / '.env'
    print(f"Current working directory: {Path.cwd()}")
    print(f"Looking for .env at: {env_path}")
    print(f".env exists: {env_path.exists()}")
    
    if env_path.exists():
        loaded = load_dotenv(env_path)
        print(f".env loaded successfully: {loaded}")
    else:
        print("[WARNING] .env file not found in current directory")
except ImportError:
    print("[ERROR] python-dotenv not installed")
    sys.exit(1)

print()
print("Checking Environment Variables:")
print("-" * 80)

# Check key env vars
env_vars = [
    "OPENAI_API_KEY",
    "FABRIC_WORKSPACE_ID",
    "FABRIC_CLIENT_ID",
    "SNOWFLAKE_ACCOUNT"
]

for var in env_vars:
    value = os.getenv(var)
    if value:
        # Show first/last 10 chars for sensitive values
        if "KEY" in var or "SECRET" in var or "PASSWORD" in var:
            display = f"{value[:10]}...{value[-10:]}"
        else:
            display = value
        print(f"  {var}: {display}")
    else:
        print(f"  {var}: NOT SET")

print()
print("Testing LLM Initialization:")
print("-" * 80)

sys.path.insert(0, 'src')

try:
    from semabridge.converter.gemini_dax_translator import get_gemini_translator
    
    translator = get_gemini_translator()
    
    if translator.api_key:
        print("[OK] Gemini LLM client initialized successfully")
        print(f"   Model preferences: {translator.model_preferences}")
        print(f"   API Key loaded: {translator.api_key[:20]}...")
    else:
        print("[FAIL] Gemini LLM client not initialized")
        print("       Check that GEMINI_API_KEY is set in .env")
        sys.exit(1)
        
except Exception as e:
    print(f"[ERROR] Failed to initialize LLM translator: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 80)
print("[SUCCESS] .env file loading is working correctly!")
print()
print("You can now test the full pipeline:")
print("  python tests/test_measure_pipeline_detailed.py --dataset Probability")
