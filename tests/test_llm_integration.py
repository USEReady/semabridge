"""
Tests for LLM-based DAX to SQL conversion integration.

Tests cover:
- LLMConfig validation
- LLMConfigManager loading
- LLMConverter prompt construction and SQL extraction
- DAXTranslator Tier 4 LLM fallback integration
- Backward compatibility with existing Tier 1-3 translations
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from semabridge.core.llm_config import LLMConfig, LLMConfigManager, LLMProjectConfig, PathsConfig
from semabridge.core.config_loader import ConfigLoadError, MissingEnvVarError
from semabridge.converter.llm_converter import LLMConverter, PROMPT_TEMPLATE
from semabridge.converter.dax_translator import DAXTranslator, DAXTranslationResult


# ============================================================================
# LLMConfig Tests
# ============================================================================


class TestLLMConfig:
    """Test LLMConfig validation."""

    def test_default_config_llm_disabled(self):
        config = LLMConfig()
        assert config.enabled is False
        assert config.model == "gpt-4o-mini"
        assert config.timeout_seconds == 30
        assert config.max_retries == 3

    def test_validate_disabled_no_error(self):
        """Validation should pass when LLM is disabled even without API key."""
        config = LLMConfig(enabled=False)
        config.validate()  # Should not raise

    def test_validate_enabled_missing_api_key(self):
        """Should raise MissingEnvVarError when enabled but no API key."""
        config = LLMConfig(enabled=True, model="gpt-4", api_key_env="NONEXISTENT_KEY_12345")
        with pytest.raises(MissingEnvVarError):
            config.validate()

    def test_validate_enabled_empty_model(self):
        """Should raise ConfigLoadError when model name is empty."""
        config = LLMConfig(enabled=True, model="", api_key_env="OPENAI_API_KEY")
        with pytest.raises(ConfigLoadError, match="llm.model.*empty"):
            config.validate()

    def test_validate_enabled_invalid_timeout(self):
        """Should raise ConfigLoadError when timeout is < 1."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            config = LLMConfig(enabled=True, model="gpt-4", timeout_seconds=0)
            with pytest.raises(ConfigLoadError, match="timeout_seconds"):
                config.validate()

    def test_validate_enabled_invalid_retries(self):
        """Should raise ConfigLoadError when max_retries is negative."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            config = LLMConfig(enabled=True, model="gpt-4", max_retries=-1)
            with pytest.raises(ConfigLoadError, match="max_retries"):
                config.validate()

    def test_api_key_from_env(self):
        """API key should be read from environment variable."""
        with patch.dict(os.environ, {"MY_LLM_KEY": "sk-test-123"}):
            config = LLMConfig(api_key_env="MY_LLM_KEY")
            assert config.api_key == "sk-test-123"

    def test_api_key_missing_returns_none(self):
        """Missing env var should return None."""
        config = LLMConfig(api_key_env="DEFINITELY_NONEXISTENT_KEY_XYZ")
        assert config.api_key is None


# ============================================================================
# LLMConfigManager Tests
# ============================================================================


class TestLLMConfigManager:
    """Test LLMConfigManager config loading."""

    def test_default_config_when_file_missing(self, tmp_path):
        """Should return defaults when config.yaml doesn't exist."""
        config = LLMConfigManager.load_config(str(tmp_path / "nonexistent.yaml"))
        assert config.llm.enabled is False
        assert config.llm.model == "gpt-4o-mini"

    def test_load_valid_config(self, tmp_path):
        """Should load valid config from YAML file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n"
            "  enabled: false\n"
            "  model: claude-3-sonnet\n"
            "  timeout_seconds: 60\n"
            "  max_retries: 5\n"
            "  api_key_env: ANTHROPIC_API_KEY\n"
            "paths:\n"
            "  duckdb_repo: ./my_data\n"
            "  logs: ./my_logs\n"
        )
        config = LLMConfigManager.load_config(str(config_file))
        assert config.llm.enabled is False
        assert config.llm.model == "claude-3-sonnet"
        assert config.llm.timeout_seconds == 60
        assert config.llm.max_retries == 5
        assert config.paths.duckdb_repo == "./my_data"

    def test_load_invalid_yaml(self, tmp_path):
        """Should raise ConfigLoadError for invalid YAML."""
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("{{invalid yaml!!")
        with pytest.raises(ConfigLoadError):
            LLMConfigManager.load_config(str(config_file))

    def test_load_empty_yaml(self, tmp_path):
        """Should return defaults for empty YAML file."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        config = LLMConfigManager.load_config(str(config_file))
        assert config.llm.enabled is False

    def test_get_default_config(self):
        """get_default_config should return LLM disabled."""
        config = LLMConfigManager.get_default_config()
        assert isinstance(config, LLMProjectConfig)
        assert config.llm.enabled is False


