import sys
import socket
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent.parent
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from semabridge.reconciliation import dax_executor

print("Python executable:", sys.executable)
print("\n--- 1. Testing existing dax_executor.discover_local_pbi_ports() ---")
ports = dax_executor.discover_local_pbi_ports()
print("Discovered ports:", ports)

print("\n--- 2. Testing existing dax_executor.execute_dax (unmodified) ---")
try:
    res = dax_executor.execute_dax('EVALUATE ROW("x", 1)', backend="pbi_desktop")
    print("Result success:", res.success)
    print("Result rows:", res.rows)
except Exception as e:
    print(f"FAILED as expected: {type(e).__name__}: {e}")

print("\n--- 3. Testing with Microsoft.PowerBI.AdomdClient.dll workaround ---")
import clr
pbi_adomd_dll = r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll"
print("Referencing:", pbi_adomd_dll)
clr.AddReference(pbi_adomd_dll)
from Microsoft.AnalysisServices.AdomdClient import AdomdConnection, AdomdCommand

for p_info in ports:
    port = p_info["port"]
    ws = p_info["workspace_dir"]
    
    # Check socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    is_open = (s.connect_ex(("127.0.0.1", port)) == 0)
    s.close()
    
    print(f"\nPort {port} ({ws}): Socket is_open={is_open}")
    if not is_open:
        print(f"  Skipping closed port {port} (stale workspace).")
        continue

    conn_str = f"Provider=MSOLAP;Data Source=localhost:{port};"
    conn = AdomdConnection(conn_str)
    try:
        conn.Open()
        print(f"  Connected to port {port}!")
        ds = conn.GetSchemaDataSet("DBSCHEMA_CATALOGS", None)
        cats = [row["CATALOG_NAME"] for row in ds.Tables[0].Rows]
        print(f"  Catalogs: {cats}")
        
        cmd = conn.CreateCommand()
        cmd.CommandText = 'EVALUATE ROW("x", 1)'
        rdr = cmd.ExecuteReader()
        if rdr.Read():
            print(f"  SUCCESS! Port {port} -> EVALUATE ROW('x', 1) = {rdr.GetValue(0)}")
        rdr.Close()
        conn.Close()
    except Exception as e:
        print(f"  Port {port} error: {e}")
