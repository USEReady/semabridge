from semabridge.governance.drift.adapters.snowflake import SnowflakeAdapter

ddl = """CREATE OR REPLACE SEMANTIC VIEW DB.SCHEMA.VIEW (
  TABLES = (
    BU AS DB.SCHEMA.BU_RAW PRIMARY KEY (BU_ID),
    COL_DATE AS DB.SCHEMA.DATE_RAW PRIMARY KEY (DATE_KEY)
  ),
  DIMENSIONS = (
    BU."BU" WITH SYNONYMS = ('BUSINESS_UNIT'),
    COL_DATE."DATE" WITH SYNONYMS = ('DATE'),
    COL_DATE."DAY",
    COL_DATE."MONTH"
  ),
  METRICS = (
    ACTIVES AS COUNT(EMPLOYEE.ID)
  )
);"""

print("STARTING TEST...")
adapter = SnowflakeAdapter()
print("Extracting sections...")
sec = adapter._extract_sections(ddl)
print("Sections found:", list(sec.keys()))
vdef = adapter.parse_definition(ddl, 'DB.SCHEMA.VIEW')
print(f"Total objects parsed: {len(vdef.objects)}")
for o in vdef.objects:
    print(f"Kind: {o.kind} | Table: {o.table} | Name: {o.name} | Expr: {o.expression} | Props: {o.properties}")

