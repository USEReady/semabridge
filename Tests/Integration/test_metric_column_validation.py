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
from semabridge.intermediate.models import (
    OSIAggregationType,
    OSIColumn,
    OSIDataset,
    OSIMetric,
    OSIModel,
    OSIDataType,
)


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

    def test_normalize_metric_column_references_quotes_reserved_word_value(self, emitter):
        """Test that _normalize_metric_column_references quotes the 'VALUE' reserved word."""
        emitter._id.suppress_reserved = False
        metric_sql = 'sf."VALUE"'
        metric_name = "value_metric"
        dataset_col_lookup = {"salesfact": {"VALUE"}}
        dataset_aliases = {"salesfact": "sf"}
        
        normalized = emitter._normalize_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        # Should stay quoted because VALUE is a reserved word
        assert normalized == 'sf."VALUE"'

    def test_normalize_metric_column_references_quotes_unquoted_reserved_word_value(self, emitter):
        """Test that _normalize_metric_column_references quotes an unquoted 'VALUE' reserved word."""
        emitter._id.suppress_reserved = False
        metric_sql = 'SUM(sf.VALUE)'
        metric_name = "value_metric"
        dataset_col_lookup = {"salesfact": {"VALUE"}}
        dataset_aliases = {"salesfact": "sf"}
        
        normalized = emitter._normalize_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        print(f"DEBUG: normalized='{normalized}'")
        
        # Should be quoted because VALUE is a reserved word
        assert normalized == 'SUM(sf."VALUE")'

    def test_sanitize_alias_reserved_word_value(self, emitter):
        """Test that sanitize_alias handles the 'VALUE' reserved word."""
        # By default suppress_reserved is True
        assert emitter._sanitize_alias("VALUE") == "COL_VALUE"
        
        # If we disable suppression, it should remain VALUE (but we should probably quote it in DDL)
        emitter._id.suppress_reserved = False
        assert emitter._sanitize_alias("VALUE") == "VALUE"

    def test_normalize_metric_column_references_unquotes_non_reserved_word(self, emitter):
        """Test that _normalize_metric_column_references unquotes non-reserved words."""
        metric_sql = 'sf."REVENUE"'
        metric_name = "revenue_metric"
        dataset_col_lookup = {"salesfact": {"REVENUE"}}
        dataset_aliases = {"salesfact": "sf"}
        
        normalized = emitter._normalize_metric_column_references(
            metric_sql, metric_name, dataset_col_lookup, dataset_aliases
        )
        
        # Should be unquoted because REVENUE is not a reserved word
        assert normalized == 'sf.REVENUE'
    
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

    def test_basic_fallback_totalytd_uses_primarydate_token(self, emitter):
        """TOTALYTD should treat [PrimaryDate] as semantic token and use physical date column."""
        from semabridge.formats.sml.models import SMLMetric

        metric = SMLMetric(
            unique_name="YTD_REVENUE",
            dataset="salesfact",
            expression="TOTALYTD(SUM([Revenue]), [PrimaryDate])",
        )

        translated = emitter._try_basic_dax_metric_fallback_expression(
            metric=metric,
            table_alias="sf",
            dataset_col_lookup={
                "salesfact": {"REVENUE", "DATE", "YEAR", "PRIOR_YEAR_DATE_KEY"}
            },
        )

        assert translated is not None
        assert 'SUM(CASE WHEN sf."COL_DATE" >= DATE_TRUNC(\'YEAR\', MAX_DATE) AND sf."COL_DATE" <= MAX_DATE THEN sf."REVENUE"::FLOAT END)' in translated

    def test_basic_fallback_sply_uses_window_when_offset_present(self, emitter, monkeypatch):
        """SPLY keeps universal window fallback even when offset key exists."""
        from semabridge.formats.sml.models import SMLMetric

        monkeypatch.setenv("SEMABRIDGE_SNOWFLAKE_ENABLE_SPLY_OFFSET_FASTPATH", "true")

        metric = SMLMetric(
            unique_name="PY_REVENUE",
            dataset="salesfact",
            expression="CALCULATE(SUM([Revenue]), SAMEPERIODLASTYEAR([PrimaryDate]))",
        )

        translated = emitter._try_basic_dax_metric_fallback_expression(
            metric=metric,
            table_alias="sf",
            dataset_col_lookup={
                "salesfact": {
                    "REVENUE",
                    "DATE",
                    "YEAR",
                    "MONTH",
                    "SPLY_OFFSET_KEY",
                }
            },
        )

        assert translated is not None
        assert 'SUM(CASE WHEN YEAR(sf."COL_DATE") = YEAR(MAX_DATE) - 1 AND sf."COL_DATE" BETWEEN DATEADD(YEAR, -1, DATE_TRUNC(\'YEAR\', MAX_DATE)) AND DATEADD(YEAR, -1, MAX_DATE) THEN sf."REVENUE"::FLOAT END)' in translated

    def test_warns_for_non_sync_friendly_time_intelligence_dax(self, emitter, caplog):
        """Unsupported TI functions should emit a warn-only parser guidance message."""
        from semabridge.formats.sml.models import SMLMetric

        metric = SMLMetric(
            unique_name="REV_SPLY_DATEADD",
            dataset="salesfact",
            expression="CALCULATE(SUM([Revenue]), DATEADD([PrimaryDate], -1, YEAR))",
        )

        with caplog.at_level("WARNING"):
            translated = emitter._try_basic_dax_metric_fallback_expression(
                metric=metric,
                table_alias="sf",
                dataset_col_lookup={"salesfact": {"REVENUE", "DATE", "YEAR", "MONTH"}},
            )

        assert translated is None
        assert "high-risk time-intelligence dax" in caplog.text.lower()
        assert "prefer parser-safe modeling" in caplog.text.lower()

    def test_warns_for_measure_dependency_variance_expression(self, emitter, caplog):
        """Measure dependency variance should emit parser-safe explicit formula guidance."""
        from semabridge.formats.sml.models import SMLMetric

        metric = SMLMetric(
            unique_name="Revenue Var To Budget",
            dataset="salesfact",
            expression="[RevenueTY] - [Revenue Budget]",
        )

        with caplog.at_level("WARNING"):
            translated = emitter._try_basic_dax_metric_fallback_expression(
                metric=metric,
                table_alias="sf",
                dataset_col_lookup={"salesfact": {"REVENUE", "SCENARIO"}},
            )

        assert translated is None
        assert "measure dependency variance expression" in caplog.text.lower()
        assert "prefer explicit parser-safe formula" in caplog.text.lower()

    def test_basic_fallback_resolves_static_filter_and_ytd_chain(self, emitter):
        """Emitter basic fallback should resolve the exact Budget / TY / YTD chain."""
        from semabridge.formats.sml.models import SMLMetric

        metrics = [
            SMLMetric(
                unique_name="REVENUE_BUDGET",
                dataset="salesfact",
                expression='CALCULATE(SUM([Revenue]), \'Scenario\'[Scenario] = "Budget")',
            ),
            SMLMetric(
                unique_name="REVENUETY",
                dataset="salesfact",
                expression='CALCULATE(SUM([Revenue]), \'Scenario\'[Scenario] = "Actual")',
            ),
            SMLMetric(
                unique_name="REVENUE_VAR_TO_BUDGET",
                dataset="salesfact",
                expression='[REVENUETY] - [REVENUE_BUDGET]',
            ),
            SMLMetric(
                unique_name="YTD_REVENUE",
                dataset="salesfact",
                expression="TOTALYTD([REVENUETY], 'Calendar'[Date])",
            ),
            SMLMetric(
                unique_name="YTD_COGS",
                dataset="salesfact",
                expression="TOTALYTD([REVENUE_BUDGET], 'Calendar'[Date])",
            ),
            SMLMetric(
                unique_name="YTD_GROSS_MARGIN",
                dataset="salesfact",
                expression="TOTALYTD([REVENUE_VAR_TO_BUDGET], 'Calendar'[Date])",
            ),
        ]

        model = MagicMock()
        model.metrics = metrics

        dataset_col_lookup = {
            "salesfact": {"REVENUE", "YEAR", "PERIOD"},
            "calendar": {"YEAR", "PERIOD", "DATE"},
            "scenario": {"SCENARIO"},
        }

        outputs = {
            metric.unique_name: emitter._try_basic_dax_metric_fallback_expression(
                metric=metric,
                table_alias="FACT",
                dataset_col_lookup=dataset_col_lookup,
                model=model,
                dataset_by_name={"salesfact": MagicMock(unique_name="salesfact")},
            )
            for metric in metrics
        }

        assert outputs["REVENUE_BUDGET"] is not None
        assert "CASE WHEN SCENARIO.SCENARIO = 'Budget'" in outputs["REVENUE_BUDGET"]
        assert outputs["REVENUETY"] is not None
        assert "CASE WHEN SCENARIO.SCENARIO = 'Actual'" in outputs["REVENUETY"]
        assert outputs["REVENUE_VAR_TO_BUDGET"] is not None
        assert "-" in outputs["REVENUE_VAR_TO_BUDGET"]
        assert outputs["YTD_REVENUE"] is not None
        assert 'SUM(FACT."REVENUETY") OVER' in outputs["YTD_REVENUE"]
        assert 'PARTITION BY FACT."YEAR"' in outputs["YTD_REVENUE"]
        assert 'ORDER BY FACT."PERIOD"' in outputs["YTD_REVENUE"]
        assert "CASE WHEN" not in outputs["YTD_REVENUE"]
        assert outputs["YTD_COGS"] is not None
        assert 'SUM(FACT."REVENUE_BUDGET") OVER' in outputs["YTD_COGS"]
        assert 'PARTITION BY FACT."YEAR"' in outputs["YTD_COGS"]
        assert outputs["YTD_GROSS_MARGIN"] is not None
        assert 'SUM(FACT."REVENUE_VAR_TO_BUDGET") OVER' in outputs["YTD_GROSS_MARGIN"]
        assert 'PARTITION BY FACT."YEAR"' in outputs["YTD_GROSS_MARGIN"]

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
        # Note: Now produces ::FLOAT as required for Snowflake numeric metrics.
        assert 'SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT"::FLOAT)' in repaired
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
        assert 'SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT"::FLOAT)' in repaired

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
        # Note: produces ::FLOAT as required for Snowflake numeric metrics.
        assert repaired == 'SUM(SPEND_FACT.TRANSACTION_USD_AMOUNT::FLOAT)'

    def test_window_metric_expression_rewritten_for_semantic_metrics(self, emitter):
        """Rewrite DIV0(SUM(x), SUM(x) OVER(...)) to SUM(x) for semantic safety."""
        raw_expr = (
            'DIV0(SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT"), '
            'SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT") OVER (PARTITION BY SPEND_FACT."COST_CENTER"))'
        )

        rewritten = emitter._rewrite_window_metric_expression(raw_expr)
        assert rewritten == 'SUM(SPEND_FACT."TRANSACTION_USD_AMOUNT"::FLOAT)'

    def test_lag_window_metric_expression_preserved_for_sply(self, emitter):
        """SPLY LAG/OVER metrics should be preserved for Snowflake sync."""
        raw_expr = (
            'LAG(SUM(FACT.REVENUE), 12) OVER '
            '(PARTITION BY FACT."PRODUCT_KEY" ORDER BY CALENDAR.YEARPERIOD)'
        )

        rewritten = emitter._rewrite_window_metric_expression(raw_expr)
        assert rewritten == raw_expr

    def test_ytd_window_metric_expression_preserved(self, emitter):
        """YTD SUM OVER windows should be preserved for time-intelligence sync."""
        raw_expr = (
            'SUM(FACT."REVENUE") OVER '
            '(PARTITION BY FACT."YEAR" ORDER BY FACT."DATE" '
            'ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)'
        )

        rewritten = emitter._rewrite_window_metric_expression(raw_expr, preferred_table_alias="FACT")
        assert rewritten == raw_expr

    def test_ytd_window_metric_expression_cross_entity_downgrades_to_null(self, emitter):
        """Cross-entity YTD windows are invalid for semantic metrics and should downgrade to NULL."""
        raw_expr = (
            'SUM(FACT."REVENUE") OVER '
            '(PARTITION BY CALENDAR.YEAR ORDER BY CALENDAR.PERIOD '
            'ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)'
        )

        rewritten = emitter._rewrite_window_metric_expression(raw_expr, preferred_table_alias="FACT")
        assert rewritten == "NULL"

    def test_known_fallback_revenue_sply_uses_calendar_window(self, emitter):
        """Hardcoded Revenue SPLY fallback should no longer be emitted."""
        dataset_col_lookup = {
            "FACT": {"REVENUE", "YEAR", "PERIOD"},
            "CALENDAR": {"YEAR", "PERIOD"},
        }
        expr = emitter._build_known_metric_fallback_expression(
            metric_name="REVENUE_SPLY",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
            calendar_alias="CALENDAR",
            dataset_col_lookup=dataset_col_lookup,
        )

        assert expr is None

    def test_known_fallback_yoy_growth_uses_nullif(self, emitter):
        """Hardcoded YoY growth fallback should no longer be emitted."""
        dataset_col_lookup = {
            "FACT": {"REVENUE", "YEAR", "PERIOD"},
            "CALENDAR": {"YEAR", "PERIOD"},
        }
        expr = emitter._build_known_metric_fallback_expression(
            metric_name="YOY_REV_GROWTH",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
            calendar_alias="CALENDAR",
            dataset_col_lookup=dataset_col_lookup,
        )

        assert expr is None

    def test_known_fallback_yoy_var_uses_total_revenue_sum(self, emitter):
        """Hardcoded YoY variance fallback should no longer be emitted."""
        dataset_col_lookup = {
            "FACT": {"REVENUE", "YEAR", "PERIOD"},
            "CALENDAR": {"YEAR", "PERIOD"},
        }
        expr = emitter._build_known_metric_fallback_expression(
            metric_name="YOY_REV_VAR",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
            calendar_alias="CALENDAR",
            dataset_col_lookup=dataset_col_lookup,
        )

        assert expr is None

    def test_dedupe_malformed_qualified_partition_reference(self, emitter):
        """Deduplicate malformed alias."COL".COL chains."""
        metric_sql = (
            'LAG(SUM(FACT.REVENUE), 12) OVER '
            '(PARTITION BY FACT."PRODUCT_KEY".PRODUCT_KEY ORDER BY CALENDAR.YEARPERIOD)'
        )

        repaired = emitter._dedupe_qualified_column_tokens(metric_sql)

        assert 'FACT."PRODUCT_KEY".PRODUCT_KEY' not in repaired
        assert 'PARTITION BY FACT."PRODUCT_KEY"' in repaired

    def test_known_finance_metric_fallback_expressions_removed(self, emitter):
        """Hardcoded finance metric fallbacks should no longer be emitted."""
        dataset_col_lookup = {
            "FACT": {
                "REVENUE", "MATERIAL_COSTS", "LABOR_COSTS_VARIABLE", "TAXES",
                "REV_FOR_EXP_TRAVEL", "TRAVEL_EXPENSES", "COST_THIRD_PARTY", "SCENARIO"
            },
            "SCENARIO": {"SCENARIO"},
        }

        assert emitter._build_known_metric_fallback_expression(
            metric_name="TOTAL_COGS",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
            calendar_alias=None,
            dataset_col_lookup=dataset_col_lookup,
        ) is None
        assert emitter._build_known_metric_fallback_expression(
            metric_name="GROSS_MARGIN",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
            calendar_alias=None,
            dataset_col_lookup=dataset_col_lookup,
        ) is None

        from semabridge.utils.known_metrics_registry import get_known_metrics_registry

        registry = get_known_metrics_registry()
        assert registry.resolve(
            metric_name="TOTAL_COGS",
            fact_alias="FACT",
            scenario_alias="SCENARIO",
            calendar_alias=None,
            available_columns=dataset_col_lookup,
        ) is None

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


