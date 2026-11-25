import sys
from pathlib import Path

import clr
dll_path = r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll"
clr.AddReference(dll_path)
from Microsoft.AnalysisServices.AdomdClient import AdomdConnection, AdomdCommand

for port in [59761, 59756]:
    print(f"\n================ PORT {port} ================")
    conn_str = f"Provider=MSOLAP;Data Source=localhost:{port};"
    conn = AdomdConnection(conn_str)
    try:
        conn.Open()
        print(f"Connected to port {port}.")
        
        # Discover catalogs
        catalogs = []
        try:
            ds = conn.GetSchemaDataSet("DBSCHEMA_CATALOGS", None)
            table = ds.Tables[0]
            for row in table.Rows:
                cat_name = str(row["CATALOG_NAME"])
                catalogs.append(cat_name)
            print("Found catalogs via GetSchemaDataSet:", catalogs)
        except Exception as e:
            print("Failed to get DBSCHEMA_CATALOGS:", e)

        if not catalogs and conn.Database:
            catalogs = [conn.Database]
            print("conn.Database is:", conn.Database)

        for cat in catalogs:
            print(f"\nTesting with Catalog='{cat}'...")
            cat_conn_str = f"Provider=MSOLAP;Data Source=localhost:{port};Initial Catalog={cat};"
            cat_conn = AdomdConnection(cat_conn_str)
            cat_conn.Open()
            cmd = cat_conn.CreateCommand()
            cmd.CommandText = 'EVALUATE ROW("x", 1)'
            try:
                rdr = cmd.ExecuteReader()
                if rdr.Read():
                    print(f"SUCCESS! Port {port}, Catalog '{cat}' -> Result: {rdr.GetValue(0)}")
                rdr.Close()
            except Exception as e:
                print(f"Query failed on Catalog '{cat}': {e}")
            cat_conn.Close()
            
        conn.Close()
    except Exception as e:
        print(f"Failed on port {port}: {e}")
