#!/usr/bin/env python3
"""
Quick diagnostic: How to restore deployment logs visibility

This shows the difference between buffered and unbuffered output.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 70)
print("DEPLOYMENT LOGS VISIBILITY DIAGNOSTIC")
print("=" * 70)

print("""
PROBLEM: You're not seeing deployment logs when you click Deploy

REASON: Backend is running with BUFFERED output
- Logs ARE being generated and executed
- Logs ARE being written to files
- Logs are just NOT appearing in your terminal in real-time


SOLUTION: Run backend with UNBUFFERED output
""")

print("\n" + "=" * 70)
print("HOW TO FIX (Pick ONE of these):")
print("=" * 70)

print("""
┌─ OPTION 1: Use the startup script (EASIEST) ──────────────────────┐
│                                                                     │
│  $ python run_backend.py                                            │
│                                                                     │
│  This runs: python -u src/semabridge/api/main.py                  │
│  Result: ✓ All logs appear in real-time                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─ OPTION 2: Run directly with -u flag ─────────────────────────────┐
│                                                                     │
│  $ python -u src/semabridge/api/main.py                            │
│                                                                     │
│  The -u flag = unbuffered output                                   │
│  Result: ✓ All logs appear in real-time                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─ OPTION 3: Set environment variable ──────────────────────────────┐
│                                                                     │
│  $ $env:PYTHONUNBUFFERED=1                                          │
│  $ python src/semabridge/api/main.py                               │
│                                                                     │
│  This tells Python to never buffer output                          │
│  Result: ✓ All logs appear in real-time                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
""")

print("=" * 70)
print("WHAT YOU'LL SEE (with the fix):")
print("=" * 70)

print("""
When you run the backend correctly and click Deploy, you'll see logs like:

  [03/14/26 13:01:53] INFO     Syncing model: Competitive Marketing Analysis
  [03/14/26 13:01:53] INFO     Step 1: Loading and validating configuration
  [03/14/26 13:01:53] INFO     Step 2: Initializing identifiers
  [03/14/26 13:01:53] INFO     Step 3: Resolving authentication
  [03/14/26 13:01:53] INFO     Step 4: Extracting from fabric
  [03/14/26 13:01:55] INFO     Listing semantic models in workspace...
  [03/14/26 13:01:55] INFO     Found 20 semantic models
  ... (and so on for all steps)
  [03/14/26 13:02:20] INFO     Deployment successful
  [03/14/26 13:02:21] INFO     Step 10: Finalizing run with status SUCCESS
""")

print("=" * 70)
print("TEST IT NOW:")
print("=" * 70)

print("""
1. Open PowerShell/Terminal
2. cd c:\\Users\\chara\\semabridge_merged
3. Run ONE of the commands above (e.g., python run_backend.py)
4. Keep this terminal open
5. Open browser at http://localhost:5174
6. Click Deploy
7. Watch Terminal for logs - they should appear immediately!

If logs still don't appear, it's a different issue. Report the exact
error message you see.
""")

# Now test the logging system itself
print("\n" + "=" * 70)
print("TESTING LOGGING SYSTEM:")
print("=" * 70 + "\n")

from semabridge.utils.logger import setup_logging, get_logger

print("1. Setting up logging with INFO level...")
setup_logging(level="INFO")
print("   ✓ Logging configured\n")

print("2. Creating logger for 'semabridge.core'...")
logger = get_logger("semabridge.core.execution_engine")
print("   ✓ Logger created\n")

print("3. Testing log output (you should see these messages):\n")
logger.info("✓ Step 1: Configuration initialized")
logger.info("✓ Step 2: Validating source format") 
logger.info("✓ Step 3: Extracting from source")
logger.info("✓ Step 4: Converting to SML format")
logger.info("✓ Step 5: Generating Snowflake DDL")
logger.info("✓ Step 6: Deploying to Snowflake")

print("\n" + "=" * 70)
print("If you see ✓ messages above, logging is working correctly.")
print("=" * 70)
