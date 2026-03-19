#!/usr/bin/env python
"""
Run Sync With Verbose Logging - Enable DEBUG mode for full output

This enables all debug/info logs so you see EVERYTHING happening during sync
"""

import os
import sys
import subprocess
import logging
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def setup_verbose_environment():
    """Configure environment for verbose logging."""
    
    # Set logging environment variables
    env = os.environ.copy()
    env['SEMABRIDGE_LOG_LEVEL'] = 'DEBUG'
    env['LOG_LEVEL'] = 'DEBUG'
    env['PYTHONUNBUFFERED'] = '1'  # Unbuffered output
    
    return env


def run_sync_verbose():
    """Run sync with maximum verbosity."""
    
    print("\n" + "="*100)
    print("SYNC WITH VERBOSE LOGGING")
    print("="*100)
    print("Showing ALL debug/info messages\n")
    
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n" + "-"*100 + "\n")
    
    # Get environment with verbose settings
    env = setup_verbose_environment()
    
    # Build command with verbose flags
    cmd = [
        sys.executable,
        "main.py",
        "semantic-sync",
        "fabric",
        "snowflake",
    ]
    
    print(f"Command: {' '.join(cmd)}")
    print(f"Log level: DEBUG")
    print(f"Output: Unbuffered (real-time)\n")
    print("-"*100 + "\n")
    
    try:
        # Run with live streaming
        process = subprocess.run(
            cmd,
            env=env,
            text=True,
        )
        
        print("\n" + "-"*100)
        print(f"\nCompleted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        if process.returncode == 0:
            print("[OK] SYNC COMPLETED SUCCESSFULLY")
        else:
            print(f"[ERROR] SYNC FAILED (Exit code: {process.returncode})")
        
        print("\n" + "="*100 + "\n")
        
        return process.returncode
    
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    """Main execution."""
    return run_sync_verbose()


if __name__ == "__main__":
    sys.exit(main())