# ============================================================================
# LLMConverter Tests
# ============================================================================


class TestLLMConverter:
    """Test LLMConverter prompt construction and SQL extraction."""

    def _make_converter(self):
        """Create an LLMConverter with mocked API client."""
        config = LLMConfig(enabled=True, model="gpt-4", api_key_env="FAKE_KEY")
        converter = LLMConverter.__new__(LLMConverter)
        converter.config = config
        converter._api_client = MagicMock()
        return converter

    def test_build_prompt_contains_dax(self):
        """Prompt should contain the DAX expression."""
        converter = self._make_converter()
        dax = "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))"
        prompt = converter._build_prompt(dax, "snowflake")
        assert dax in prompt
        assert "SNOWFLAKE" in prompt

    def test_build_prompt_contains_dialect(self):
        """Prompt should reference the target dialect."""
        converter = self._make_converter()
        prompt = converter._build_prompt("SUM([X])", "databricks")
        assert "DATABRICKS" in prompt

    def test_extract_sql_from_code_block(self):
        """Should extract SQL from ```sql ... ``` code block."""
        converter = self._make_converter()
        response = '```sql\nSUM(CASE WHEN CALENDAR."YEAR" = 2025 THEN FACT."REVENUE" ELSE 0 END)\n```'
        sql = converter._extract_sql(response)
        assert sql is not None
        assert "CALENDAR" in sql
        assert "REVENUE" in sql

    def test_extract_sql_from_bare_expression(self):
        """Should extract bare SQL expression without code block."""
        converter = self._make_converter()
        response = 'SUM(CASE WHEN SCENARIO."SCENARIO" = \'Actual\' THEN FACT."REVENUE" ELSE 0 END)'
        sql = converter._extract_sql(response)
        assert sql is not None
        assert "SUM(CASE" in sql

    def test_extract_sql_from_response_with_text(self):
        """Should extract SQL even with surrounding explanation text."""
        converter = self._make_converter()
        response = "Here is the SQL:\nSUM(FACT.\"REVENUE\")\nThis calculates total revenue."
        sql = converter._extract_sql(response)
        assert sql is not None
        assert "SUM" in sql

    def test_extract_sql_returns_none_for_empty_response(self):
        """Should return None for empty response."""
        converter = self._make_converter()
        assert converter._extract_sql("") is None
        assert converter._extract_sql(None) is None

    def test_extract_sql_removes_semicolons(self):
        """Should strip trailing semicolons."""
        converter = self._make_converter()
        result = converter._extract_sql('SUM(FACT."REVENUE");')
        assert result is not None
        assert not result.endswith(";")

    def test_convert_success(self):
        """Full convert flow with mocked API response."""
        converter = self._make_converter()
        converter._api_client.call_llm.return_value = 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)'

        result = converter.convert("CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))")
        assert result is not None
        assert "CALENDAR" in result
        assert "REVENUE" in result

    def test_convert_api_failure(self):
        """Should return None when API returns None."""
        converter = self._make_converter()
        converter._api_client.call_llm.return_value = None

        result = converter.convert("CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))")
        assert result is None

    def test_convert_no_api_client(self):
        """Should return None when API client is not initialized."""
        converter = self._make_converter()
        converter._api_client = None

        result = converter.convert("SUM([X])")
        assert result is None

    def test_convert_empty_dax(self):
        """Should return None for empty DAX."""
        converter = self._make_converter()
        assert converter.convert("") is None
        assert converter.convert("   ") is None


