import sqlglot
from sqlglot import exp

_DATE_PART_KEYWORDS = frozenset({"year", "month", "day", "week", "quarter", "hour", "minute", "second"})

def _fix_unit_arg(node_unit) -> "exp.Expression | None":
    if node_unit is None:
        return None
    if isinstance(node_unit, exp.Var):
        val = node_unit.this.lower()
        return exp.Literal.string(val) if val in _DATE_PART_KEYWORDS else None
    if isinstance(node_unit, (exp.Column, exp.Identifier)):
        col_name = (node_unit.name or "").lower()
        return exp.Literal.string(col_name) if col_name in _DATE_PART_KEYWORDS else None
    if isinstance(node_unit, exp.Literal) and node_unit.is_string:
        val = node_unit.this.lower()
        return exp.Literal.string(val) if val in _DATE_PART_KEYWORDS else None
    return None

sql = "DATE_TRUNC('year', MAX_DATE)"
ast = sqlglot.parse_one(sql, read="snowflake")
for node in list(ast.find_all(exp.TimestampTrunc)):
    fixed = _fix_unit_arg(node.args.get("unit"))
    if fixed is not None:
        node.set("unit", fixed)

print("OUT:", ast.sql(dialect="snowflake"))
