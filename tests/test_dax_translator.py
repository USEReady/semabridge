"""
Unit tests for DAX to SQL translator.

Tests the DAXTranslator class in transform/dax_translator.py including:
- Tier 1 translations (direct aggregations)
- Tier 2 translations (CALCULATE, partial support)
- Tier 3 fallback (complex expressions)
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from semabridge.converter.dax_translator import DAXTranslator, DAXTranslationResult


class TestDAXTranslator:
    """Tests for DAX to SQL translation."""
    
    @pytest.fixture
    def translator(self):
        """Create a translator instance."""
        return DAXTranslator()
    
    # -------------------------------------------------------------------------
    # Tier 1: Direct Aggregations
    # -------------------------------------------------------------------------
    
    def test_tier1_sum(self, translator):
        """Test SUM translation."""
        result = translator.translate("SUM([Revenue])", "sales", "Sales")
        
        assert result.is_success is True
        assert result.tier == 1
        assert "SUM" in result.sql
        assert "sales" in result.sql
    
    def test_tier1_average(self, translator):
        """Test AVERAGE translation (maps to AVG)."""
        result = translator.translate("AVERAGE([Amount])", "t", "Table")
        
        assert result.is_success is True
        assert result.tier == 1
        assert "AVG" in result.sql
    
    def test_tier1_count(self, translator):
        """Test COUNT translation."""
        result = translator.translate("COUNT([OrderID])", "orders", "Orders")
        
        assert result.is_success is True
        assert result.tier == 1
        assert "COUNT" in result.sql
    
    def test_tier1_distinctcount(self, translator):
        """Test DISTINCTCOUNT translation."""
        result = translator.translate("DISTINCTCOUNT([CustomerID])", "sales", "Sales")
        
        assert result.is_success is True
        assert result.tier == 1
        assert "COUNT(DISTINCT" in result.sql
    
    def test_tier1_min(self, translator):
        """Test MIN translation."""
        result = translator.translate("MIN([Price])", "products", "Products")
        
        assert result.is_success is True
        assert "MIN" in result.sql
    
    def test_tier1_max(self, translator):
        """Test MAX translation."""
        result = translator.translate("MAX([Price])", "products", "Products")
        
        assert result.is_success is True
        assert "MAX" in result.sql
    
    def test_tier1_with_table_reference(self, translator):
        """Test aggregation with table reference in column."""
        result = translator.translate("SUM('Sales'[Revenue])", "s", "Sales")
        
        assert result.is_success is True
        assert result.tier == 1
    
    def test_tier1_case_insensitive(self, translator):
        """Test that function names are case-insensitive."""
        result1 = translator.translate("sum([X])", "t", "T")
        result2 = translator.translate("SUM([X])", "t", "T")
        result3 = translator.translate("Sum([X])", "t", "T")
        
        assert result1.is_success is True
        assert result2.is_success is True
        assert result3.is_success is True
    
    def test_tier1_with_whitespace(self, translator):
        """Test handling of extra whitespace."""
        result = translator.translate("  SUM(  [Revenue]  )  ", "s", "Sales")
        
        assert result.is_success is True
    
    # -------------------------------------------------------------------------
    # Tier 2: CALCULATE (partial support)
    # -------------------------------------------------------------------------
    
    def test_tier2_calculate_not_implemented(self, translator):
        """Test that CALCULATE falls back to Tier 4 (complex/unsupported)."""
        result = translator.translate(
            "CALCULATE(SUM([Revenue]), Product[Color] = \"Red\")",
            "sales",
            "Sales"
        )
        
        # Current implementation uses Tier 4 for complex/unsupported expressions
        assert result.tier == 4
        assert result.is_success is False
    
    # -------------------------------------------------------------------------
    # Tier 3: Complex/Unsupported
    # -------------------------------------------------------------------------
    
    def test_tier3_time_intelligence(self, translator):
        """Test that time intelligence functions return Tier 4 (currently disabled for Cortex)."""
        result = translator.translate(
            "TOTALYTD(SUM([Revenue]), 'Date'[Date])",
            "sales",
            "Sales"
        )
        
        # Time Intel is Tier 3 but currently falls back to Tier 4 due to Cortex validation
        assert result.tier == 4
        assert result.is_success is False
        assert result.sql is None
    
    def test_tier3_iterator(self, translator):
        """Test that iterator functions return Tier 4 (complex/unsupported)."""
        result = translator.translate(
            "SUMX(Sales, Sales[Quantity] * Sales[Price])",
            "sales",
            "Sales"
        )
        
        assert result.tier == 4
        assert result.is_success is False
    
    def test_tier3_nested_functions(self, translator):
        """Test that nested/complex functions return Tier 4 (complex/unsupported)."""
        result = translator.translate(
            "IF(ISBLANK([Revenue]), 0, [Revenue])",
            "sales",
            "Sales"
        )
        
        assert result.tier == 4
        assert result.is_success is False
    
    def test_tier3_variables(self, translator):
        """Test that VAR expressions return Tier 4 (complex/unsupported)."""
        result = translator.translate(
            "VAR x = SUM([Revenue]) RETURN x * 1.1",
            "sales",
            "Sales"
        )
        
        assert result.tier == 4
        assert result.is_success is False
    
    # -------------------------------------------------------------------------
    # Edge Cases
    # -------------------------------------------------------------------------
    
    def test_empty_expression(self, translator):
        """Test handling of empty expression."""
        result = translator.translate("", "t", "Table")
        
        assert result.is_success is False
        # Note: Empty expressions return tier 3 as default fallback
        assert result.tier == 3
    
    def test_whitespace_only(self, translator):
        """Test handling of whitespace-only expression."""
        result = translator.translate("   ", "t", "Table")
        
        assert result.is_success is False
    
    def test_result_preserves_original(self, translator):
        """Test that result preserves original DAX."""
        dax = "SUM([Revenue])"
        result = translator.translate(dax, "t", "Table")
        
        assert result.original_dax == dax


class TestDAXTranslationResult:
    """Tests for DAXTranslationResult class."""
    
    def test_successful_result(self):
        """Test successful translation result."""
        result = DAXTranslationResult(
            sql="SUM(t.revenue)",
            tier=1,
            original_dax="SUM([Revenue])"
        )
        
        assert result.is_success is True
        assert result.sql == "SUM(t.revenue)"
        assert result.tier == 1
    
    def test_failed_result(self):
        """Test failed translation result."""
        result = DAXTranslationResult(
            sql=None,
            tier=3,
            original_dax="TOTALYTD(...)"
        )
        
        assert result.is_success is False
        assert result.sql is None
        assert result.tier == 3
