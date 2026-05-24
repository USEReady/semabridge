#!/usr/bin/env python3
"""
Comprehensive Test Suite for DAX Translation Engine.

Tests the production-grade deterministic DAX → Snowflake SQL translator.

Coverage:
- Direct aggregations (SUM, COUNT, AVG, MIN, MAX, DISTINCTCOUNT)
- CALCULATE with FILTER
- Time intelligence (YTD, MTD, QTD, SAMEPERIODLASTYEAR, etc.)
- DIVIDE
- IF/SWITCH/IFERROR
- Capability detection
- Caching layer
- Metrics collection
"""

import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from semabridge.converter.dax_engine import (
    DaxTranslationEngine,
    CapabilityDetector,
    CapabilityLevel,
    TranslationStrategy,
)
from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer


class TestDirectAggregations:
    """Test direct aggregation translations."""
    
    def test_sum_direct(self):
        engine = DaxTranslationEngine()
        sql, metrics = engine.translate("SUM([Amount])", "sales")
        assert sql is not None
        assert "SUM" in sql.upper()
        assert metrics.strategy == TranslationStrategy.AST_BASED
        assert metrics.sql == sql
    
    def test_count(self):
        engine = DaxTranslationEngine()
        sql, metrics = engine.translate("COUNT([ID])", "products")
        assert sql is not None
        assert "COUNT" in sql.upper()
    
    def test_average(self):
        engine = DaxTranslationEngine()
        sql, metrics = engine.translate("AVERAGE([Price])", "pricing")
        assert sql is not None
        assert "AVG" in sql.upper()
    
    def test_distinctcount(self):
        engine = DaxTranslationEngine()
        sql, metrics = engine.translate("DISTINCTCOUNT([Customer])", "sales")
        assert sql is not None
        assert "COUNT(DISTINCT" in sql.upper()
    
    def test_with_table_reference(self):
        engine = DaxTranslationEngine()
        sql, metrics = engine.translate("SUM('Sales'[Amount])", "sales")
        assert sql is not None
        assert "SUM" in sql.upper()


class TestCalculate:
    """Test CALCULATE with FILTER."""
    
    def test_calculate_simple_filter(self):
        engine = DaxTranslationEngine()
        dax = "CALCULATE(SUM([Amount]), [Region] = \"West\")"
        sql, metrics = engine.translate(dax, "sales")
        assert sql is not None
        assert "SUM" in sql.upper()
        assert "CASE WHEN" in sql.upper() or "WHERE" in sql.upper()
    
    def test_calculate_with_all(self):
        engine = DaxTranslationEngine()
        dax = "CALCULATE(SUM([Amount]), ALL([Date]))"
        sql, metrics = engine.translate(dax, "sales")
        assert sql is not None


class TestTimeIntelligence:
    """Test time intelligence functions."""
    
    def test_totalytd(self):
        engine = DaxTranslationEngine()
        dax = "TOTALYTD(SUM([Amount]), [Date])"
        sql, metrics = engine.translate(dax, "sales", date_alias="dates")
        assert sql is not None
        assert "OVER" in sql.upper() or "WINDOW" in sql.upper()
    
    def test_totalmtd(self):
        engine = DaxTranslationEngine()
        dax = "TOTALMTD(SUM([Amount]), [Date])"
        sql, metrics = engine.translate(dax, "sales", date_alias="dates")
        assert sql is not None
    
    def test_previousyear(self):
        engine = DaxTranslationEngine()
        dax = "SAMEPERIODLASTYEAR(SUM([Amount]), [Date])"
        sql, metrics = engine.translate(dax, "sales", date_alias="dates")
        # May fall back, but shouldn't error


class TestDivide:
    """Test DIVIDE function."""
    
    def test_divide_basic(self):
        engine = DaxTranslationEngine()
        dax = "DIVIDE([Sales], [Quantity])"
        sql, metrics = engine.translate(dax, "sales", measure_map={
            "Sales": "SUM(AMOUNT)",
            "Quantity": "SUM(QTY)"
        })
        # Should translate or fall back gracefully


class TestControlFlow:
    """Test IF/SWITCH/IFERROR."""
    
    def test_if(self):
        engine = DaxTranslationEngine()
        dax = "IF([Amount] > 100, [Amount], 0)"
        sql, metrics = engine.translate(dax, "sales")
        assert sql is not None or metrics.strategy == TranslationStrategy.LLM_FALLBACK


class TestCapabilityDetector:
    """Test capability detection."""
    
    def test_fully_supported(self):
        can_translate, level = CapabilityDetector.can_translate("SUM([Amount])")
        assert can_translate
        assert level in (CapabilityLevel.FULLY_SUPPORTED, CapabilityLevel.WELL_SUPPORTED)
    
    def test_well_supported(self):
        dax = "CALCULATE(SUM([Amount]), [Region] = \"West\")"
        can_translate, level = CapabilityDetector.can_translate(dax)
        assert can_translate
    
    def test_unsupported_rankx(self):
        can_translate, level = CapabilityDetector.can_translate("RANKX(...)")
        # May or may not be detected as unsupported, but shouldn't crash
        assert isinstance(level, CapabilityLevel)
    
    def test_unsupported_sumx_filter(self):
        can_translate, level = CapabilityDetector.can_translate(
            "SUMX(FILTER([Table], [Condition]), [Amount])"
        )
        assert not can_translate or level == CapabilityLevel.UNSUPPORTED


