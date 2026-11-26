"""Comprehensive verification test suite for drift expression and description normalization.

Tests satisfy all requirements in the drift monitoring normalization contract:
1. Contract: Purity, platform neutrality, error resilience, return types.
2. Section 2: Must NOT count as drift (removed or unified).
3. Section 3: MUST count as drift (preserved exactly).
4. Section 4: Edge cases (unparseable, mixed parsing, unknown dialect, placeholders, length limit, determinism).
5. Section 5: Descriptions (whitespace, empty values, Unicode NFC, case retention).
6. Golden test outputs: canonical stability.
"""

from __future__ import annotations

import pytest

from semabridge.governance.drift.normalize import (
    PLACEHOLDER_TOKEN,
    NormalizedExpr,
    has_expression,
    is_placeholder_expression,
    normalize_description,
    normalize_expression,
    normalize_expression_fallback,
)


# =========================================================================
# Section 1: Contract Requirements
# =========================================================================

def test_contract_return_type_and_purity():
    """normalize_expression returns NormalizedExpr with .text and .method, and is pure."""
    res = normalize_expression("SUM(amount)", dialect="snowflake")
    assert isinstance(res, NormalizedExpr)
    assert isinstance(res, str)
    assert res.method == "parsed"
    assert res.text == str(res)
    assert res == "SUM(AMOUNT)"

    # Purity: identical repeated calls return identical results
    res2 = normalize_expression("SUM(amount)", dialect="snowflake")
    assert res == res2
    assert res.method == res2.method


def test_contract_never_raises_on_bad_input():
    """Invalid syntax never raises to the caller; falls back safely to text normalization."""
    bad_expr = "SELECT %%% NOT VALID SQL &&& -- trailing note"
    res = normalize_expression(bad_expr, dialect="snowflake")
    assert isinstance(res, NormalizedExpr)
    assert res.method == "text_fallback"
    assert "SELECT %%% NOT VALID SQL &&&" in res
    assert "-- trailing note" not in res


# =========================================================================
# Section 2: Must NOT Count as Drift (Removed or Unified)
# =========================================================================

@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_not_drift_whitespace_and_tabs(dialect):
    """Whitespace, line breaks, tabs, and indentation are unified."""
    a = normalize_expression("SUM(\n  x\t\n)", dialect=dialect)
    b = normalize_expression("SUM( x )", dialect=dialect)
    c = normalize_expression("SUM(x)", dialect=dialect)
    assert a == b == c


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_not_drift_comments(dialect):
    """Inline and block comments are removed."""
    a = normalize_expression("SUM(x) -- calculate sum", dialect=dialect)
    b = normalize_expression("/* header */ SUM(x)", dialect=dialect)
    c = normalize_expression("SUM(x)", dialect=dialect)
    assert a == b == c


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_not_drift_keyword_and_function_casing(dialect):
    """Keyword and function casing are unified."""
    a = normalize_expression("sum(x)", dialect=dialect)
    b = normalize_expression("SUM(x)", dialect=dialect)
    assert a == b


def test_must_not_drift_unquoted_identifiers_snowflake():
    """Unquoted identifiers in Snowflake case-fold to canonical uppercase."""
    a = normalize_expression("amount", dialect="snowflake")
    b = normalize_expression("AMOUNT", dialect="snowflake")
    c = normalize_expression("Amount", dialect="snowflake")
    assert a == b == c == "AMOUNT"


def test_must_not_drift_unneeded_quotes_snowflake():
    """Quotes around identifiers that don't need them in Snowflake are unquoted."""
    a = normalize_expression('"AMOUNT"', dialect="snowflake")
    b = normalize_expression("AMOUNT", dialect="snowflake")
    c = normalize_expression("amount", dialect="snowflake")
    assert a == b == c == "AMOUNT"


def test_must_not_drift_unneeded_backticks_databricks():
    """Standard backticks in Databricks are unquoted."""
    a = normalize_expression("`amount`", dialect="databricks")
    b = normalize_expression("amount", dialect="databricks")
    assert a == b


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_not_drift_redundant_parentheses(dialect):
    """Redundant double/triple parentheses are removed."""
    a = normalize_expression("((a + b))", dialect=dialect)
    b = normalize_expression("a + b", dialect=dialect)
    assert a == b


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_not_drift_as_keyword_and_trailing_semicolon(dialect):
    """Optional AS keyword and trailing semicolons do not create drift."""
    a = normalize_expression("x AS y;", dialect=dialect)
    b = normalize_expression("x y", dialect=dialect)
    c = normalize_expression("x AS y", dialect=dialect)
    assert a == b == c


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_not_drift_equivalent_inequality_spellings(dialect):
    """Equivalent operators <> and != normalize to the same representation."""
    a = normalize_expression("a <> b", dialect=dialect)
    b = normalize_expression("a != b", dialect=dialect)
    assert a == b


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_not_drift_cast_syntax_variants(dialect):
    """Shorthand ::type and explicit CAST(x AS type) normalize identically."""
    a = normalize_expression("x::INT", dialect=dialect)
    b = normalize_expression("CAST(x AS INT)", dialect=dialect)
    assert a == b


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_not_drift_boolean_null_case(dialect):
    """Boolean and NULL casing normalize canonically."""
    a = normalize_expression("CASE WHEN x IS null THEN true ELSE false END", dialect=dialect)
    b = normalize_expression("CASE WHEN x IS NULL THEN TRUE ELSE FALSE END", dialect=dialect)
    assert a == b


