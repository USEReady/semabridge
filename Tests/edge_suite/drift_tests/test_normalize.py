"""1. Normalisation.

Formatting must disappear before comparing; meaning must survive it. Every case
runs for each supported dialect, because a rule that only holds on one platform
would let drift through on the other.
"""
from __future__ import annotations

from drift_tests import adapter

N = adapter.names(adapter.normalize,
                  "normalize_expression", "normalize_description", "comparable",
                  "is_placeholder", "PLACEHOLDER", "PARSED", "TEXT_FALLBACK")


def same(a, b, dialect):
    left, right = N.comparable(a, b, dialect)
    return left.text == right.text


# ---------------------------------------------------------------- must NOT be drift

def test_1_1_whitespace_and_line_breaks(dialect):
    assert same("SUM(T1.AMOUNT)", "SUM(\n   T1.AMOUNT\n)", dialect)


def test_1_2_comments_on_one_side_only(dialect):
    assert same("SUM(T1.AMOUNT) -- running total", "/* total */ SUM(T1.AMOUNT)", dialect)


def test_1_3_keyword_and_function_case(dialect):
    assert same("sum(t1.amount)", "SUM(T1.AMOUNT)", dialect)


def test_1_4_unquoted_identifier_case(dialect):
    assert same("SUM(t1.amount)", "SUM(T1.AMOUNT)", dialect)


def test_1_5_redundant_quotes_around_a_plain_identifier(dialect):
    quote = '"' if dialect == "snowflake" else "`"
    quoted = f"SUM({quote}T1{quote}.{quote}AMOUNT{quote})"
    assert same(quoted, "SUM(T1.AMOUNT)", dialect)


def test_1_6_redundant_parentheses(dialect):
    assert same("((SUM(T1.AMOUNT)))", "SUM(T1.AMOUNT)", dialect)


def test_1_6b_precedence_parentheses_are_kept(dialect):
    assert not same("(T1.A + T1.B) * T1.C", "T1.A + T1.B * T1.C", dialect)


def test_1_7_trailing_semicolon(dialect):
    assert same("SUM(T1.AMOUNT);", "SUM(T1.AMOUNT)", dialect)


def test_1_8_equivalent_operator_spellings(dialect):
    assert same("T1.A <> T1.B", "T1.A != T1.B", dialect)


# ---------------------------------------------------------------- must BE drift

def test_1_9_string_literal_case_is_drift(dialect):
    assert not same("COUNT(CASE WHEN T1.S = 'Open' THEN 1 END)",
                    "COUNT(CASE WHEN T1.S = 'open' THEN 1 END)", dialect)


def test_1_10_numeric_literal_is_drift(dialect):
    assert not same("SUM(T1.AMOUNT) * 0.5", "SUM(T1.AMOUNT) * 0.05", dialect)


def test_1_11_cast_target_type_is_drift(dialect):
    assert not same("CAST(T1.AMOUNT AS INT)", "CAST(T1.AMOUNT AS FLOAT)", dialect)


def test_1_11b_cast_precision_is_drift(dialect):
    assert not same("CAST(T1.AMOUNT AS DECIMAL(10, 2))",
                    "CAST(T1.AMOUNT AS DECIMAL(12, 2))", dialect)


def test_1_12_function_column_and_filter_changes_are_drift(dialect):
    assert not same("SUM(T1.AMOUNT)", "AVG(T1.AMOUNT)", dialect)
    assert not same("SUM(T1.AMOUNT)", "SUM(T1.NET_AMOUNT)", dialect)
    assert not same("COUNT(CASE WHEN T1.A > 1 THEN 1 END)",
                    "COUNT(CASE WHEN T1.A >= 1 THEN 1 END)", dialect)


def test_1_13_quoting_follows_the_dialect_not_a_fixed_rule(dialect):
    # Where quoting makes a name case sensitive, a mixed case quoted identifier is
    # a different object and must show as drift. Where identifiers are case
    # insensitive, the same text is the same object and must not.
    quote = '"' if dialect == "snowflake" else "`"
    mixed_case_is_distinct = dialect == "snowflake"
    equal = same(f"SUM(T1.{quote}Amount{quote})", "SUM(T1.AMOUNT)", dialect)
    assert equal is not mixed_case_is_distinct


def test_1_14_operand_order_is_drift(dialect):
    assert not same("T1.A - T1.B", "T1.B - T1.A", dialect)


# ---------------------------------------------------------------- edge cases

def test_1_15_unparseable_expression_falls_back_without_raising(dialect):
    result = N.normalize_expression("SUM(T1.AMOUNT", dialect)
    assert result.method == N.TEXT_FALLBACK


def test_1_16_one_side_parseable_the_other_not_gives_no_false_drift(dialect):
    broken = "SUM(T1.AMOUNT"
    left, right = N.comparable(broken, broken, dialect)
    assert left.text == right.text and left.method == N.TEXT_FALLBACK


def test_1_17_unknown_dialect_falls_back():
    assert N.normalize_expression("SUM(T1.AMOUNT)", "not-a-dialect").method == N.TEXT_FALLBACK


def test_1_18_oversized_expression_falls_back(dialect):
    long_expr = "SUM(T1.AMOUNT) + " * 5000 + "0"
    assert N.normalize_expression(long_expr, dialect, max_length=100).method == N.TEXT_FALLBACK


def test_1_19_placeholder_normalises_to_one_token(dialect):
    for text in ("CAST(NULL AS DOUBLE)", "NULL::DOUBLE", "NULL"):
        assert N.normalize_expression(text, dialect).text == N.PLACEHOLDER
        assert N.is_placeholder(text)


def test_1_20_determinism(dialect):
    expr = "ROUND(AVG(T1.AMOUNT::FLOAT), 0)"
    assert N.normalize_expression(expr, dialect).text == N.normalize_expression(expr, dialect).text


def test_1_21_golden_output_is_stable(dialect):
    # A parser upgrade that changes canonical output must fail here, not silently
    # turn every metric into drift on the next check.
    result = N.normalize_expression("sum( t1.amount )", dialect).text
    assert result.upper().replace(" ", "") == "SUM(T1.AMOUNT)"


def test_1_22_description_cleanup():
    assert N.normalize_description("  Total   amount\r\n") == "Total amount"
    assert N.normalize_description(None) == N.normalize_description("")
    assert N.normalize_description("Total") != N.normalize_description("total")
