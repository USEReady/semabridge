#!/usr/bin/env python
"""
Stream-based Sync Runner - Captures and displays ALL sync output
Shows deployment progress in real-time on console
"""

import os
import sys
import subprocess
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def format_output(line: str) -> str:
    """Format output with colors and structure."""
    line = line.rstrip()
    
    # Errors
    if any(x in line.lower() for x in ['error', 'failed', 'exception', 'traceback']):
        return f"[ERROR] {line}"
    
    # Success
    if any(x in line.lower() for x in ['success', 'completed', 'deployed', 'synced']):
        return f"[OK] {line}"
    
    # Warnings
    if any(x in line.lower() for x in ['warning', 'skip', 'omitted']):
        return f"[WARN] {line}"
    
    # Info blocks (Stage headers)
    if re.match(r'^(Stage|Phase|\[).*(\]|:)$', line):
        return f"[INFO] {line}"
    
    # Metric/Measure lines
    if 'measure' in line.lower() or 'metric' in line.lower():
        return f"[METRIC] {line}"
    
    # DAX expression
    if 'dax' in line.lower() or 'sql' in line.lower() or 'expression' in line.lower():
        return f"[EXPR] {line}"
    
    # Regular info
    if any(x in line.lower() for x in ['processing', 'extracting', 'translating', 'deploying', 'querying']):
        return f"[PROC] {line}"
    
    # Table/view names
    if any(x in line for x in ['_SEMANTIC', 'FABRIC', 'SNOWFLAKE', 'TABLE']):
        return f"[TABLE] {line}"
    
    return line


def run_sync_with_streaming(source="fabric", target="snowflake"):
    """Run sync and stream output in real-time."""
    
    print("\n" + "="*100)
    print("SEMABRIDGE SYNC - REAL-TIME OUTPUT")
    print("="*100)
    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Direction: {source.upper()} -> {target.upper()}")
    print("\n" + "-"*100 + "\n")
    
    # Build command
    cmd = [
        sys.executable,
        "main.py",
        "semantic-sync",
        source,
        target,
    ]
    
    print(f"Running: {' '.join(cmd)}\n")
    
    try:
        # Start process with live output streaming
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffering
            universal_newlines=True,
        )
        
        # Stream output in real-time
        output_lines = []
        while True:
            line = process.stdout.readline()
            
            if not line:
                break
            
            formatted_line = format_output(line.rstrip())
            print(formatted_line)
            output_lines.append(line.rstrip())
        
        # Wait for completion
        return_code = process.wait()
        
        print("\n" + "-"*100)
        print(f"\nCompleted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        if return_code == 0:
            print("[OK] SYNC COMPLETED SUCCESSFULLY")
        else:
            print(f"[ERROR] SYNC FAILED (Exit code: {return_code})")
        
        # Save full log
        os.makedirs('output', exist_ok=True)
        log_file = f"output/sync_stream_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        with open(log_file, 'w') as f:
            f.write("\n".join(output_lines))
        
        print(f"Saved log to: {log_file}")
        print("\n" + "="*100 + "\n")
        
        return return_code
    
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    """Main execution."""
    if len(sys.argv) > 1:
        source = sys.argv[1] if sys.argv[1] in ['fabric', 'snowflake'] else 'fabric'
        target = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] in ['fabric', 'snowflake'] else 'snowflake'
    else:
        source = 'fabric'
        target = 'snowflake'
    
    return run_sync_with_streaming(source, target)


if __name__ == "__main__":
    sys.exit(main())
