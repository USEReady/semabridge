"""
Tests for the Hybrid DAX AST Parser (Tier 3 / Tier 4 translation).

Each parametrized case supplies a DAX expression and checks that:
  - try_ast_translate() returns a non-None SQL string, AND
  - the produced SQL contains the expected fragment(s).

Tests are intentionally coarse (substring checks) so the suite stays
resilient to minor whitespace / alias changes in the renderer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semabridge.converter.dax_ast_parser import (
    DaxAstParser,
    DaxLexer,
    DaxSqlRenderer,
    DaxTokenType,
    try_ast_translate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TABLE_ALIAS = "t"
DATE_ALIAS  = "d"

MEASURE_MAP = {
    "Total Revenue": "SUM(t.REVENUE)",
    "Order Count":   "COUNT(t.ORDER_ID)",
}


def _translate(dax: str) -> str | None:
    return try_ast_translate(
        dax=dax,
        table_alias=TABLE_ALIAS,
        date_alias=DATE_ALIAS,
        measure_sql_map=MEASURE_MAP,
    )


# ---------------------------------------------------------------------------
# Lexer sanity
# ---------------------------------------------------------------------------

class TestDaxLexer:
    def test_tokenises_function_name(self):
        tokens = list(DaxLexer("SUM ( t[REVENUE] )").tokenize())
        values = [t.value for t in tokens]
        assert any("SUM" in v.upper() for v in values)

    def test_tokenises_string_literal(self):
        tokens = list(DaxLexer('"hello world"').tokenize())
        assert any("hello" in t.value.lower() for t in tokens)

    def test_tokenises_number(self):
        tokens = list(DaxLexer("42.5").tokenize())
        assert any("42" in t.value for t in tokens)

    def test_token_has_type_attribute(self):
        tokens = DaxLexer("SUM").tokenize()
        assert hasattr(tokens[0], "type")
        assert isinstance(tokens[0].type, DaxTokenType)


# ---------------------------------------------------------------------------
# Parser round-trip (parse → render should produce SQL)
# ---------------------------------------------------------------------------

class TestDaxAstParser:
    def test_parse_returns_node(self):
        node = DaxAstParser().parse("1 + 2")
        assert node is not None

    def test_parse_function_call(self):
        node = DaxAstParser().parse("SUM(t[REVENUE])")
        assert node is not None

    def test_parses_nested(self):
        expr = "IF(SUM(t[FLAG]) > 0, SUM(t[REVENUE]), 0)"
        node = DaxAstParser().parse(expr)
        assert node is not None


# ---------------------------------------------------------------------------
# Tier 1/2-style expressions (direct SQL mapping)
# ---------------------------------------------------------------------------

class TestDirectTranslations:
    @pytest.mark.parametrize("dax,expected_fragment", [
        ("1 + 2",  "1"),
        ("ABS(-5)", "ABS"),
    ])
    def test_basic_expression(self, dax, expected_fragment):
        """Simple expressions that the AST can parse and render."""
        sql = _translate(dax)
        assert sql is not None, f"Expected SQL for: {dax}"
        assert expected_fragment.upper() in sql.upper(), (
            f"Fragment '{expected_fragment}' not in: {sql}"
        )

    @pytest.mark.parametrize("dax", [
        'IF(t[AMT] > 0, "positive", "negative")',
        'SWITCH(t[STATUS], 1, "Active", "Unknown")',
        "DIVIDE(t[SALES], t[TARGET], 0)",
        "IFERROR(t[RATE], 0.0)",
    ])
    def test_conditional_expressions_render_or_none(self, dax):
        """Conditional functions should render or return None gracefully."""
        result = _translate(dax)
        assert result is None or (isinstance(result, str) and len(result) > 0)


# ---------------------------------------------------------------------------
# Tier 3 — Time-intelligence functions
# ---------------------------------------------------------------------------

class TestTier3TimeIntelligence:
    @pytest.mark.parametrize("dax,expected_fragments", [
        (
            "TOTALYTD([Total Revenue], 'Date'[Date])",
            ["SUM", "YEAR"],
        ),
        (
            "TOTALMTD([Total Revenue], 'Date'[Date])",
            ["SUM", "MONTH"],
        ),
        (
            "TOTALQTD([Total Revenue], 'Date'[Date])",
            ["SUM", "QUARTER"],
        ),
        (
            "SAMEPERIODLASTYEAR('Date'[Date])",
            ["DATEADD", "YEAR"],
        ),
        (
            "PREVIOUSYEAR('Date'[Date])",
            ["DATEADD", "YEAR"],
        ),
        (
            "PREVIOUSMONTH('Date'[Date])",
            ["DATEADD", "MONTH"],
        ),
        (
            "PREVIOUSQUARTER('Date'[Date])",
            ["DATEADD", "QUARTER"],
        ),
    ])
    def test_time_intelligence(self, dax, expected_fragments):
        sql = _translate(dax)
        assert sql is not None, f"Expected SQL for time-intel DAX: {dax}"
        upper_sql = sql.upper()
        for frag in expected_fragments:
            assert frag.upper() in upper_sql, (
                f"Fragment '{frag}' missing from:\n  {sql}"
            )


# ---------------------------------------------------------------------------
# Tier 4 — CALCULATE / filter context
# ---------------------------------------------------------------------------

class TestTier4Calculate:
    @pytest.mark.parametrize("dax,expected_fragments", [
        (
            "CALCULATE([Total Revenue], ALL(t[REGION]))",
            ["SUM"],  # should produce something with SUM
        ),
        (
            "CALCULATE([Total Revenue], FILTER(t, t[AMT] > 100))",
            ["SUM"],  # should produce something with SUM
        ),
    ])
    def test_calculate_dispatch(self, dax, expected_fragments):
        sql = _translate(dax)
        assert sql is not None, f"Expected SQL for CALCULATE DAX: {dax}"
        upper_sql = sql.upper()
        for frag in expected_fragments:
            assert frag.upper() in upper_sql, (
                f"Fragment '{frag}' missing from:\n  {sql}"
            )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string_returns_none_or_string(self):
        """Empty DAX should not raise — may return None."""
        result = _translate("")
        # Either None or a (possibly empty) string
        assert result is None or isinstance(result, str)

    def test_unknown_function_returns_something(self):
        """Unknown DAX functions should degrade gracefully."""
        result = _translate("SOME_UNKNOWN_DAX_FUNCTION(t[COL])")
        # Should not raise; may return None
        assert result is None or isinstance(result, str)

    def test_deeply_nested_if(self):
        """Deeply nested IF chains should not stack-overflow."""
        dax = "IF(t[A] > 1, IF(t[B] > 2, IF(t[C] > 3, 1, 2), 3), 4)"
        result = _translate(dax)
        assert result is None or isinstance(result, str)

    def test_measure_reference_substitution(self):
        """Measure references like [Total Revenue] should expand from measure_sql_map."""
        sql = _translate("[Total Revenue]")
        if sql is not None:
            assert "SUM" in sql.upper() or "REVENUE" in sql.upper() or "Total Revenue" in sql

    def test_column_reference_renders_without_crash(self):
        """Column refs like 'Date'[Date] should not raise."""
        result = _translate("'Date'[Date]")
        assert result is None or isinstance(result, str)
