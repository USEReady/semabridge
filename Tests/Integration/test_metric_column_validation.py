#!/usr/bin/env python3
"""
Tests for metric column reference validation in SnowflakeEmitter.

This test suite verifies that:
1. Invalid column references are caught and skipped
2. Valid column references pass validation
3. Error messages are clear and helpful
4. Column name case sensitivity is handled correctly
5. Cross-table references are properly validated
"""

import sys
import pytest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior
from unittest.mock import MagicMock


class TestMetricColumnValidation:
    """Test metric SQL column reference validation."""
    
    @pytest.fixture
    def emitter(self):
        """Create a SnowflakeEmitter instance for testing."""
        config = SnowflakeConfig(
            account="test.local",
            user="test_user",
            password="test_password",  # noqa: S106
            warehouse="test_wh",
            database="test_db",
            schema_name="test_schema",
            role="test_role"
        )
        behavior = ConnectorBehavior()
        return SnowflakeEmitter(config, behavior)
    
    def test_valid_single_table_column_reference(self, emitter):
        """Test that simple single-table column references pass validation."""
        metric_sql = 'SUM(sf."REVENUE")'
        metric_name = "total_revenue"
        dataset_col_lookup = {
            "salesfact": {"REVENUE", "UNITS", "DATE_ID"},
        }
        dataset_aliases = {"salesfact": "sf"}
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        assert is_valid is True
        assert error is None
    
    def test_invalid_column_not_in_dataset(self, emitter):
        """Test that references to non-existent columns are caught."""
        metric_sql = 'SUM(sf."INVALID_COL")'
        metric_name = "bad_metric"
        dataset_col_lookup = {
            "salesfact": {"REVENUE", "UNITS", "DATE_ID"},
        }
        dataset_aliases = {"salesfact": "sf"}
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        assert is_valid is False
        assert error is not None
        assert "INVALID_COL" in error
        assert "not found" in error.lower()
    
    def test_valid_cross_table_reference(self, emitter):
        """Test that valid cross-table references pass validation."""
        metric_sql = 'SUM(sf."REVENUE") / NULLIF(COUNT(DISTINCT d."DATE_ID"), 0)'
        metric_name = "revenue_per_date"
        dataset_col_lookup = {
            "salesfact": {"REVENUE", "UNITS", "DATE_ID"},
            "date": {"DATE_ID", "YEAR", "MONTH"},
        }
        dataset_aliases = {
            "salesfact": "sf",
            "date": "d",
        }
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        assert is_valid is True
        assert error is None
    
    def test_invalid_cross_table_wrong_column_in_target_table(self, emitter):
        """Test that column mismatch across tables is caught."""
        metric_sql = 'SUM(sf."REVENUE") / NULLIF(COUNT(DISTINCT d."REVENUE"), 0)'
        metric_name = "bad_cross_table"
        dataset_col_lookup = {
            "salesfact": {"REVENUE", "UNITS", "DATE_ID"},
            "date": {"DATE_ID", "YEAR", "MONTH"},
        }
        dataset_aliases = {
            "salesfact": "sf",
            "date": "d",
        }
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        assert is_valid is False
        assert error is not None
        assert "REVENUE" in error
        assert "not found" in error.lower()
    
    def test_unknown_alias_in_reference(self, emitter):
        """Test that unknown aliases are caught."""
        metric_sql = 'SUM(unknown."REVENUE")'
        metric_name = "unknown_alias_metric"
        dataset_col_lookup = {
            "salesfact": {"REVENUE", "UNITS", "DATE_ID"},
        }
        dataset_aliases = {"salesfact": "sf"}
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        assert is_valid is False
        assert error is not None
        assert "unknown" in error.lower()
    
    def test_no_table_reference_simple_aggregation(self, emitter):
        """Test that simple aggregations without table refs pass."""
        metric_sql = 'SUM(some_column)'  # No TABLE.COLUMN reference
        metric_name = "simple"
        dataset_col_lookup = {
            "salesfact": {"REVENUE", "UNITS", "DATE_ID"},
        }
        dataset_aliases = {"salesfact": "sf"}
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        # Should pass because there are no TABLE.COLUMN refs to validate
        assert is_valid is True
        assert error is None
    
    def test_multiple_valid_references(self, emitter):
        """Test multiple valid column references in one metric."""
        metric_sql = 'AVG(sf."PRICE" * sf."QUANTITY") / NULLIF(COUNT(sf."UNITS"), 0)'
        metric_name = "complex_metric"
        dataset_col_lookup = {
            "salesfact": {"REVENUE", "PRICE", "QUANTITY", "UNITS", "DATE_ID"},
        }
        dataset_aliases = {"salesfact": "sf"}
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        assert is_valid is True
        assert error is None

    def test_repair_invalid_bare_table_sum_identifier(self, emitter):
        """Repair SUM("TABLE") into SUM(alias.PREFERRED_NUMERIC_COLUMN)."""
        metric_sql = 'DIV0(SUM("SPEND_FACT"), SUM("SPEND_FACT") OVER ())'
        dataset_col_lookup = {
            "SPEND_FACT": {
                "TRANSACTION_USD_AMOUNT",
                "SPEND_TYPE",
                "SPEND_FACT_CK",
                "GL_ACCOUNT_CK",
            },
        }
        dataset_aliases = {
            "SPEND_FACT": "SPEND_FACT",
        }

        repaired = emitter._normalize_metric_column_references(
            metric_sql,
            "SPEND_OF_TOTAL",
            dataset_col_lookup,
            dataset_aliases,
            metric_names={"SPEND_OF_TOTAL", "TOTAL_SPEND"},
        )

        assert 'SUM(SPEND_FACT.TRANSACTION_USD_AMOUNT)' in repaired
        assert 'SUM("SPEND_FACT")' not in repaired

    def test_repair_preserves_real_metric_aggregate_wrappers(self, emitter):
        """Do not rewrite valid semantic metric wrappers while repairing table-name wrappers."""
        metric_sql = 'SUM("TOTAL_SPEND") + SUM("SPEND_FACT")'
        dataset_col_lookup = {
            "SPEND_FACT": {"TRANSACTION_USD_AMOUNT"},
        }
        dataset_aliases = {
            "SPEND_FACT": "SPEND_FACT",
        }

        repaired = emitter._normalize_metric_column_references(
            metric_sql,
            "SPEND_OF_TOTAL",
            dataset_col_lookup,
            dataset_aliases,
            metric_names={"TOTAL_SPEND", "SPEND_OF_TOTAL"},
        )

        # Existing metric wrapper is handled by wrapper rewrite to direct metric ref,
        # while the invalid table-name wrapper is repaired to a physical column.
        assert '"TOTAL_SPEND"' in repaired
        assert 'SUM(SPEND_FACT.TRANSACTION_USD_AMOUNT)' in repaired

    def test_partition_identifier_prefers_metric_entity_alias(self, emitter):
        """Qualify partition key within metric entity when resolvable there."""
        metric_sql = (
            'DIV0(SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT"), '
            'SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT") OVER (PARTITION BY "FUNCTION"))'
        )
        dataset_col_lookup = {
            "SPEND_FACT": {"TRANSACTION_USD_AMOUNT", "SPEND_TYPE", "COST_CENTER"},
            "COST_CENTER_HIERARCHY": {"FUNCTION", "GL_COST_CENTER_CK"},
        }
        dataset_aliases = {
            "SPEND_FACT": "SPEND_FACT",
            "COST_CENTER_HIERARCHY": "COST_CENTER_HIERARCHY",
        }

        repaired = emitter._normalize_metric_column_references(
            metric_sql,
            "SPEND_WITHIN_FUNCTION",
            dataset_col_lookup,
            dataset_aliases,
            metric_names={"SPEND_WITHIN_FUNCTION", "SPEND_OF_TOTAL", "TOTAL_SPEND"},
            preferred_table_alias="SPEND_FACT",
        )

        assert repaired == 'SUM(SPEND_FACT.TRANSACTION_USD_AMOUNT)'

    def test_window_metric_expression_rewritten_for_semantic_metrics(self, emitter):
        """Rewrite DIV0(SUM(x), SUM(x) OVER(...)) to SUM(x) for semantic safety."""
        raw_expr = (
            'DIV0(SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT"), '
            'SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT") OVER (PARTITION BY SPEND_FACT."COST_CENTER"))'
        )

        rewritten = emitter._rewrite_window_metric_expression(raw_expr)
        assert rewritten == 'SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT")'

    def test_lag_window_metric_expression_downgraded_to_null(self, emitter):
        """Unsupported LAG/OVER metrics should emit NULL to keep deployment valid."""
        raw_expr = (
            'LAG(SUM(FACT.REVENUE), 12) OVER '
            '(PARTITION BY FACT."PRODUCT_KEY" ORDER BY CALENDAR.YEARPERIOD)'
        )

        rewritten = emitter._rewrite_window_metric_expression(raw_expr)
        assert rewritten == "NULL"

    def test_dedupe_malformed_qualified_partition_reference(self, emitter):
        """Deduplicate malformed alias."COL".COL chains."""
        metric_sql = (
            'LAG(SUM(FACT.REVENUE), 12) OVER '
            '(PARTITION BY FACT."PRODUCT_KEY".PRODUCT_KEY ORDER BY CALENDAR.YEARPERIOD)'
        )

        repaired = emitter._dedupe_qualified_column_tokens(metric_sql)

        assert 'FACT."PRODUCT_KEY".PRODUCT_KEY' not in repaired
        assert 'PARTITION BY FACT."PRODUCT_KEY"' in repaired

    def test_known_finance_metric_fallback_expressions(self, emitter):
        """Known finance metrics should receive deterministic SQL fallback."""
        total_cogs = emitter._build_known_metric_fallback_expression(
            metric_name="TOTAL_COGS",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
        )
        gross_margin = emitter._build_known_metric_fallback_expression(
            metric_name="GROSS_MARGIN",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
        )
        gm = emitter._build_known_metric_fallback_expression(
            metric_name="GM",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
        )
        revenuety = emitter._build_known_metric_fallback_expression(
            metric_name="REVENUETY",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
        )
        rev_var = emitter._build_known_metric_fallback_expression(
            metric_name="REVENUE_VAR_TO_BUDGET_1",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
        )

        assert "SUM(FACT.MATERIAL_COSTS)" in total_cogs
        assert "SUM(FACT.REVENUE)" in gross_margin
        assert "CASE WHEN SUM(FACT.REVENUE) = 0" in gm
        assert "SCENARIO.SCENARIO = 'Actual'" in revenuety
        assert "SCENARIO.SCENARIO = 'Budget'" in rev_var

    def test_metric_emission_alias_rehomed_to_expression_entity(self, emitter):
        """Emit metrics under referenced entity when default alias is unrelated."""
        dataset_aliases = {
            "MEASURES": "MEASURES",
            "SPEND_FACT": "SPEND_FACT",
        }
        expr = 'SUM(SPEND_FACT.TRANSACTION_USD_AMOUNT)'

        resolved = emitter._resolve_metric_emission_alias(
            "MEASURES",
            expr,
            dataset_aliases,
        )

        assert resolved == "SPEND_FACT"
    
    def test_mixed_valid_invalid_references(self, emitter):
        """Test that one invalid ref causes entire validation to fail."""
        metric_sql = 'SUM(sf."REVENUE") + COUNT(sf."NONEXISTENT")'
        metric_name = "mixed_metric"
        dataset_col_lookup = {
            "salesfact": {"REVENUE", "UNITS", "DATE_ID"},
        }
        dataset_aliases = {"salesfact": "sf"}
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        assert is_valid is False
        assert error is not None
        assert "NONEXISTENT" in error
    
    
    def test_empty_column_lookup(self, emitter):
        """Test handling of empty column lookup (no columns known)."""
        metric_sql = 'SUM(sf."REVENUE")'
        metric_name = "no_columns_metric"
        dataset_col_lookup = {
            "salesfact": set(),  # Empty set
        }
        dataset_aliases = {"salesfact": "sf"}
        
        is_valid, error = emitter._validate_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        assert is_valid is False
        assert error is not None


