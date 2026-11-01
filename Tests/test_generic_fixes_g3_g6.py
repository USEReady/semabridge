import pytest
from unittest.mock import MagicMock
from semabridge.converter.dax_ast_parser import try_ast_translate, DaxAstParser, DaxSqlRenderer


def test_g3_nested_window_fails_with_non_additive_reason():
    """G3: CALCULATE over a measure that already contains a window metric cannot be windowed again; it must fail with 'nested window over a non-additive measure'."""
    measure_sql_map = {
        "WIF Same": 'CASE WHEN COALESCE(NULL, 0) >= SUM(SALES."RETURN_RATE_NUM"::FLOAT) OVER (PARTITION BY EXCLUDING STORE."TYPE") THEN 0 ELSE 1 END',
        "WIF Profit": 'SALES."WIF_SAME" * 10',
        "WIF Forecast": 'SALES."WIF_PROFIT" + 5',
    }
    # Attempting to window WIF Forecast (which transitively contains WIF Same with OVER)
    dax = "CALCULATE([WIF Forecast], ALL('Calendar'[Month]))"
    renderer = DaxSqlRenderer(table_alias="SALES", measure_sql_map=measure_sql_map, table_aliases={"Calendar": "CALENDAR"})
    node = DaxAstParser().parse(dax)
    assert renderer.render(node) is None
    with pytest.raises(Exception) as exc_info:
        renderer._render_node(node)
    assert "nested window over a non-additive measure" in str(exc_info.value)


def test_g6_window_over_rounded_ratio_preserves_outer_round():
    """G6: Window over a wrapped ratio (e.g. ROUND(... * 100, 0) / 100) preserves the outer ROUND arithmetic wrapping the ratio of windows."""
    measure_sql_map = {
        "Return Rate": 'ROUND(IFF(COALESCE(SUM(SALES."AMOUNT"::FLOAT), 0) = 0, 0, (SUM(CASE WHEN SALES.STATUS = \'Returned\' THEN SALES."AMOUNT" ELSE 0 END)) / SUM(SALES."AMOUNT"::FLOAT)) * 100, 0) / 100',
    }
    dax = "CALCULATE([Return Rate], ALL('Calendar'[Month]))"
    sql = try_ast_translate(dax, "SALES", None, measure_sql_map=measure_sql_map, table_aliases={"Calendar": "CALENDAR"})
    assert sql is not None
    assert "ROUND(" in sql
    assert "/ 100" in sql
    assert "OVER (PARTITION BY EXCLUDING" in sql
    assert 'SUM(SALES."RETURN_RATE_NUM"::FLOAT) OVER (PARTITION BY EXCLUDING CALENDAR."MONTH")' in sql
