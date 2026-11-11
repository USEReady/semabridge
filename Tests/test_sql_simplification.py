"""Unit tests for SQL metric expression simplification.

Exercises Item 2:
- Remove dead arithmetic (x * 0 / 100, x + 0, 0 + x, x - 0, x * 1, etc.)
- Remove redundant NULL guards (CASE WHEN ... 0 IS NULL, nested COALESCE/NULLIF, redundant IFF)
without changing mathematical results.
"""
from __future__ import annotations

import pytest
from semabridge.utils.sql_simplifier import simplify_sql_expression


@pytest.mark.parametrize(
    "raw_sql, expected_sql",
    [
        # Dead multiplication by 0 with division preserving NULL semantics
        ("SUM(Sales.Amount) * 0 / 100", "CASE WHEN SUM(Sales.Amount) IS NULL THEN NULL ELSE 0 END"),
        ("(SUM(Sales.Amount)) * 0 / 100", "CASE WHEN (SUM(Sales.Amount)) IS NULL THEN NULL ELSE 0 END"),
        # Dead addition / subtraction of 0
        ("SUM(Sales.Amount) + 0", "SUM(Sales.Amount)"),
        ("0 + SUM(Sales.Amount)", "SUM(Sales.Amount)"),
        ("SUM(Sales.Amount) - 0", "SUM(Sales.Amount)"),
        # What-if parameter default 0 simplification: x * (1 + (0) / 100) -> x
        ("SUM(Sales.Amount) * (1 + (0) / 100)", "SUM(Sales.Amount)"),
        ("SUM(orders.QTY) * (1 + (0) / 100)", "SUM(orders.QTY)"),
        # Redundant NULL guards generated for literal 0 operands
        (
            "CASE WHEN (SUM(Sales.Amount)) IS NULL AND (0) IS NULL THEN NULL ELSE COALESCE(SUM(Sales.Amount), 0) + 0 END",
            "COALESCE(SUM(Sales.Amount), 0)",
        ),
        (
            "CASE WHEN SUM(Sales.Amount) IS NULL THEN NULL ELSE SUM(Sales.Amount) END",
            "SUM(Sales.Amount)",
        ),
        (
            "IFF(SUM(Sales.Amount) IS NULL, NULL, SUM(Sales.Amount))",
            "SUM(Sales.Amount)",
        ),
        # Redundant nested COALESCE and NULLIF
        ("COALESCE(COALESCE(SUM(Sales.Amount), 0), 0)", "COALESCE(SUM(Sales.Amount), 0)"),
        ("COALESCE(NULL, 0)", "0"),
        ("NULLIF(NULLIF(ACTIVES, 0), 0)", "NULLIF(ACTIVES, 0)"),
        # Identity multiplication / division by 1
        ("SUM(Sales.Amount) * 1", "SUM(Sales.Amount)"),
        ("1 * SUM(Sales.Amount)", "SUM(Sales.Amount)"),
        ("SUM(Sales.Amount) / 1", "SUM(Sales.Amount)"),
        # NULL and empty string handling
        (None, None),
        ("", ""),
        ("   ", ""),
        ("NULL", "NULL"),
        ("CAST(NULL AS DOUBLE)", "CAST(NULL AS DOUBLE)"),
    ],
)
def test_sql_simplification_before_after(raw_sql: str | None, expected_sql: str | None):
    simplified = simplify_sql_expression(raw_sql)
    assert simplified == expected_sql, f"Expected '{expected_sql}', got '{simplified}' from '{raw_sql}'"


def test_null_semantics_with_sqlite():
    """Point 7: NULL test: x * 0 / 100 with x NULL keeps NULL semantics."""
    import sqlite3

    con = sqlite3.connect(":memory:")
    cur = con.cursor()
    cur.execute("CREATE TABLE test (x DOUBLE)")
    cur.execute("INSERT INTO test VALUES (NULL), (42.0)")

    # When x is NULL, raw evaluation yields NULL
    cur.execute("SELECT x * 0 / 100 FROM test WHERE x IS NULL")
    assert cur.fetchone()[0] is None

    # Simplified expression must preserve NULL when x is NULL
    simplified = simplify_sql_expression("x * 0 / 100")
    cur.execute(f"SELECT {simplified} FROM test WHERE x IS NULL")
    assert cur.fetchone()[0] is None

    # When x is non-NULL (42.0), both raw and simplified return 0
    cur.execute("SELECT x * 0 / 100 FROM test WHERE x IS NOT NULL")
    assert cur.fetchone()[0] == 0.0
    cur.execute(f"SELECT {simplified} FROM test WHERE x IS NOT NULL")
    assert cur.fetchone()[0] == 0.0