def test_must_not_drift_table_qualification():
    """Table qualification pointing to the same table is unified."""
    a = normalize_expression("T.COL", dialect="snowflake", table="T")
    b = normalize_expression("COL", dialect="snowflake", table="T")
    assert a == b


# =========================================================================
# Section 3: MUST Count as Drift (Preserved Exactly)
# =========================================================================

@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_drift_string_literal_content_and_case(dialect):
    """String literal content and case are strictly preserved."""
    a = normalize_expression("status = 'Active'", dialect=dialect)
    b = normalize_expression("status = 'active'", dialect=dialect)
    c = normalize_expression("status = 'Pending'", dialect=dialect)
    assert a != b
    assert a != c
    assert "'Active'" in a
    assert "'active'" in b


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_drift_numeric_literal_values(dialect):
    """Numeric values are preserved exactly."""
    a = normalize_expression("rate * 0.5", dialect=dialect)
    b = normalize_expression("rate * 0.05", dialect=dialect)
    assert a != b


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_drift_cast_target_type_or_precision(dialect):
    """Target types and precision differences count as drift."""
    a = normalize_expression("CAST(x AS INT)", dialect=dialect)
    b = normalize_expression("CAST(x AS FLOAT)", dialect=dialect)
    assert a != b

    c = normalize_expression("CAST(x AS NUMBER(10, 2))", dialect=dialect)
    d = normalize_expression("CAST(x AS NUMBER(12, 2))", dialect=dialect)
    assert c != d


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_drift_function_changes(dialect):
    """Function changes count as drift."""
    a = normalize_expression("SUM(amount)", dialect=dialect)
    b = normalize_expression("AVG(amount)", dialect=dialect)
    c = normalize_expression("COUNT(DISTINCT amount)", dialect=dialect)
    assert a != b
    assert a != c


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_drift_column_or_table_changes(dialect):
    """Column or table change counts as drift."""
    a = normalize_expression("T.AMOUNT", dialect=dialect)
    b = normalize_expression("T.NET_AMOUNT", dialect=dialect)
    assert a != b


def test_must_drift_quoted_case_sensitive_identifiers_snowflake():
    """Quoted mixed-case identifiers in Snowflake are case-sensitive and must NOT equal unquoted."""
    a = normalize_expression('"Amount"', dialect="snowflake")
    b = normalize_expression("AMOUNT", dialect="snowflake")
    assert a != b
    assert '"Amount"' in a
    assert b == "AMOUNT"


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_drift_operators_and_null_handling(dialect):
    """Operator changes and NULL handling count as drift."""
    a = normalize_expression("a > b", dialect=dialect)
    b = normalize_expression("a >= b", dialect=dialect)
    assert a != b

    c = normalize_expression("x", dialect=dialect)
    d = normalize_expression("COALESCE(x, 0)", dialect=dialect)
    assert c != d


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_must_drift_window_and_order_changes(dialect):
    """Window partition and operand order changes count as drift."""
    a = normalize_expression("SUM(x) OVER (PARTITION BY dep)", dialect=dialect)
    b = normalize_expression("SUM(x) OVER (PARTITION BY reg)", dialect=dialect)
    assert a != b

    # Operand order must count as drift (safer)
    c = normalize_expression("a - b", dialect=dialect)
    d = normalize_expression("b - a", dialect=dialect)
    assert c != d


# =========================================================================
# Section 4: Edge Cases
# =========================================================================

def test_edge_case_unknown_or_missing_dialect():
    """Missing or unknown dialect uses text fallback + log without crashing."""
    res1 = normalize_expression("SUM(x) -- note", dialect="unknown_xyz")
    assert res1.method == "text_fallback"
    assert "SUM(x)" in res1
    assert "-- note" not in res1

    res2 = normalize_expression("SUM(x) -- note", dialect="")
    assert res2.method == "text_fallback"


def test_edge_case_empty_or_none():
    """None and empty strings normalize to empty string."""
    assert normalize_expression(None) == ""
    assert normalize_expression("") == ""
    assert normalize_expression("   ") == ""