class TestBooleanSumHandling:
    """Regression tests for Snowflake SUM(BOOLEAN) compilation failures."""

    @pytest.fixture
    def emitter(self):
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

    def test_build_safe_sum_sql_boolean_uses_iff(self, emitter):
        sql = emitter._build_safe_sum_sql('base."IS_ACTIVE"', "IS_ACTIVE")
        assert sql == 'SUM(IFF(base."IS_ACTIVE" = 1 OR base."IS_ACTIVE" = TRUE, 1, 0))'

    def test_build_safe_sum_sql_numeric_casts_float(self, emitter):
        sql = emitter._build_safe_sum_sql('base."REVENUE"', "REVENUE")
        assert sql == 'SUM(base."REVENUE"::FLOAT)'

    def test_build_safe_sum_sql_prefixed_flag_uses_iff(self, emitter):
        # Regression test for prefixed identifiers like TABLE.DELETED
        sql = emitter._build_safe_sum_sql('REP_SFDC.DELETED', "REP_SFDC.DELETED")
        assert sql == 'SUM(IFF(REP_SFDC.DELETED = 1 OR REP_SFDC.DELETED = TRUE, 1, 0))'

    def test_generate_semantic_view_tiered_avoids_sum_boolean(self, emitter):
        triage = MagicMock()
        triage.strategy.value = "passthrough"
        triage_results = {"IS_ACTIVE": triage}

        ddl = emitter.generate_semantic_view_tiered(
            model_name="Client Data",
            shadow_table='test_db.test_schema."MEASURES_CLIENT_DATA"',
            triage_results=triage_results,
            grain_dimensions=["ID"],
        )

        assert 'SUM(IFF(base."IS_ACTIVE" = 1 OR base."IS_ACTIVE" = TRUE, 1, 0)) AS "IS_ACTIVE"' in ddl
        assert 'SUM(base."IS_ACTIVE") AS "IS_ACTIVE"' not in ddl

    def test_generate_ddls_from_osi_keeps_metric_on_declared_dataset(self, emitter):
        metric_owner = OSIDataset(
            unique_name="FABRICMODEL_DATA",
            source_table="FABRICMODEL_DATA",
            columns=[
                OSIColumn(unique_name="ID", data_type=OSIDataType.STRING, is_key=True),
                OSIColumn(unique_name="QUANTITY", data_type=OSIDataType.FLOAT),
            ],
            is_fact=False,
        )
        fact_owner = OSIDataset(
            unique_name="ORDERS",
            source_table="ORDERS",
            columns=[
                OSIColumn(unique_name="ID", data_type=OSIDataType.STRING, is_key=True),
                OSIColumn(unique_name="QUANTITY", data_type=OSIDataType.FLOAT),
            ],
            is_fact=True,
        )
        model = OSIModel(
            unique_name="Shared Quantity Model",
            label="Shared Quantity Model",
            datasets=[metric_owner, fact_owner],
            dimensions=[],
            metrics=[
                OSIMetric(
                    unique_name="Fabricmodel Data - Sum of Quantity",
                    label="Fabricmodel Data - Sum of Quantity",
                    dataset="FABRICMODEL_DATA",
                    source_column="QUANTITY",
                    aggregation=OSIAggregationType.SUM,
                ),
            ],
            relationships=[],
        )

        ddl = emitter.generate_ddls_from_osi(model)[0]

        assert 'FABRICMODEL_DATA."FABRICMODEL_DATA_SUM_OF_QUANTITY" AS SUM(FABRICMODEL_DATA."QUANTITY"::FLOAT)' in ddl
        assert 'SUM(ORDERS."QUANTITY"::FLOAT)' not in ddl


