import os
import sys
import glob
import json
import traceback
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent.parent
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

print("=== SCRIPT STARTED ===")
print("Repo root:", repo_root)
print("Python:", sys.executable, sys.version)

import semabridge.reconciliation.dax_executor as de

# Check both original and store app patterns
patterns_to_check = [
    *de._PBI_PORT_PATTERNS,
    r"%USERPROFILE%\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt",
]

print("\n=== CHECKING PORT PATTERNS ===")
found_ports = []
for pat in patterns_to_check:
    exp = os.path.expandvars(pat)
    matches = glob.glob(exp)
    print(f"Pattern: {pat}")
    print(f"Expanded: {exp}")
    print(f"Matches ({len(matches)}): {matches}")
    for m in matches:
        p = Path(m)
        try:
            raw = p.read_bytes()
            text = raw.decode("utf-16-le", errors="ignore").strip()
            digits = "".join(ch for ch in text if ch.isdigit())
            if not digits:
                text = raw.decode("utf-8", errors="ignore").strip()
                digits = "".join(ch for ch in text if ch.isdigit())
            if digits:
                found_ports.append({
                    "port": int(digits),
                    "workspace_dir": p.parent.parent.name,
                    "port_file": str(p),
                })
        except Exception as e:
            print(f"Error reading {p}: {e}")

print("Found ports:", found_ports)

# Test existing dax_executor without monkey-patching first:
print("\n=== EXISTING DAX_EXECUTOR DISCOVER ===")
existing_discovered = de.discover_local_pbi_ports()
print("Existing discover_local_pbi_ports returned:", existing_discovered)

print("\n=== TESTING ADOMD DRIVER / PYTHONNET / PYADOMD ===")
for p_info in found_ports:
    port = p_info["port"]
    ws = p_info["workspace_dir"]
    conn_str = f"Provider=MSOLAP;Data Source=localhost:{port};"
    print(f"\n--- Testing port {port} (workspace {ws}) ---")
    try:
        mode, drv = de._open_adomd_connection(conn_str)
        print("Driver mode:", mode)
    except Exception as e:
        print("Error opening ADOMD connection:")
        traceback.print_exc()

# If discover returned empty, test execute_dax with monkey-patched pattern
print("\n=== RUNNING EXECUTE_DAX ===")
de._PBI_PORT_PATTERNS = tuple(patterns_to_check)
try:
    res = de.execute_dax('EVALUATE ROW("x", 1)', backend="pbi_desktop")
    print("execute_dax result success:", res.success)
    print("execute_dax columns:", res.columns)
    print("execute_dax rows:", res.rows)
    print("execute_dax error:", res.error)
    print("execute_dax metadata:", res.metadata)
except Exception as e:
    print("execute_dax raised exception:")
    traceback.print_exc()
