import sys
sys.path.insert(0, ".")

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
from semabridge.converter.dax_translator import DAXTranslator

dax = """CALCULATE (
    COUNT( Opportunities[Value] ),
    FILTER (
        Opportunities,
        Opportunities[Status] = "Open"
          --  && Opportunities[PipelineStep] IN { "3-Pipeline", "4-Mandate", "5-Close" }
    )
)"""

print("--- AST Parser direct ---")
try:
    parser = DaxAstParser()
    ast = parser.parse(dax)
    renderer = DaxSqlRenderer(table_alias="OPPORTUNITIES")
    res = renderer.render(ast)
    print("AST result:", res)
except Exception as e:
    import traceback; traceback.print_exc()

print("--- DAXTranslator direct ---")
try:
    t = DAXTranslator()
    res2 = t.translate(dax, "OPPORTUNITIES", "OPPORTUNITIES")
    print("DAXTranslator result:", res2)
except Exception as e:
    import traceback; traceback.print_exc()
