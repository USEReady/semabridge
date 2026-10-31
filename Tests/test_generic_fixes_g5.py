"""
Tests for Generic Fix G5:
- No raw DAX left in SQL (DIVIDE( / [..]))
- No zero guard on a constant non-zero denominator:
  - no IFF(COALESCE(<number>, 0) = 0 ...)
  - no / NULLIF(<number>, 0)
- Preserves zero guards on dynamic denominators.
"""
from types import SimpleNamespace
import pytest
from semabridge.converter.dax_translator import DAXTranslator


def _metric(name, sql):
    return SimpleNamespace(unique_name=name, sql_expression=sql)


class TestGenericFixG5Division:
    def setup_method(self):
        self.translator = DAXTranslator()
        self.metrics_context = [
            _metric("Measure_A", 'SUM(fact."COL_A")'),
            _metric("Measure_B", 'SUM(fact."COL_B")'),
        ]

    def test_divide_constant_denominator_two_args(self):
        res = self.translator.translate(
            "DIVIDE([Measure_A], 100)",
            "fact",
            "SomeTable",
            metrics_context=self.metrics_context,
        )
        assert res is not None
        sql = res.sql
        assert "NULLIF" not in sql
        assert "IFF" not in sql
        assert "100" in sql
        assert "DIVIDE" not in sql

    def test_divide_constant_denominator_three_args(self):
        res = self.translator.translate(
            "DIVIDE([Measure_A], 100, 0)",
            "fact",
            "SomeTable",
            metrics_context=self.metrics_context,
        )
        assert res is not None
        sql = res.sql
        assert "NULLIF" not in sql
        assert "IFF" not in sql
        assert "100" in sql
        assert "DIVIDE" not in sql

    def test_binary_slash_constant_denominator(self):
        res = self.translator.translate(
            "[Measure_A] / 100",
            "fact",
            "SomeTable",
            metrics_context=self.metrics_context,
        )
        assert res is not None
        sql = res.sql
        assert "NULLIF" not in sql
        assert "100" in sql

    def test_divide_dynamic_denominator_two_args(self):
        res = self.translator.translate(
            "DIVIDE([Measure_A], [Measure_B])",
            "fact",
            "SomeTable",
            metrics_context=self.metrics_context,
        )
        assert res is not None
        sql = res.sql
        assert "NULLIF" in sql

    def test_divide_dynamic_denominator_three_args(self):
        res = self.translator.translate(
            "DIVIDE([Measure_A], [Measure_B], 0)",
            "fact",
            "SomeTable",
            metrics_context=self.metrics_context,
        )
        assert res is not None
        sql = res.sql
        assert "IFF" in sql
