import sys
sys.path.append('src')
from semabridge.connectors.snowflake_emitter import _sanitize_snowflake_date_functions

cases = [
    # (description, input, expected_fragment)
    ("Table-qualified YEAR",      'DATE_TRUNC(COL_DATE."YEAR", MAX_DATE)',       "date_trunc('year'"),
    ("Bare unquoted YEAR",        'DATE_TRUNC(YEAR, MAX_DATE)',                   "date_trunc('year'"),
    ("Already single-quoted",     "DATE_TRUNC('year', MAX_DATE)",                 "date_trunc('year'"),
    ("Already double-quoted",     'DATE_TRUNC("year", MAX_DATE)',                  "date_trunc('year'"),
    ("DATEADD table-qualified",   'DATEADD(COL_DATE."YEAR", -1, MAX_DATE)',        "dateadd('year'"),
    ("DATEADD bare MONTH",        'DATEADD(MONTH, 1, MAX_DATE)',                   "dateadd('month'"),
    ("DATE_PART table-qualified", 'DATE_PART(COL_DATE."QUARTER", col)',            "date_part('quarter'"),
    ("DATEDIFF WEEK",             'DATEDIFF(WEEK, start_date, end_date)',          "datediff('week'"),
    ("Uppercase in quotes",       "DATE_TRUNC('YEAR', MAX_DATE)",                  "date_trunc('year'"),
    ("Complex expression",        "SUM(CASE WHEN col >= DATE_TRUNC(COL_DATE.\"YEAR\", MAX_DATE) THEN 1 END)",
                                   "date_trunc('year'"),
]

all_pass = True
for desc, inp, expected in cases:
    out = _sanitize_snowflake_date_functions(inp)
    ok = expected.lower() in out.lower()
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"[{status}] {desc}")
    if not ok:
        print(f"       IN:  {inp}")
        print(f"       OUT: {out}")
        print(f"       EXP: ...{expected}...")

print()
if all_pass:
    print("All tests passed!")
else:
    print("SOME TESTS FAILED!")
    sys.exit(1)
