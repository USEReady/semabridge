import os
import sys
from pathlib import Path

def read_log(path, encoding='utf-8'):
    if not os.path.exists(path):
        print(f"{path} does not exist")
        return
    print(f"=== Last 50 lines of {path} ({encoding}) ===")
    try:
        with open(path, 'r', encoding=encoding, errors='ignore') as f:
            lines = f.readlines()
            for line in lines[-50:]:
                safe_line = line.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding)
                print(safe_line.strip())
    except Exception as e:
        print(f"Error reading {path}: {e}")

if __name__ == '__main__':
    read_log('snowflake_deploy.log', 'utf-16le')
    read_log('semantic_view_debug.log', 'utf-8')
    
    global_log = Path.home() / ".semabridge" / "semabridge.log"
    read_log(str(global_log), 'utf-8')
