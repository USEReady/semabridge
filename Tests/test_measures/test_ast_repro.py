from semabridge.converter.dax_ast_parser import try_ast_translate

expr = 'CALCULATE(SUM(Sales[Amount]),Sales[Status]="Sold")'
res = try_ast_translate(expr, table_alias='analysis dax', primary_table_name='Analysis DAX')
print("try_ast_translate Result:", res)

from semabridge.converter.dax_translator import DAXTranslator
t = DAXTranslator()
trans = t.translate(expr, 'analysis dax', 'Analysis DAX', metric_name='Net Sales', skip_tier5=True)
print("DAXTranslator Result:", trans.sql, "tier:", trans.tier, "is_success:", trans.is_success)