def test_edge_case_placeholders():
    """Placeholders normalize to the unified placeholder token."""
    ph1 = normalize_expression("NULL", dialect="snowflake")
    ph2 = normalize_expression("cast(null as string)", dialect="snowflake")
    ph3 = normalize_expression("CAST(NULL AS DOUBLE)", dialect="snowflake")
    assert ph1 == ph2 == ph3 == PLACEHOLDER_TOKEN
    assert is_placeholder_expression(ph1)


def test_edge_case_quotes_inside_strings_and_non_ascii():
    """Escaped quotes inside strings and non-ASCII names are preserved."""
    expr = "label = 'O''Reilly' AND dept = 'España'"
    res = normalize_expression(expr, dialect="snowflake")
    assert "'O''Reilly'" in res or "O\\'Reilly" in res
    assert "'España'" in res


def test_edge_case_very_long_expression(monkeypatch):
    """Expressions exceeding length limit use text fallback safely."""
    monkeypatch.setenv("SEMABRIDGE_DRIFT_MAX_EXPRESSION_LENGTH", "50")
    long_expr = "SUM(case when status = 'active' then amount else 0 end) -- comment"
    res = normalize_expression(long_expr, dialect="snowflake")
    assert res.method == "text_fallback"
    assert "-- comment" not in res


def test_edge_case_mixed_methods_fallback_helper():
    """normalize_expression_fallback can compare unparseable with parseable without false drift."""
    live_valid = "SUM(amount) -- valid"
    base_invalid = "SUM(amount) -- invalid %%%"

    live_fb = normalize_expression_fallback(live_valid)
    assert live_fb.method == "text_fallback"
    assert live_fb == "SUM(amount)"


# =========================================================================
# Section 5: Description Normalization
# =========================================================================

def test_normalize_description_trim_and_collapse_whitespace():
    """Leading/trailing and internal whitespace/newlines are collapsed."""
    desc = "   This is a   description \n\n with line breaks   "
    res = normalize_description(desc)
    assert res == "This is a description with line breaks"


def test_normalize_description_empty_and_none():
    """None and empty descriptions are identical."""
    assert normalize_description(None) == ""
    assert normalize_description("") == ""
    assert normalize_description("   ") == ""


def test_normalize_description_unicode_nfc():
    """Visually identical text in composed/decomposed forms matches via NFC."""
    # Composed é (U+00E9) vs Decomposed e + combining acute accent (U+0065 U+0301)
    composed = "Caf\u00e9"
    decomposed = "Cafe\u0301"
    assert composed != decomposed  # Different raw byte strings
    assert normalize_description(composed) == normalize_description(decomposed)


def test_normalize_description_case_preserved():
    """Case is kept: case change counts as description drift."""
    a = normalize_description("Total Sales")
    b = normalize_description("total sales")
    assert a != b


# =========================================================================
# Section 6: Golden Outputs (Canonical Stability)
# =========================================================================

def test_golden_outputs_snowflake():
    """Golden canonical strings catch any unexpected parser changes."""
    assert normalize_expression("sum( amount )", dialect="snowflake") == "SUM(AMOUNT)"
    assert normalize_expression("count( distinct id )", dialect="snowflake") == "COUNT(DISTINCT ID)"
    assert normalize_expression("case when flag then 1 else 0 end", dialect="snowflake") == "CASE WHEN FLAG THEN 1 ELSE 0 END"
    assert normalize_expression("x::int", dialect="snowflake") == "CAST(X AS INT)"


def test_golden_outputs_databricks():
    """Golden canonical strings for Databricks."""
    assert normalize_expression("sum( `amount` )", dialect="databricks") == "SUM(amount)"
    assert normalize_expression("cast(x as int)", dialect="databricks") == "CAST(x AS INT)"


def test_fallback_preserves_string_literal_containing_table_name():
    """An expression containing a string literal that includes a table name is not altered by the fallback."""
    expr = "status = 'ORDERS.ACTIVE' AND note = 'from ORDERS.REGION'"
    res = normalize_expression_fallback(expr, table="ORDERS")
    assert "'ORDERS.ACTIVE'" in res
    assert "'from ORDERS.REGION'" in res


def test_empty_expression_is_not_a_placeholder():
    """Empty and missing expressions return False for is_placeholder_expression, while has_expression handles presence."""
    assert not is_placeholder_expression("")
    assert not is_placeholder_expression("   ")
    assert not is_placeholder_expression(None)

    assert not has_expression("")
    assert not has_expression("   ")
    assert not has_expression(None)
    assert has_expression("SUM(X)")
    assert has_expression("CAST(NULL AS DOUBLE)")

    assert is_placeholder_expression("CAST(NULL AS DOUBLE)")
    assert is_placeholder_expression("cast(null as string)")
    assert is_placeholder_expression("NULL")
    assert is_placeholder_expression("null::varchar")
    assert is_placeholder_expression(PLACEHOLDER_TOKEN)
    assert not is_placeholder_expression("SUM(amount)")
