from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
import sys

parser = DaxAstParser()
ast = parser.parse("DIVIDE([Total Units], [Total Units SPLY], 0)")
renderer = DaxSqlRenderer(table_alias="FACT", date_alias="CALENDAR")
sql = renderer.render(ast)
print("SQL:", sql)