class TestBuildSchemaValidationMap:
    """Test schema map building from SML model."""
    
    @pytest.fixture
    def emitter(self):
        """Create a SnowflakeEmitter instance for testing."""
        config = SnowflakeConfig(
            account="test.local",
            user="test_user",
            password="test_password",  # noqa: S106
            warehouse="test_wh",
            database="test_db",
            schema_name="test_schema",
            role="test_role"
        )
        behavior = ConnectorBehavior()
        return SnowflakeEmitter(config, behavior)
    
    def test_build_schema_map_basic(self, emitter):
        """Test basic schema map building."""
        # Create mock SML model
        from semabridge.formats.sml.models import SMLModel, SMLDataset, SMLDimension, SMLColumn
        
        col1 = MagicMock()
        col1.unique_name = "revenue"
        col1.source_expression = None
        
        col2 = MagicMock()
        col2.unique_name = "units"
        col2.source_expression = None
        
        ds = MagicMock()
        ds.unique_name = "salesfact"
        ds.columns = [col1, col2]
        
        sml = MagicMock()
        sml.datasets = [ds]
        
        schema_map = emitter._build_schema_validation_map(sml)
        
        assert "salesfact" in schema_map
        assert "REVENUE" in schema_map["salesfact"]
        assert "UNITS" in schema_map["salesfact"]
    
    def test_build_schema_map_excludes_calculated_columns(self, emitter):
        """Test that calculated columns are excluded from schema map."""
        # Create mock SML
        col1 = MagicMock()
        col1.unique_name = "revenue"
        col1.source_expression = None
        
        col2 = MagicMock()
        col2.unique_name = "calculated_metric"
        col2.source_expression = "[revenue] * 2"  # Calculated
        
        ds = MagicMock()
        ds.unique_name = "salesfact"
        ds.columns = [col1, col2]
        
        sml = MagicMock()
        sml.datasets = [ds]
        
        schema_map = emitter._build_schema_validation_map(sml)
        
        # Calculated column should not be in map
        assert "REVENUE" in schema_map["salesfact"]
        assert len(schema_map["salesfact"]) == 1  # Only revenue


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
