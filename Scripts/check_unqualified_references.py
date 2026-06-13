import os
import re
import sys
from pathlib import Path

def main():
    files_to_check = sys.argv[1:]
    if not files_to_check:
        output_dir = Path("output")
        if output_dir.exists():
            files_to_check = [str(p) for p in output_dir.rglob("*.sql")]

    if not files_to_check:
        print("No SQL files found to check.")
        sys.exit(0)

    # Regex to find double-quoted identifiers that are NOT preceded by another identifier and dot.
    # We want to catch things like: SUM("UNITS")
    # But ignore things like: SALESFACT."UNITS"
    # Also ignore aliases: AS "ALIAS"
    
    unqualified_pattern = re.compile(r'(?<![A-Za-z0-9_"]\.)("([A-Za-z0-9_ ]+)")')
    as_alias_pattern = re.compile(r'(?i)\bAS\s+"[^"]+"')
    
    errors_found = False

    for file_path in files_to_check:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            continue

        # Very basic check: just looking at lines inside the METRICS clause for now
        in_metrics = False
        for line_num, line in enumerate(content.splitlines(), 1):
            if line.strip().startswith("METRICS ("):
                in_metrics = True
                continue
            if in_metrics and line.strip().startswith(");"):
                in_metrics = False
                continue
                
            if in_metrics:
                # Remove AS "ALIAS" to reduce false positives
                line_without_aliases = as_alias_pattern.sub("", line)
                
                # Check for unqualified references
                matches = unqualified_pattern.findall(line_without_aliases)
                if matches:
                    errors_found = True
                    print(f"[{file_path}:{line_num}] Unqualified reference found: {matches[0][0]}")
                    print(f"  Line: {line.strip()}")

    if errors_found:
        print("\nERROR: Found unqualified column references in DDL.")
        print("Please ensure all column references are qualified with table aliases (e.g., ALIAS.\"COLUMN_NAME\").")
        sys.exit(1)
    else:
        print("No unqualified references found.")
        sys.exit(0)

if __name__ == "__main__":
    main()
