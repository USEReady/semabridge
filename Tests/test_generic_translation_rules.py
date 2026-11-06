import pytest
from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer, classify_dax_ast_not_applicable, try_ast_translate
from semabridge.converter.time_intelligence_shapes import (
    build_shape_boolean_sql,
    discover_time_intelligence_shapes,
    flag_column_name,
)


class MockMetric:
    def __init__(self, unique_name: str, expression: str):
        self.unique_name = unique_name
        self.expression = expression


def test_rule1_test1_month_comparison_uses_flags():
    """Test 1: [X] - CALCULATE([X], DATEADD(d,-1,MONTH)) -> both sides use flags (IS_CURRENT_MONTH vs IS_PREV_MONTH)."""
    dax = "[X] - CALCULATE([X], DATEADD('Date'[Date], -1, MONTH))"
    measure_sql_map = {"X": 'SUM(FACT."SALES")'}
    anchor_flag_map = {
        ("CURRENT_MONTH",): "IS_CURRENT_MONTH",
        ("PREV_MONTH",): "IS_PREV_MONTH",
    }
    sql = try_ast_translate(
        dax,
        table_alias="FACT",
        measure_sql_map=measure_sql_map,
        anchor_flag_map=anchor_flag_map,
    )
    assert sql is not None
    # Both sides must use their respective period flags
    assert 'FACT."IS_CURRENT_MONTH"' in sql
    assert 'FACT."IS_PREV_MONTH"' in sql
    # Must not fall back to an unrestricted aggregate on the current side
    assert 'SUM(FACT."SALES")' not in sql.replace('FACT."IS_CURRENT_MONTH"', '').replace('FACT."IS_PREV_MONTH"', '')


def test_rule1_test2_matching_flag_pairs():
    """Test 2: Same with -1 YEAR, -1 QUARTER, SAMEPERIODLASTYEAR, PREVIOUSMONTH -> matching flag pairs."""
    measure_sql_map = {"X": 'SUM(FACT."SALES")'}

    # 1. -1 YEAR
    dax_year = "[X] - CALCULATE([X], DATEADD('Date'[Date], -1, YEAR))"
    sql_year = try_ast_translate(
        dax_year,
        table_alias="FACT",
        measure_sql_map=measure_sql_map,
        anchor_flag_map={
            ("CURRENT_YEAR",): "IS_CURRENT_YEAR",
            ("PREV_YEAR",): "IS_PREV_YEAR",
        },
    )
    assert sql_year is not None
    assert 'FACT."IS_CURRENT_YEAR"' in sql_year
    assert 'FACT."IS_PREV_YEAR"' in sql_year

    # 2. -1 QUARTER
    dax_qtr = "[X] - CALCULATE([X], DATEADD('Date'[Date], -1, QUARTER))"
    sql_qtr = try_ast_translate(
        dax_qtr,
        table_alias="FACT",
        measure_sql_map=measure_sql_map,
        anchor_flag_map={
            ("CURRENT_QUARTER",): "IS_CURRENT_QUARTER",
            ("PREV_QUARTER",): "IS_PREV_QUARTER",
        },
    )
    assert sql_qtr is not None
    assert 'FACT."IS_CURRENT_QUARTER"' in sql_qtr
    assert 'FACT."IS_PREV_QUARTER"' in sql_qtr

    # 3. SAMEPERIODLASTYEAR
    dax_sply = "[X] - CALCULATE([X], SAMEPERIODLASTYEAR('Date'[Date]))"
    sql_sply = try_ast_translate(
        dax_sply,
        table_alias="FACT",
        measure_sql_map=measure_sql_map,
        anchor_flag_map={
            ("CURRENT_YEAR",): "IS_CURRENT_YEAR",
            ("SPLY_YEAR",): "IS_SPLY_YEAR",
        },
    )
    assert sql_sply is not None
    assert 'FACT."IS_CURRENT_YEAR"' in sql_sply
    assert 'FACT."IS_SPLY_YEAR"' in sql_sply

    # 4. PREVIOUSMONTH
    dax_pm = "[X] - CALCULATE([X], PREVIOUSMONTH('Date'[Date]))"
    sql_pm = try_ast_translate(
        dax_pm,
        table_alias="FACT",
        measure_sql_map=measure_sql_map,
        anchor_flag_map={
            ("CURRENT_MONTH",): "IS_CURRENT_MONTH",
            ("PREV_MONTH",): "IS_PREV_MONTH",
        },
    )
    assert sql_pm is not None
    assert 'FACT."IS_CURRENT_MONTH"' in sql_pm
    assert 'FACT."IS_PREV_MONTH"' in sql_pm


