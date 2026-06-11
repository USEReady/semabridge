import sys
sys.path.append('src')
import sqlglot
from sqlglot import exp

tests = [
    "DATEADD(YEAR, -1, MAX_DATE)",
    "DATEADD(COL_DATE.\"YEAR\", -1, MAX_DATE)",
    "DATEDIFF(WEEK, start_date, end_date)",
    "DATE_PART(QUARTER, col)",
    "DATE_TRUNC(YEAR, MAX_DATE)",
]

for t in tests:
    try:
        ast = sqlglot.parse_one(t, read='snowflake')
        print(f"Input: {t}")
        print(f"  AST type: {type(ast).__name__}")
        print(f"  AST.args: {list(ast.args.keys())}")
        if hasattr(ast, 'expressions'):
            for i, e in enumerate(ast.expressions):
                print(f"  expressions[{i}]: type={type(e).__name__}, val={e!r}")
        if hasattr(ast, 'this'):
            print(f"  this: type={type(ast.this).__name__}, val={ast.this!r}")
        print(f"  SQL out: {ast.sql(dialect='snowflake')}")
        print()
    except Exception as ex:
        print(f"FAILED {t}: {ex}")
