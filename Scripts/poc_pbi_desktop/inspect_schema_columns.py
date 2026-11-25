import sys
import json
from pathlib import Path

import clr
clr.AddReference(r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll")
from Microsoft.AnalysisServices.AdomdClient import AdomdConnection, AdomdCommand

port = 61470
catalog = "ad023c8e-cfd2-4118-9384-acb93155b216"
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
}

for name, q in queries.items():
    cmd = conn.CreateCommand()
    cmd.CommandText = q
    rdr = cmd.ExecuteReader()
    fc = rdr.FieldCount
    cols = [rdr.GetName(i) for i in range(fc)]
    print(f"=== {name} ({fc} columns) ===")
    print(cols)
    rdr.Close()

conn.Close()
