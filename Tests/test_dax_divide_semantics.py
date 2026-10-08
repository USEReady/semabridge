import pytest
from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer

def test_dax_divide_semantics_zero_by_zero_yields_null():
    parser = DaxAstParser()
    renderer = DaxSqlRenderer(table_alias="T")
    ast = parser.parse("DIVIDE(0, 0)")
    sql_2args = renderer.render(ast)
    assert "NULLIF" in sql_2args
    assert "DIV0NULL" not in sql_2args
    assert "DIV0" not in sql_2args
    assert sql_2args == "(0) / NULLIF(0, 0)"

def test_dax_divide_semantics_3args_alternate_result():
    parser = DaxAstParser()
    renderer = DaxSqlRenderer(table_alias="T")
    ast = parser.parse("DIVIDE(0, 0, 0)")
    sql_3args = renderer.render(ast)
    assert "IFF(COALESCE(0, 0) = 0, 0, (0) / (0))" in sql_3args