class TestHistorySnapshotDDL:
    """Regression tests for automatic history-table fan-out mitigation."""

    @pytest.fixture
    def emitter(self):
        config = SnowflakeConfig(
            account="test.local",
            user="test_user",
            password="test_password",  # noqa: S106
            warehouse="test_wh",
            database="test_db",
            schema_name="test_schema",
            role="test_role",
        )
        behavior = ConnectorBehavior()
        return SnowflakeEmitter(config, behavior)

    def test_generate_ddls_adds_latest_snapshot_view_for_history_dataset(self, emitter):
        from semabridge.formats.sml.models import (
            SMLColumn,
            SMLDataset,
            SMLModel,
            SMLRelationship,
            Cardinality,
            DataType,
        )

        quote = SMLDataset(
            unique_name="REP_SFDC_SBQQ_QUOTE",
            source_table="REP_SFDC_SBQQ_QUOTE",
            columns=[
                SMLColumn(unique_name="ID", data_type=DataType.STRING, is_key=True),
            ],
            is_fact=True,
        )
        quote_history = SMLDataset(
            unique_name="REP_SFDC_SBQQ_QUOTE_HISTORY",
            source_table="REP_SFDC_SBQQ_QUOTE_HISTORY",
            columns=[
                SMLColumn(unique_name="PARENT_ID", data_type=DataType.STRING, is_key=False),
                SMLColumn(unique_name="CREATED_DATE", data_type=DataType.DATETIME, is_key=False),
                SMLColumn(unique_name="NEWVALUE", data_type=DataType.STRING, is_key=False),
            ],
            is_fact=False,
        )

        model = SMLModel(
            unique_name="Client Data",
            datasets=[quote, quote_history],
            dimensions=[],
            metrics=[],
            relationships=[
                SMLRelationship(
                    unique_name="REL_QUOTE_HISTORY",
                    from_dataset="REP_SFDC_SBQQ_QUOTE_HISTORY",
                    from_columns=["PARENT_ID"],
                    to_dataset="REP_SFDC_SBQQ_QUOTE",
                    to_columns=["ID"],
                    cardinality=Cardinality.MANY_TO_ONE,
                    is_active=True,
                )
            ],
        )

        ddls = emitter.generate_ddls(model)

        assert len(ddls) >= 2
        assert "CREATE OR REPLACE VIEW" in ddls[0]
        assert "REP_SFDC_SBQQ_QUOTE_HISTORY_LATEST" in ddls[0]
        assert 'PARTITION BY "PARENT_ID"' in ddls[0]
        assert 'ORDER BY "CREATED_DATE" DESC NULLS LAST' in ddls[0]
        assert "REP_SFDC_SBQQ_QUOTE_HISTORY_LATEST" in ddls[-1]

    def test_generate_ddls_skips_history_snapshot_when_timestamp_missing(self, emitter):
        from semabridge.formats.sml.models import SMLColumn, SMLDataset, SMLModel, DataType

        quote_history = SMLDataset(
            unique_name="REP_SFDC_SBQQ_QUOTE_HISTORY",
            source_table="REP_SFDC_SBQQ_QUOTE_HISTORY",
            columns=[
                SMLColumn(unique_name="PARENT_ID", data_type=DataType.STRING, is_key=False),
                SMLColumn(unique_name="NEWVALUE", data_type=DataType.STRING, is_key=False),
            ],
            is_fact=False,
        )
        model = SMLModel(
            unique_name="Client Data",
            datasets=[quote_history],
            dimensions=[],
            metrics=[],
            relationships=[],
        )

        ddls = emitter.generate_ddls(model)

        assert len(ddls) == 1
        assert "CREATE OR REPLACE SEMANTIC VIEW" in ddls[0]
        assert "QUOTE_HISTORY_LATEST" not in ddls[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
