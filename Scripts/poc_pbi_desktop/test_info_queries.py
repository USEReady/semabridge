import sys
import json
from pathlib import Path

import clr
clr.AddReference(r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll")
from Microsoft.AnalysisServices.AdomdClient import AdomdConnection, AdomdCommand

port = 61470
conn_str = f"Provider=MSOLAP;Data Source=localhost:{port};Initial Catalog=ad023c8e-cfd2-4118-9384-acb93155b216;"
conn = AdomdConnection(conn_str)
conn.Open()
print("Connected!")

queries = [
    ("INFO.TABLES()", "EVALUATE INFO.TABLES()"),
    ("INFO.COLUMNS()", "EVALUATE INFO.COLUMNS()"),
    ("INFO.MEASURES()", "EVALUATE INFO.MEASURES()"),
    ("INFO.RELATIONSHIPS()", "EVALUATE INFO.RELATIONSHIPS()"),
    ("INFO.PARTITIONS()", "EVALUATE INFO.PARTITIONS()"),
    ("TMSCHEMA_TABLES", "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES"),
    ("TMSCHEMA_COLUMNS", "SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS"),
    ("TMSCHEMA_MEASURES", "SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES"),
    ("TMSCHEMA_RELATIONSHIPS", "SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS"),
    ("TMSCHEMA_PARTITIONS", "SELECT * FROM $SYSTEM.TMSCHEMA_PARTITIONS"),
    ("TMSCHEMA_EXPRESSIONS", "SELECT * FROM $SYSTEM.TMSCHEMA_EXPRESSIONS"),
    ("TMSCHEMA_MODEL", "SELECT * FROM $SYSTEM.TMSCHEMA_MODEL"),
]

for name, q in queries:
    try:
        cmd = conn.CreateCommand()
        cmd.CommandText = q
        rdr = cmd.ExecuteReader()
        field_count = rdr.FieldCount
        col_names = [rdr.GetName(i) for i in range(field_count)]
        row_count = 0
        first_row = None
        while rdr.Read():
            row_count += 1
            if row_count == 1:
                first_row = {col_names[i]: rdr.GetValue(i) for i in range(field_count)}
        rdr.Close()
        print(f"[{name}] SUCCESS! Rows: {row_count}, Columns: {len(col_names)}")
        print(f"    Sample columns: {col_names[:8]}")
    except Exception as e:
        print(f"[{name}] FAILED: {e}")

conn.Close()
