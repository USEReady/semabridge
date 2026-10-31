import sys
sys.path.insert(0, ".")
from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer

dax1 = """COUNTAX(
    FILTER(
        KEEPFILTERS(Opportunities), Opportunities[Status] = "Won"
    ),
    Opportunities[OpportunitySeq]
)"""

dax2 = """CALCULATE(
    SUMX(Opportunities, Opportunities[Value]),
    FILTER(Opportunities, Opportunities[Status] = "Won")
)"""

dax3 = """COUNTAX(Opportunities,TRUE())"""

dax4 = """DIVIDE(CALCULATE(COUNTROWS('Cases'), 'Cases'[Is Escalated] = TRUE()), [Case Count], 0)"""

tests = [
    ("Count of Won", dax1),
    ("Revenue Won", dax2),
    ("Opportunity Count", dax3),
    ("Escalations", dax4),
]

for name, dax in tests:
    print(f"\n=== Testing: {name} ===")
    p = DaxAstParser()
    tree = p.parse(dax)
    print("Parsed AST:", type(tree), tree)
    r = DaxSqlRenderer(table_alias="OPPORTUNITIES", measure_sql_map={"CASE_COUNT": "COUNT(*)"})
    sql = r.render(tree)
    print("Rendered SQL:", sql)