# ============================================================================
# DAXTranslator Tier 4 Integration Tests
# ============================================================================


class TestDAXTranslatorLLMIntegration:
    """Test DAXTranslator Tier 4 LLM fallback."""

    def test_tier4_invoked_when_tier1_3_fail(self):
        """LLM converter should be invoked when Tiers 1-3 fail."""
        translator = DAXTranslator(llm_config=False)

        # Manually attach a mock LLM converter
        mock_converter = MagicMock()
        mock_converter.convert.return_value = 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)'
        translator.llm_converter = mock_converter

        # Complex DAX that Tier 1-3 can't handle
        dax = "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))"
        result = translator.translate(dax, "FACT", "Fact")

        assert result.is_success is True
        assert result.tier == 4
        assert result.source == "llm"
        assert "CALENDAR" in result.sql
        mock_converter.convert.assert_called_once()

    def test_tier4_not_invoked_when_tier1_succeeds(self):
        """LLM should NOT be invoked when Tier 1 succeeds."""
        translator = DAXTranslator(llm_config=False)
        mock_converter = MagicMock()
        translator.llm_converter = mock_converter

        result = translator.translate("SUM('Fact'[Revenue])", "FACT", "Fact")

        assert result.is_success is True
        assert result.tier == 1
        mock_converter.convert.assert_not_called()

    def test_tier4_not_invoked_when_override_exists(self):
        """LLM should NOT be invoked when manual override exists."""
        translator = DAXTranslator(llm_config=False)
        mock_converter = MagicMock()
        translator.llm_converter = mock_converter

        overrides = {"Revenue SPLY": 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)'}
        result = translator.translate(
            "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))",
            "FACT", "Fact",
            overrides=overrides,
            metric_name="Revenue SPLY"
        )

        assert result.is_success is True
        assert result.tier == 0  # Override tier
        mock_converter.convert.assert_not_called()

    def test_tier4_returns_none_on_llm_failure(self):
        """Should return None when LLM conversion fails."""
        translator = DAXTranslator(llm_config=False)
        mock_converter = MagicMock()
        mock_converter.convert.return_value = None
        translator.llm_converter = mock_converter

        dax = "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))"
        result = translator.translate(dax, "FACT", "Fact")

        assert result.is_success is False
        assert result.tier == 4
        assert result.sql is None

    def test_llm_disabled_skips_tier4(self):
        """When LLM is disabled, Tier 4 should be skipped."""
        translator = DAXTranslator(llm_config=False)
        assert translator.llm_converter is None

        dax = "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))"
        result = translator.translate(dax, "FACT", "Fact")

        assert result.is_success is False
        assert result.tier == 4
        assert result.sql is None


# ============================================================================
# Backward Compatibility Tests
# ============================================================================


