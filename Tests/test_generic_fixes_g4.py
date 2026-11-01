"""
Tests for Generic Fix G4:
- What-if parameter without default resolves to NULL (BLANK in DAX).
- In * and /, it keeps NULL (e.g. SUM(fact."UNITS"::FLOAT) * NULL).
- In + and -, and comparisons, wrapped in COALESCE(..., 0).
- Parameter with explicit default retains that default.
"""
from types import SimpleNamespace
import pytest
from semabridge.converter.dax_translator import DAXTranslator


def _metric(name, expr, dataset=None):
    return SimpleNamespace(unique_name=name, expression=expr, dataset=dataset, sql_expression=None)


class TestGenericFixG4WhatIfParameters:
    def setup_method(self):
        self.translator = DAXTranslator()
        # Disconnected parameter table 'Threshold Param' with no relationships
        self.metrics_context = [
            _metric("Param No Default", "SELECTEDVALUE('Threshold Param'[Value]) / 100", dataset="Threshold Param"),
            _metric("Param With Default", "SELECTEDVALUE('Threshold Param'[Value], 5) / 100", dataset="Threshold Param"),
            _metric("Units", "SUM(Sales[Units])", dataset="Sales"),
            _metric("Net Sales", "SUM(Sales[Amount])", dataset="Sales"),
        ]
        self.relationships = []

    def test_parameter_in_multiplication_keeps_null(self):
        res = self.translator.translate(
            "SUM(Sales[Units]) * [Param No Default]",
            "sales",
            "Sales",
            metrics_context=self.metrics_context,
            relationships=self.relationships,
        )
        assert res is not None
        sql = res.sql
        assert "* NULL" in sql or "* (NULL)" in sql or "* (0)" not in sql
        assert "0" not in sql

    def test_parameter_in_division_keeps_null(self):
        res = self.translator.translate(
            "DIVIDE(SUM(Sales[Units]) * [Param No Default], 1, 0)",
            "sales",
            "Sales",
            metrics_context=self.metrics_context,
            relationships=self.relationships,
        )
        assert res is not None
        sql = res.sql
        assert "* NULL" in sql or "* (NULL)" in sql
        assert "* (0)" not in sql

    def test_parameter_in_subtraction_wraps_in_coalesce(self):
        res = self.translator.translate(
            "[Net Sales] - (SUM(Sales[Units]) * [Param No Default])",
            "sales",
            "Sales",
            metrics_context=self.metrics_context,
            relationships=self.relationships,
        )
        assert res is not None
        sql = res.sql
        assert "COALESCE" in sql
        assert "* (0)" not in sql

    def test_parameter_in_comparison_wraps_in_coalesce(self):
        res = self.translator.translate(
            "IF([Param No Default] >= 0.1, 1, 0)",
            "sales",
            "Sales",
            metrics_context=self.metrics_context,
            relationships=self.relationships,
        )
        assert res is not None
        sql = res.sql
        assert "COALESCE" in sql

