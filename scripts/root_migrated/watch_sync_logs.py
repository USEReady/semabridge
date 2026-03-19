#!/usr/bin/env python
"""
Monitor Sync Logs - Watch deployment logs in real-time from CLI

This shows logs being written by the CLI sync process
"""

import os
import sys
import time
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def watch_sync_logs(max_wait_seconds=300):
    """Watch and display sync logs as they're written."""
    
    print("\n" + "="*100)
    print("SYNC LOG MONITOR")
    print("="*100 + "\n")
    
    try:
        from semabridge.repository.command_logger import get_command_logger
        from semabridge.utils.enterprise_logger import get_enterprise_logger
        
        cmd_logger = get_command_logger()
        ent_logger = get_enterprise_logger()
        
        print("Log Directories:")
        print(f"  Command logs: {cmd_logger.db_path}")
        print(f"  Enterprise logs: {ent_logger.logs_dir}")
        print()
        
        # Get latest operation log
        print("Recent Command Logs:")
        print("-" * 100)
        
        recent_logs = cmd_logger.get_recent_logs(limit=5)
        
        if not recent_logs:
            print("No logs found yet. Start a sync operation...")
            return 0
        
        for i, log_entry in enumerate(recent_logs, 1):
            timestamp = log_entry.started_at
            command = log_entry.command
            status = log_entry.status
            duration = log_entry.elapsed_ms
            
            status_icon = {
                'success': '[OK]',
                'failed': '[ERROR]',
                'in_progress': '[PROC]',
            }.get(status, '[?]')
            
            print(f"\n{i}. {status_icon} {command.upper()}")
            print(f"   Status:    {status}")
            print(f"   Started:   {timestamp}")
            print(f"   Duration:  {duration}ms" if duration else "   Duration:  (in progress)")
            print(f"   Details:   {log_entry.details}")
        
        # Show enterprise log files
        print("\n" + "-"*100)
        print("Recent Operation Logs:")
        print("-"*100)
        
        log_files = ent_logger.get_log_files()
        
        if not log_files or not log_files.get('operations'):
            print("No operation logs found")
        else:
            for log_file in sorted(log_files['operations'], reverse=True)[:3]:
                log_path = Path(ent_logger.logs_dir) / 'operations' / log_file
                
                if log_path.exists():
                    print(f"\n File: {log_file}")
                    print("- " * 50)
                    
                    with open(log_path, 'r') as f:
                        lines = f.readlines()
                        # Show last 20 lines
                        for line in lines[-20:]:
                            print(f"  {line.rstrip()}")
        
        print("\n" + "="*100)
        print("[OK] LOG MONITOR COMPLETE")
        print("="*100 + "\n")
        
        return 0
    
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


def tail_operation_logs(lines=50):
    """Tail the latest operation log file."""
    
    print("\n" + "="*100)
    print("TAILING LATEST OPERATION LOG")
    print("="*100 + "\n")
    
    try:
        from semabridge.utils.enterprise_logger import get_enterprise_logger
        
        ent_logger = get_enterprise_logger()
        log_files = ent_logger.get_log_files()
        
        if not log_files or not log_files.get('operations'):
            print("[ERROR] No operation logs found")
            return 1
        
        # Get latest log file
        latest_log = sorted(log_files['operations'], reverse=True)[0]
        log_path = Path(ent_logger.logs_dir) / 'operations' / latest_log
        
        print(f"Latest log: {latest_log}\n")
        print("-"*100 + "\n")
        
        with open(log_path, 'r') as f:
            all_lines = f.readlines()
            # Show last N lines
            for line in all_lines[-lines:]:
                print(line.rstrip())
        
        print("\n" + "="*100 + "\n")
        
        return 0
    
    except Exception as e:
        print(f"[ERROR] {e}")
        return 1


def main():
    """Main execution."""
    
    if len(sys.argv) > 1:
        if sys.argv[1] == 'tail':
            lines = int(sys.argv[2]) if len(sys.argv) > 2 else 50
            return tail_operation_logs(lines)
    
    return watch_sync_logs()


if __name__ == "__main__":
    sys.exit(main())
