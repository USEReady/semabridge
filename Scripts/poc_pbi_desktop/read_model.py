"""Model extraction script for Power BI Desktop Analysis Services engine.

Connects to the local running Analysis Services engine via ADOMD.NET
(using Microsoft.PowerBI.AdomdClient.dll) and extracts:
- INFO.TABLES()
- INFO.COLUMNS()
- INFO.MEASURES()
- INFO.RELATIONSHIPS()
- INFO.PARTITIONS()
and the TMSCHEMA_* equivalents.

Saves raw JSON fixtures under Scripts/poc_pbi_desktop/recorded/.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from Scripts.poc_pbi_desktop.discover import discover_running_pbi_instances

RECORDED_DIR = Path(__file__).resolve().parent / "recorded"


def _load_adomd_client() -> Any:
    """Load Microsoft.PowerBI.AdomdClient.dll via pythonnet / clr."""
    import clr

    dll_candidates = [
        r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll",
        r"C:\Program Files\WindowsApps\Microsoft.MicrosoftPowerBIDesktop_2.160.1151.0_x64__8wekyb3d8bbwe\bin\Microsoft.PowerBI.AdomdClient.dll",
        r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.AnalysisServices.AdomdClient.dll",
    ]
    for dll in dll_candidates:
        if Path(dll).exists():
            clr.AddReference(dll)
            break
    else:
        clr.AddReference("Microsoft.AnalysisServices.AdomdClient")

    try:
        from Microsoft.PowerBI.AdomdClient import AdomdConnection, AdomdCommand
        return AdomdConnection, AdomdCommand
    except ImportError:
        from Microsoft.AnalysisServices.AdomdClient import AdomdConnection, AdomdCommand
        return AdomdConnection, AdomdCommand


def run_query(conn: Any, AdomdCommand: Any, query: str) -> List[Dict[str, Any]]:
    """Execute a query (DAX or DMV) and return rows as list of dicts."""
    cmd = conn.CreateCommand()
    cmd.CommandText = query
    rdr = cmd.ExecuteReader()
    field_count = rdr.FieldCount
    col_names = [rdr.GetName(i) for i in range(field_count)]
    rows: List[Dict[str, Any]] = []
    while rdr.Read():
        row_dict: Dict[str, Any] = {}
        for i, col in enumerate(col_names):
            val = rdr.GetValue(i)
            # Handle DBNull or .NET null
            if val is None or type(val).__name__ == "DBNull":
                row_dict[col] = None
            else:
                row_dict[col] = val
        rows.append(row_dict)
    rdr.Close()
    return rows


def extract_and_record_model(port: Optional[int] = None, catalog: Optional[str] = None) -> Dict[str, Any]:
    """Extract model metadata from running engine and save recorded fixtures."""
    AdomdConnection, AdomdCommand = _load_adomd_client()

    if port is None:
        instances = discover_running_pbi_instances()
        if not instances:
            raise RuntimeError("No running Power BI Desktop engine found!")
        # Find instance with active port
        inst = instances[0]
        port = inst["port"]
        print(f"Using discovered instance on port {port} (file: {inst['file_name']})")

    # Connect to find catalog if not specified
    temp_conn = AdomdConnection(f"Provider=MSOLAP;Data Source=localhost:{port};")
    temp_conn.Open()
    if catalog is None:
        ds = temp_conn.GetSchemaDataSet("DBSCHEMA_CATALOGS", None)
        catalogs = [row["CATALOG_NAME"] for row in ds.Tables[0].Rows]
        if not catalogs:
            temp_conn.Close()
            raise RuntimeError(f"No catalog found on port {port}!")
        catalog = catalogs[0]
        print(f"Discovered catalog: {catalog}")
    temp_conn.Close()

    conn_str = f"Provider=MSOLAP;Data Source=localhost:{port};Initial Catalog={catalog};"
    conn = AdomdConnection(conn_str)
    conn.Open()

    queries = {
        "info_tables": "EVALUATE INFO.TABLES()",
        "info_columns": "EVALUATE INFO.COLUMNS()",
        "info_measures": "EVALUATE INFO.MEASURES()",
        "info_relationships": "EVALUATE INFO.RELATIONSHIPS()",
        "info_partitions": "EVALUATE INFO.PARTITIONS()",
        "tmschema_tables": "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES",
        "tmschema_columns": "SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS",
        "tmschema_measures": "SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES",
        "tmschema_relationships": "SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS",
        "tmschema_partitions": "SELECT * FROM $SYSTEM.TMSCHEMA_PARTITIONS",
        "tmschema_model": "SELECT * FROM $SYSTEM.TMSCHEMA_MODEL",
        "tmschema_hierarchies": "SELECT * FROM $SYSTEM.TMSCHEMA_HIERARCHIES",
        "tmschema_levels": "SELECT * FROM $SYSTEM.TMSCHEMA_LEVELS",
        "tmschema_expressions": "SELECT * FROM $SYSTEM.TMSCHEMA_EXPRESSIONS",
    }

    results: Dict[str, List[Dict[str, Any]]] = {}
    RECORDED_DIR.mkdir(parents=True, exist_ok=True)

    for name, q in queries.items():
        try:
            print(f"Executing {name}...")
            rows = run_query(conn, AdomdCommand, q)
            results[name] = rows
            out_file = RECORDED_DIR / f"{name}.json"
            out_file.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
            print(f"  -> Saved {len(rows)} rows to {out_file.name}")
        except Exception as e:
            print(f"  -> Error executing {name}: {e}")

    conn.Close()
    return results


if __name__ == "__main__":
    extract_and_record_model()