class TestDAXTranslatorBackwardCompat:
    """Ensure DAXTranslator remains backward compatible."""

    @pytest.fixture
    def translator(self):
        return DAXTranslator(llm_config=False)

    def test_tier1_sum_still_works(self, translator):
        result = translator.translate("SUM('Fact'[Revenue])", "FACT", "Fact")
        assert result.is_success is True
        assert result.tier == 1
        assert "SUM" in result.sql

    def test_tier1_count_still_works(self, translator):
        result = translator.translate("COUNT('Fact'[OrderID])", "FACT", "Fact")
        assert result.is_success is True
        assert result.tier == 1

    def test_tier2_arithmetic_still_works(self, translator):
        metrics = [
            MagicMock(unique_name="Gross Sales", sql_expression='SUM(FACT."GROSS_SALES")', sync_enabled=True),
            MagicMock(unique_name="Discounts", sql_expression='SUM(FACT."DISCOUNTS")', sync_enabled=True),
        ]
        result = translator.translate("[Gross Sales] - [Discounts]", "FACT", "Fact", metrics_context=metrics)
        assert result.is_success is True
        assert result.tier == 2

    def test_translation_result_has_source_field(self):
        """New 'source' field should default to 'rule'."""
        result = DAXTranslationResult("SUM(X)", 1, "SUM([X])")
        assert result.source == "rule"

    def test_translation_result_llm_source(self):
        """Can set source to 'llm'."""
        result = DAXTranslationResult("SUM(X)", 4, "CALCULATE(...)", source="llm")
        assert result.source == "llm"
        assert result.tier == 4


# ============================================================================
# All 8 Override Measures via LLM
# ============================================================================


OVERRIDE_MEASURES = [
    ("Revenue SPLY", "CALCULATE([Total Revenue],SAMEPERIODLASTYEAR('Calendar'[Date]))"),
    ("Gross Margin SPLY", "CALCULATE([Gross Margin],SAMEPERIODLASTYEAR('Calendar'[Date]))"),
    ("YTD Revenue SPLY", "CALCULATE([YTD Revenue],SAMEPERIODLASTYEAR(DATESYTD('Calendar'[Date])))"),
    ("COGS SPLY", "CALCULATE([Total COGS],SAMEPERIODLASTYEAR('Calendar'[Date]))"),
    ("YTD COGS SPLY", "CALCULATE([YTD COGS],SAMEPERIODLASTYEAR(DATESYTD('Calendar'[Date])))"),
    ("YTD GM SPLY", "CALCULATE([YTD Gross Margin],SAMEPERIODLASTYEAR(DATESYTD('Calendar'[Date])))"),
    ("Revenue Budget", "CALCULATE([Total Revenue], FILTER(Scenario, Scenario[Scenario]=\"Budget\"))"),
    ("RevenueTY", "CALCULATE([Total Revenue], FILTER(Scenario, Scenario[Scenario]=\"Actual\"))"),
]


class TestLLMAllOverrideMeasures:
    """Test that all 8 override measures can be translated via LLM Tier 4."""

    @pytest.mark.parametrize("measure_name,dax_expr", OVERRIDE_MEASURES)
    def test_llm_tier4_invoked_for_measure(self, measure_name, dax_expr):
        """Each override measure should invoke Tier 4 when no manual override exists."""
        translator = DAXTranslator(llm_config=False)

        mock_converter = MagicMock()
        mock_converter.convert.return_value = f'SUM(FACT."REVENUE")  -- LLM for {measure_name}'
        translator.llm_converter = mock_converter

        result = translator.translate(dax_expr, "FACT", "Fact", metric_name=measure_name)

        assert result.is_success is True
        assert result.tier == 4
        assert result.source == "llm"
        mock_converter.convert.assert_called_once()

    @pytest.mark.parametrize("measure_name,dax_expr", OVERRIDE_MEASURES)
    def test_manual_override_takes_precedence_over_llm(self, measure_name, dax_expr):
        """Manual override should always take precedence over LLM."""
        translator = DAXTranslator(llm_config=False)

        mock_converter = MagicMock()
        translator.llm_converter = mock_converter

        manual_sql = f'SUM(FACT."MANUAL_OVERRIDE")  -- for {measure_name}'
        overrides = {measure_name: manual_sql}
        result = translator.translate(dax_expr, "FACT", "Fact", overrides=overrides, metric_name=measure_name)

        assert result.is_success is True
        assert result.tier == 0  # Override tier
        assert result.sql == manual_sql
        mock_converter.convert.assert_not_called()
