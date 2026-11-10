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
        # Dead multiplication by 0 with division
        ("SUM(Sales.Amount) * 0 / 100", "0"),
        ("(SUM(Sales.Amount)) * 0 / 100", "0"),
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
    ],
)
def test_sql_simplification_before_after(raw_sql: str, expected_sql: str):
    simplified = simplify_sql_expression(raw_sql)
    assert simplified == expected_sql, f"Expected '{expected_sql}', got '{simplified}' from '{raw_sql}'"