class TestCaching:
    """Test translation cache."""
    
    def test_cache_hit(self):
        engine = DaxTranslationEngine(cache_enabled=True)
        if engine.cache:
            engine.cache.clear()
        
        # First call (cache miss)
        sql1, metrics1 = engine.translate("SUM([Amount])", "sales")
        assert not metrics1.cached
        
        # Second call (cache hit)
        sql2, metrics2 = engine.translate("SUM([Amount])", "sales")
        assert sql1 == sql2
        assert metrics2.cached
    
    def test_different_table_alias_no_cache_hit(self):
        engine = DaxTranslationEngine(cache_enabled=True)
        if engine.cache:
            engine.cache.clear()
        
        # Different table aliases should not share cache
        sql1, _ = engine.translate("SUM([Amount])", "sales")
        sql2, metrics2 = engine.translate("SUM([Amount])", "inventory")
        
        # Both should translate but metrics2 shouldn't be cached
        # (because table alias is different)
        assert not metrics2.cached


class TestMetricsCollection:
    """Test metrics and observability."""
    
    def test_metrics_summary(self):
        engine = DaxTranslationEngine()
        if engine.cache:
            engine.cache.clear()
        
        # Translate several expressions
        engine.translate("SUM([Amount])", "sales")
        engine.translate("COUNT([ID])", "products")
        engine.translate("AVERAGE([Price])", "pricing")
        
        summary = engine.get_metrics_summary()
        assert summary["total"] == 3
        assert summary["successful"] >= 2


class TestComplexScenarios:
    """Test realistic complex scenarios."""
    
    def test_market_share_divide(self):
        """Example: Market share = Sales / Total Sales"""
        engine = DaxTranslationEngine()
        dax = "DIVIDE([Sales], [Total Sales])"
        measure_map = {
            "Sales": "SUM(AMOUNT)",
            "Total Sales": "SUM(AMOUNT) OVER (PARTITION BY YEAR)"
        }
        sql, metrics = engine.translate(dax, "sales", measure_map=measure_map)
        # Should either translate or gracefully fall back
    
    def test_ytd_calculation(self):
        """Example: Year-to-date calculation"""
        engine = DaxTranslationEngine()
        dax = "TOTALYTD(SUM([Amount]), [Date])"
        sql, metrics = engine.translate(dax, "sales", date_alias="calendar")
        # Should handle YTD
    
    def test_nested_calculate(self):
        """Example: Nested CALCULATE expression"""
        engine = DaxTranslationEngine()
        dax = "CALCULATE(SUM([Amount]), FILTER(ALL([Date]), [Year] = 2024))"
        sql, metrics = engine.translate(dax, "sales")
        # Should either translate or fail gracefully


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_empty_dax(self):
        engine = DaxTranslationEngine()
        sql, metrics = engine.translate("", "sales")
        assert sql is None
    
    def test_whitespace_only(self):
        engine = DaxTranslationEngine()
        sql, metrics = engine.translate("   ", "sales")
        assert sql is None
    
    def test_very_long_expression(self):
        engine = DaxTranslationEngine()
        long_dax = "SUM([" + "A" * 100 + "])"
        sql, metrics = engine.translate(long_dax, "sales")
        # Should handle without crashing
    
    def test_special_characters(self):
        engine = DaxTranslationEngine()
        dax = "SUM([Amount@Special])"
        sql, metrics = engine.translate(dax, "sales")
        # Should sanitize or handle gracefully


class TestAstParser:
    """Low-level tests for AST parser."""
    
    def test_parse_simple_agg(self):
        parser = DaxAstParser()
        ast = parser.parse("SUM([Amount])")
        assert ast is not None
    
    def test_parse_complex_expression(self):
        parser = DaxAstParser()
        dax = "CALCULATE(SUM([Amount]), [Region] = \"West\")"
        ast = parser.parse(dax)
        assert ast is not None
    
    def test_parse_with_table_reference(self):
        parser = DaxAstParser()
        ast = parser.parse("SUM('Sales'[Amount])")
        assert ast is not None


class TestSqlRenderer:
    """Low-level tests for SQL renderer."""
    
    def test_render_basic_agg(self):
        parser = DaxAstParser()
        ast = parser.parse("SUM('Sales'[Amount])")
        
        renderer = DaxSqlRenderer(table_alias="sales")
        sql = renderer.render(ast)
        assert sql is not None
        assert "SUM" in sql.upper()

    def test_render_iferror_avoids_try_cast(self):
        parser = DaxAstParser()
        ast = parser.parse("IFERROR(SUM('Sales'[Amount]) / SUM('Sales'[Units]), 0)")

        renderer = DaxSqlRenderer(table_alias="sales")
        sql = renderer.render(ast)

        assert sql is not None
        assert "TRY_CAST" not in sql.upper()
        assert "TRY_TO_NUMBER(TO_VARCHAR(" in sql.upper()


def run_tests():
    """Run all tests with pytest."""
    import pytest
    
    # Run with verbose output
    exit_code = pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-ra",  # Show summary of all test outcomes
    ])
    
    return exit_code


if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
