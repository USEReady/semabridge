import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer

dax = "CALCULATE(SUM([Amount]), ALL([Date]))"
parser = DaxAstParser()
ast = parser.parse(dax)
print("AST parsed:", ast)

renderer = DaxSqlRenderer(table_alias="sales", date_alias="dates")
try:
    sql = renderer._render_node(ast)
    print("Rendered SQL:", sql)
except Exception as e:
    import traceback
    traceback.print_exc()
