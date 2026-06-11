import sys
sys.path.append('src')
from semabridge.connectors.snowflake_emitter import _sanitize_snowflake_date_functions

test_sqls = [
    'DATE_TRUNC("COL_DATE"."YEAR", MAX_DATE)',
    'DATE_TRUNC(COL_DATE."YEAR", MAX_DATE)',
    'DATE_TRUNC("COL_DATE".YEAR, MAX_DATE)',
    'DATE_TRUNC(COL_DATE.YEAR, MAX_DATE)',
    'DATE_TRUNC(\'COL_DATE."YEAR"\', MAX_DATE)',
    "DATE_TRUNC('COL_DATE.YEAR', MAX_DATE)",
    'DATE_TRUNC(COL_DATE."YEAR", "MAX_DATE")',
    'DATE_TRUNC( "COL_DATE" . "YEAR" , MAX_DATE )',
]

for sql in test_sqls:
    out = _sanitize_snowflake_date_functions(sql)
    print(f"IN:  {sql}\nOUT: {out}\n")
