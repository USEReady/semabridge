import os
import re

def decode_file(path):
    with open(path, 'rb') as f:
        content = f.read()
    for encoding in ['utf-16le', 'utf-16', 'utf-8', 'latin-1']:
        try:
            return content.decode(encoding)
        except Exception:
            continue
    return content.decode('utf-8', errors='ignore')

def inspect_ddl_files():
    debug_dir = r"c:\Users\MANOJ\dev-test\semabridge\output\debug"
    for file in os.listdir(debug_dir):
        if file.startswith("ddl_COMPETITIVE_MARKETING") and file.endswith('.sql'):
            full_path = os.path.join(debug_dir, file)
            print("="*60)
            print(f"File: {full_path}")
            content = decode_file(full_path)
            lines = content.splitlines()
            
            print(f"Total lines: {len(lines)}")
            # Print around line 82 and 85
            for line_no in range(80, 115):
                if line_no <= len(lines):
                    print(f"Line {line_no}: {lines[line_no - 1]}")
            
            print("="*60)
            print("All lines with WITH SYNONYMS:")
            for i, line in enumerate(lines):
                if "WITH SYNONYMS" in line.upper():
                    print(f"Line {i+1}: {line.strip()}")

if __name__ == "__main__":
    inspect_ddl_files()
