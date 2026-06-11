import sqlglot
from sqlglot import exp
sql = 'DATE_TRUNC("COL_DATE"."YEAR", MAX_DATE)'
ast = sqlglot.parse_one(sql, read="snowflake")
node = list(ast.find_all(exp.TimestampTrunc))[0]
unit = node.args.get("unit")
print("Unit type:", type(unit))
print("Unit properties:", unit.args if hasattr(unit, 'args') else "none")
print("Unit name:", unit.name if hasattr(unit, 'name') else "none")
