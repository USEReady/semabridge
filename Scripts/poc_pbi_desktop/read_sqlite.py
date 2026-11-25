import sqlite3

db_path = r"C:\Users\charank\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces\AnalysisServicesWorkspace_752b96e8-a26b-4349-9b9f-b2e8f68d2435\Data\4c20ab4e-2910-4a7d-9a2d-7aef5fc0815b.0.db\metadata.sqlitedb"
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = cur.fetchall()
print("Tables in sqlite:", len(tables))
for t in tables:
    t_name = t[0]
    cur.execute(f'SELECT count(*) FROM "{t_name}"')
    cnt = cur.fetchone()[0]
    if cnt > 0:
        print(f"Table {t_name}: {cnt} rows")

cur.execute('SELECT * FROM "Table"')
rows = cur.fetchall()
print("\nRows in Table:")
for r in rows:
    print(r)
