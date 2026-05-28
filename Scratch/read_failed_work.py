import os

def decode_file(path):
    with open(path, 'rb') as f:
        content = f.read()
    for encoding in ['utf-16le', 'utf-16', 'utf-8', 'latin-1']:
        try:
            return content.decode(encoding)
        except Exception:
            continue
    return content.decode('utf-8', errors='ignore')

def inspect_failed_work():
    files_to_inspect = [
        r"c:\Users\MANOJ\semabridge-working\output\debug\ddl_COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC.sql",
        r"c:\Users\MANOJ\semabridge-working\output\debug\ddl_COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC_SEMANTIC.sql",
        r"c:\Users\MANOJ\dev-test\semabridge\output\debug\ddl_COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC.sql",
    ]
    
    for full_path in files_to_inspect:
        if os.path.exists(full_path):
            print("="*60)
            print(f"File: {full_path}")
            content = decode_file(full_path)
            lines = content.splitlines()
            print(f"Total lines: {len(lines)}")
            
            # Print lines 80 to 90
            for idx in range(80, min(95, len(lines) + 1)):
                print(f"Line {idx}: {lines[idx-1]}")
                
            # Search for any line containing WITH SYNONYMS in a nested structure
            print("-" * 30)
            print("Lines with suspicious/nested SYNONYMS:")
            for i, line in enumerate(lines):
                if "WITH SYNONYMS" in line.upper():
                    # If there's an open parenthesis and then WITH SYNONYMS inside it, or multiple
                    if line.strip().count("WITH SYNONYMS") > 0:
                        print(f"Line {i+1}: {line.strip()}")
        else:
            print(f"Path does not exist: {full_path}")

if __name__ == "__main__":
    inspect_failed_work()
