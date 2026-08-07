"""Unit tests for semabridge.utils.null_sentinel.is_null_cast_sql -- the
shared shape-detector used at every point that decides whether a
translated SQL expression is real or the LLM/DDL-remediation "I couldn't
translate this" placeholder.
"""
from __future__ import annotations

import pytest

from semabridge.utils.null_sentinel import is_null_cast_sql


@pytest.mark.parametrize(
    "expr",
    [
        "CAST(NULL AS DOUBLE)",
        "cast(null as double)",
        "  CAST( NULL AS DOUBLE )  ",
        "CAST(NULL AS DECIMAL(38,10))",
        "CAST(NULL AS VARCHAR)",
        "CAST(NULL AS DOUBLE) WITH SYNONYMS = ('Foo','Bar')",
        "CAST(NULL AS DOUBLE) WITH SYNONYMS=('Foo')",
    ],
)
def test_recognizes_null_cast_placeholder_shapes(expr):
    assert is_null_cast_sql(expr) is True


@pytest.mark.parametrize(
    "expr",
    [
        None,
        "",
        "SUM(SOMETABLE.SOMECOLUMN)",
        'CASE WHEN SOMETABLE."X" IS NULL THEN 0 ELSE SOMETABLE."X" END',
        'NULLIF(SOMETABLE."X", 0)',
        "CAST(SOMETABLE.X AS DOUBLE)",  # casting a real column, not NULL
        'COALESCE(CAST(NULL AS DOUBLE), SOMETABLE."X")',  # embedded, not the whole expr
        "SUM(CASE WHEN SOMETABLE.Y IS NULL THEN 0 ELSE SOMETABLE.Y END)",
    ],
)
def test_does_not_falsely_match_real_sql(expr):
    assert is_null_cast_sql(expr) is False
