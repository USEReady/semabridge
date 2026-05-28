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

def read_working_metrics():
    path = r"c:\Users\MANOJ\semabridge-working\src\semabridge\connectors\metrics_clause_builder.py"
    if os.path.exists(path):
        content = decode_file(path)
        lines = content.splitlines()
        print(f"Total lines in working metrics: {len(lines)}")
        
        # Search for _prune_unresolved_metric_lines definition
        start_line = 0
        for i, line in enumerate(lines):
            if "def _prune_unresolved_metric_lines" in line:
                start_line = i
                break
                
        if start_line > 0:
            print("="*60)
            print(f"Showing lines from {start_line} onwards:")
            for j in range(start_line, min(start_line + 100, len(lines))):
                print(f"{j+1}: {lines[j]}")
        else:
            print("Method def not found")
    else:
        print("Working metrics file not found")

if __name__ == "__main__":
    read_working_metrics()