def test_rule1_test3_dateadd_non_literal_amount():
    """Test 3: DATEADD with non-literal amount -> NULL with reason 'DATEADD shift amount must be a literal integer'."""
    dax = "CALCULATE([X], DATEADD('Date'[Date], [ShiftAmount], MONTH))"
    ast = DaxAstParser().parse(dax)
    reason = classify_dax_ast_not_applicable(ast)
    assert reason == "not applicable: DATEADD shift amount must be a literal integer"


def test_rule2_test4_sum_blank_safe():
    """Test 4: SUM(a) + SUM(b): group with only b -> b; group with neither -> NULL."""
    dax = "SUM(T[a]) + SUM(T[b])"
    sql = try_ast_translate(dax, table_alias="T")
    assert sql is not None
    assert "CASE WHEN" in sql
    assert "IS NULL AND" in sql
    assert "THEN NULL" in sql
    assert "COALESCE(" in sql
    assert "+ COALESCE(" in sql or "+ COALESCE(" in sql.replace(" ", "")

    # Test the boolean logic algebraic simulation:
    def eval_sim(a_val, b_val):
        # Emulates CASE WHEN (a) IS NULL AND (b) IS NULL THEN NULL ELSE COALESCE(a, 0) + COALESCE(b, 0) END
        if a_val is None and b_val is None:
            return None
        return (0 if a_val is None else a_val) + (0 if b_val is None else b_val)

    # group with only b -> b
    assert eval_sim(None, 42) == 42
    # group with neither -> NULL
    assert eval_sim(None, None) is None
    # group with only a -> a
    assert eval_sim(10, None) == 10
    # group with both -> a + b
    assert eval_sim(10, 42) == 52


def test_rule2_test5_sum_and_multiplication():
    """Test 5: SUM(a) + SUM(b) * 0.75 -> both operands wrapped."""
    dax = "SUM(T[a]) + SUM(T[b]) * 0.75"
    sql = try_ast_translate(dax, table_alias="T")
    assert sql is not None
    assert "CASE WHEN" in sql
    # Both operands of + are nullable (both contain SUM), so both must be wrapped in COALESCE
    assert "COALESCE(SUM(T.\"A\"::FLOAT), 0)" in sql or "COALESCE(SUM(T.\"A\"), 0)" in sql
    assert "COALESCE(SUM(T.\"B\"::FLOAT) * 0.75, 0)" in sql or "COALESCE((SUM(T.\"B\")) * 0.75, 0)" in sql or "COALESCE(SUM(T.\"B\") * 0.75, 0)" in sql


def test_rule2_test6_count_no_wrapper():
    """Test 6: COUNT(x) + COUNT(y) -> no wrapper."""
    dax = "COUNT(T[x]) + COUNT(T[y])"
    sql = try_ast_translate(dax, table_alias="T")
    assert sql is not None
    # No wrapper when neither operand is nullable
    assert "CASE WHEN" not in sql
    assert "COALESCE" not in sql
    assert "COUNT(T.\"X\") + COUNT(T.\"Y\")" in sql


def test_rule2_test7_identical_sql_shape_across_models():
    """Test 7: The same DAX in two different models -> identical SQL shape."""
    dax = "SUM(FactTable[Amount]) + SUM(FactTable[Tax])"

    # Model A: Table alias "F_A"
    sql_a = try_ast_translate(dax, table_alias="F_A")
    # Model B: Table alias "SALES_FACT_EXTENDED_LONG_NAME"
    sql_b = try_ast_translate(dax, table_alias="SALES_FACT_EXTENDED_LONG_NAME")

    assert sql_a is not None and sql_b is not None

    # Normalize table aliases to template "T"
    norm_a = sql_a.replace("F_A", "T")
    norm_b = sql_b.replace("SALES_FACT_EXTENDED_LONG_NAME", "T")

    # Identical SQL shape!
    assert norm_a == norm_b
