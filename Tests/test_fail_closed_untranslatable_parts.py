"""Tests for fail-closed partial translations.

1. Placeholder inside IF / COALESCE / arithmetic -> parent is a placeholder.
2. CALCULATE(<untranslatable measure>, ALL(col)) -> parent is a placeholder.
3. COUNTROWS(FILTER(T, [col] = ...)) with hidden columns keeps the predicate -> COUNT(CASE WHEN ... THEN 1 END).
4. TREATAS is classified as not applicable.
"""

from __future__ import annotations
import pytest

from semabridge.converter.dax_ast_parser import (
    DaxAstParser,
    DaxSqlRenderer,
    classify_dax_ast_not_applicable,
)


def test_placeholder_inside_if_coalesce_arithmetic_makes_parent_placeholder():
    """Item 1: A placeholder inside IF / COALESCE / arithmetic makes the parent a placeholder."""
    renderer = DaxSqlRenderer(
        table_alias="SALES",
        primary_table_name="Sales",
        measure_sql_map={"untranslatable_m": "CAST(NULL AS DOUBLE)"},
        known_measure_names={"untranslatable_m"},
    )
    parser = DaxAstParser()

    # 1. Inside IF condition / branch
    ast_if = parser.parse("IF([untranslatable_m] > 0, 1, 0)")
    assert renderer.render(ast_if) is None
    assert "depends on untranslatable part" in (renderer.last_render_error or "")
    with pytest.raises(Exception) as exc_if:
        renderer._render_node(ast_if)
    assert "depends on untranslatable part" in str(exc_if.value)

    # 2. Inside COALESCE
    ast_coalesce = parser.parse("COALESCE([untranslatable_m], 0)")
    assert renderer.render(ast_coalesce) is None
    assert "depends on untranslatable part" in (renderer.last_render_error or "")
    with pytest.raises(Exception) as exc_coal:
        renderer._render_node(ast_coalesce)
    assert "depends on untranslatable part" in str(exc_coal.value)

    # 3. Inside arithmetic
    ast_arith = parser.parse("[untranslatable_m] + 10")
    assert renderer.render(ast_arith) is None
    assert "depends on untranslatable part" in (renderer.last_render_error or "")
    with pytest.raises(Exception) as exc_arith:
        renderer._render_node(ast_arith)
    assert "depends on untranslatable part" in str(exc_arith.value)


def test_calculate_untranslatable_measure_with_all_makes_parent_placeholder():
    """Item 1: CALCULATE(<untranslatable measure>, ALL(col)) -> parent is a placeholder."""
    renderer = DaxSqlRenderer(
        table_alias="SALES",
        primary_table_name="Sales",
        measure_sql_map={"untranslatable_m": "CAST(NULL AS DOUBLE)"},
        known_measure_names={"untranslatable_m"},
    )
    parser = DaxAstParser()

    ast_calc = parser.parse("CALCULATE([untranslatable_m], ALL('Sales'[Region]))")
    assert renderer.render(ast_calc) is None
    assert "depends on untranslatable part" in (renderer.last_render_error or "")
    with pytest.raises(Exception) as exc:
        renderer._render_node(ast_calc)
    assert "depends on untranslatable part" in str(exc.value)


def test_countrows_filter_with_hidden_column_keeps_predicate():
    """Item 2: COUNTROWS(FILTER(T, [col] = ...)) with unqualified or hidden columns keeps predicate."""
    renderer = DaxSqlRenderer(
        table_alias="ORDERS",
        primary_table_name="Orders",
        table_aliases={"Orders": "ORDERS"},
        dataset_col_lookup={"Orders": {"status", "hidden_flag"}},
    )
    parser = DaxAstParser()

    ast = parser.parse("COUNTROWS(FILTER('Orders', [hidden_flag] = 1))")
    sql = renderer.render(ast)
    # Must keep the predicate in a CASE statement, never drop to COUNT(*)
    assert sql == 'COUNT(CASE WHEN ORDERS."HIDDEN_FLAG" = 1 THEN 1 END)'


def test_treatas_is_classified_not_applicable():
    """Item 4: TREATAS (virtual relationship) is not applicable in semantic views."""
    parser = DaxAstParser()
    ast = parser.parse(
        "CALCULATE(SUM('Sales'[Amount]), TREATAS(VALUES('Budget'[Region]), 'Sales'[Region]))"
    )
    reason = classify_dax_ast_not_applicable(ast)
    assert reason is not None
    assert "TREATAS" in reason
