"""
Tests for _sanitize_expression in SnowflakeEmitter.

Reproduces the 'invalid identifier SALESFACT.SCORE' error and validates
that all alias + column patterns are correctly normalised before DDL
is sent to Snowflake.
"""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig


@pytest.fixture()
def emitter() -> SnowflakeEmitter:
    config = SnowflakeConfig(
        account="acc",
        user="usr",
        password=SecretStr("pwd"),
        warehouse="wh",
        database="db",
        schema_name="sc",
        role="rl",
    )
    return SnowflakeEmitter(config, behavior=ConnectorBehavior())


ALIASES = {"SalesFact": "salesfact", "CustomerDim": "customerdim"}


class TestSanitizeExpressionAliasRewrite:
    """Step 3: old-style UPPERCASE aliases must become lowercase."""

    def test_uppercase_alias_quoted_col(self, emitter: SnowflakeEmitter) -> None:
        """SALESFACT."SCORE" → salesfact."SCORE" """
        result = emitter._sanitize_expression(
            'SUM(SALESFACT."SCORE")', dataset_aliases=ALIASES,
        )
        assert 'salesfact."SCORE"' in result
        assert "SALESFACT" not in result

    def test_uppercase_alias_bare_col(self, emitter: SnowflakeEmitter) -> None:
        """SALESFACT.SCORE → salesfact."SCORE"  (bug that caused the deployment error)"""
        result = emitter._sanitize_expression(
            "SUM(SALESFACT.SCORE)", dataset_aliases=ALIASES,
        )
        assert 'salesfact."SCORE"' in result
        assert "SALESFACT" not in result

    def test_mixed_case_alias_bare_col(self, emitter: SnowflakeEmitter) -> None:
        """Salesfact.SCORE → salesfact."SCORE" (case-insensitive match)"""
        result = emitter._sanitize_expression(
            "SUM(Salesfact.SCORE)", dataset_aliases=ALIASES,
        )
        assert 'salesfact."SCORE"' in result

    def test_multiple_refs_in_expression(self, emitter: SnowflakeEmitter) -> None:
        """Both alias.col refs in the same expression are fixed."""
        result = emitter._sanitize_expression(
            "SALESFACT.SCORE + CUSTOMERDIM.NAME",
            dataset_aliases=ALIASES,
        )
        assert 'salesfact."SCORE"' in result
        assert 'customerdim."NAME"' in result
        assert "SALESFACT" not in result
        assert "CUSTOMERDIM" not in result


class TestSanitizeExpressionSafetyNet:
    """Step 4: correct lowercase alias + bare column must still get quoted."""

    def test_lowercase_alias_bare_col(self, emitter: SnowflakeEmitter) -> None:
        """salesfact.SCORE → salesfact."SCORE" """
        result = emitter._sanitize_expression(
            "SUM(salesfact.SCORE)", dataset_aliases=ALIASES,
        )
        assert 'salesfact."SCORE"' in result

    def test_already_quoted_col_untouched(self, emitter: SnowflakeEmitter) -> None:
        """salesfact."SCORE" stays salesfact."SCORE" (no double-quoting)."""
        result = emitter._sanitize_expression(
            'SUM(salesfact."SCORE")', dataset_aliases=ALIASES,
        )
        assert result == 'SUM(salesfact."SCORE")'


class TestSanitizeExpressionBracketNotation:
    """Step 1: bracket notation from DAX/TMSL must be quoted."""

    def test_bracket_to_quoted(self, emitter: SnowflakeEmitter) -> None:
        result = emitter._sanitize_expression(
            "SUM([Score])", dataset_aliases=ALIASES,
        )
        assert '"SCORE"' in result
        assert "[Score]" not in result


class TestSanitizeExpressionEndToEnd:
    """Full pipeline: realistic expressions that hit multiple steps."""

    def test_aggregate_with_old_alias(self, emitter: SnowflakeEmitter) -> None:
        result = emitter._sanitize_expression(
            'SUM(SALESFACT."Revenue")', dataset_aliases=ALIASES,
        )
        assert result == 'SUM(salesfact."REVENUE")'

    def test_count_distinct_with_bare_col(self, emitter: SnowflakeEmitter) -> None:
        result = emitter._sanitize_expression(
            "COUNT(DISTINCT SALESFACT.SCORE)", dataset_aliases=ALIASES,
        )
        assert 'salesfact."SCORE"' in result
        assert "SALESFACT" not in result

    def test_window_function_expression(self, emitter: SnowflakeEmitter) -> None:
        expr = (
            'SUM(SALESFACT.SCORE) OVER ('
            'PARTITION BY SALESFACT.REGION '
            'ORDER BY SALESFACT.DATE)'
        )
        result = emitter._sanitize_expression(expr, dataset_aliases=ALIASES)
        assert 'salesfact."SCORE"' in result
        assert 'salesfact."REGION"' in result
        assert 'salesfact."DATE"' in result
        assert "SALESFACT" not in result

    def test_no_aliases_passthrough(self, emitter: SnowflakeEmitter) -> None:
        """Without dataset_aliases, expression passes through basic sanitisation."""
        result = emitter._sanitize_expression("SUM(X.Y)", dataset_aliases=None)
        assert result == "SUM(X.Y)"

    def test_empty_expression(self, emitter: SnowflakeEmitter) -> None:
        assert emitter._sanitize_expression("", dataset_aliases=ALIASES) == ""
        assert emitter._sanitize_expression(None, dataset_aliases=ALIASES) is None


class TestSanitizeExpressionExtraPrefixMap:
    """extra_prefix_map resolves short user-defined aliases from metric_overrides."""

    def test_short_alias_quoted_col(self, emitter: SnowflakeEmitter) -> None:
        """FACT."REVENUE" → factsales."REVENUE" via extra_prefix_map."""
        aliases = {"Fact_Sales": "factsales"}
        result = emitter._sanitize_expression(
            'SUM(FACT."REVENUE")',
            dataset_aliases=aliases,
            extra_prefix_map={"FACT": "factsales"},
        )
        assert 'factsales."REVENUE"' in result
        assert "FACT." not in result

    def test_short_alias_bare_col(self, emitter: SnowflakeEmitter) -> None:
        """PRODUCT.NAME → productdim."NAME" via extra_prefix_map."""
        aliases = {"ProductDim": "productdim"}
        result = emitter._sanitize_expression(
            "MAX(PRODUCT.NAME)",
            dataset_aliases=aliases,
            extra_prefix_map={"PRODUCT": "productdim"},
        )
        assert 'productdim."NAME"' in result
        assert "PRODUCT." not in result

    def test_window_function_with_multiple_short_aliases(
        self, emitter: SnowflakeEmitter
    ) -> None:
        """Full behavior.yaml-style override expression is cleaned end-to-end."""
        aliases = {
            "Fact_Sales": "factsales",
            "ProductDim": "productdim",
            "CalendarDim": "calendardim",
        }
        extra = {
            "FACT": "factsales",
            "PRODUCT": "productdim",
            "CALENDAR": "calendardim",
        }
        expr = (
            'LAG(SUM(FACT."REVENUE"), 12) OVER '
            '(PARTITION BY PRODUCT."PRODUCT KEY" ORDER BY CALENDAR."YEARPERIOD")'
        )
        result = emitter._sanitize_expression(
            expr, dataset_aliases=aliases, extra_prefix_map=extra
        )
        assert "FACT." not in result
        assert "PRODUCT." not in result
        assert "CALENDAR." not in result
        assert 'factsales."REVENUE"' in result
        assert 'productdim."PRODUCT_KEY"' in result
        assert 'calendardim."YEARPERIOD"' in result

    def test_scenario_filter_case_expression(
        self, emitter: SnowflakeEmitter
    ) -> None:
        """SCENARIO."SCENARIO" in a CASE expr is rewritten via extra_prefix_map."""
        aliases = {"ScenarioDim": "scenariodim", "Fact_Sales": "factsales"}
        extra = {"SCENARIO": "scenariodim", "FACT": "factsales"}
        expr = 'SUM(CASE WHEN SCENARIO."SCENARIO" = \'Budget\' THEN FACT."REVENUE" ELSE 0 END)'
        result = emitter._sanitize_expression(
            expr, dataset_aliases=aliases, extra_prefix_map=extra
        )
        assert "SCENARIO." not in result
        assert "FACT." not in result
        assert 'scenariodim."SCENARIO"' in result
        assert 'factsales."REVENUE"' in result


class TestSanitizeExpressionThreePartNames:
    """3-part qualified names (DB.SCHEMA.col) must be stripped to just quoted col."""

    def test_three_part_qualified_stripped(
        self, emitter: SnowflakeEmitter
    ) -> None:
        config = emitter.config
        config.database = "ANALYTICS_DB"
        config.schema_name = "SCHEMA"
        result = emitter._sanitize_expression(
            "SUM(ANALYTICS_DB.SCHEMA.SCORE)",
            dataset_aliases=ALIASES,
        )
        assert "ANALYTICS_DB" not in result
        assert "SCHEMA." not in result
        assert '"SCORE"' in result

    def test_database_prefix_bare_col_stripped(
        self, emitter: SnowflakeEmitter
    ) -> None:
        """ANALYTICS_DB.SCORE (2-part with db name) → "SCORE"."""
        config = emitter.config
        config.database = "ANALYTICS_DB"
        config.schema_name = "SCHEMA"
        result = emitter._sanitize_expression(
            "SUM(ANALYTICS_DB.SCORE)",
            dataset_aliases=ALIASES,
        )
        # db name must not survive into the output
        assert "ANALYTICS_DB" not in result


# =============================================================================
# Integration: full generate_ddls() pipeline  (fix for 000904 SALESFACT.SCORE)
# =============================================================================

import re as _re


class TestDDLGenerationPipeline:
    """Integration tests: generate_ddls() must never emit UPPERCASE.bareCol
    patterns that Snowflake rejects with error 000904.

    These tests build a minimal SMLModel and drive the full
    SnowflakeEmitter.generate_ddls() pipeline to verify the assembled
    DDL is clean before it would be sent to Snowflake.
    """

    def _minimal_sml(self, sql_expression: str):
        """Build the smallest valid SMLModel with one fact table and one metric."""
        from semabridge.sml.models import (
            SMLModel, SMLDataset, SMLColumn, SMLMetric,
            DataType, AggregationType,
        )
        ds = SMLDataset(
            unique_name="SalesFact",
            columns=[
                SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True),
                SMLColumn(unique_name="Score", data_type=DataType.DECIMAL),
            ],
        )
        metric = SMLMetric(
            unique_name="Score_Total",
            dataset="SalesFact",
            sql_expression=sql_expression,
            aggregation=AggregationType.SUM,
        )
        return SMLModel(unique_name="TestModel", datasets=[ds], metrics=[metric])

    def test_uppercase_alias_bare_col_absent_from_ddl(
        self, emitter: SnowflakeEmitter
    ) -> None:
        """sql_expression=SUM(SALESFACT.SCORE) → DDL contains salesfact."SCORE"."""
        sml = self._minimal_sml("SUM(SALESFACT.SCORE)")
        ddls = emitter.generate_ddls(sml)
        assert ddls, "generate_ddls() must return at least one DDL statement"
        ddl = ddls[0]
        # Correct form must be present
        assert 'salesfact."SCORE"' in ddl, (
            f'Expected salesfact."SCORE" in DDL:\n{ddl}'
        )
        # The invalid bare-column pattern must be absent
        assert _re.search(r"\bSALESFACT\.SCORE\b", ddl) is None, (
            f"Found invalid identifier SALESFACT.SCORE in DDL:\n{ddl}"
        )

    def test_mixed_case_alias_bare_col_absent_from_ddl(
        self, emitter: SnowflakeEmitter
    ) -> None:
        """sql_expression=SUM(Salesfact.Score) is also fully normalised."""
        sml = self._minimal_sml("SUM(Salesfact.Score)")
        ddls = emitter.generate_ddls(sml)
        assert ddls
        ddl = ddls[0]
        assert 'salesfact."SCORE"' in ddl
        assert _re.search(r"(?i)\bSalesfact\.Score\b", ddl) is None

    def test_physical_table_name_bare_col_absent_from_ddl(
        self, emitter: SnowflakeEmitter
    ) -> None:
        """SALES_FACT.SCORE (physical table name as prefix) is also rewritten."""
        sml = self._minimal_sml("SUM(SALES_FACT.SCORE)")
        ddls = emitter.generate_ddls(sml)
        assert ddls
        ddl = ddls[0]
        assert _re.search(r"\bSALES_FACT\.SCORE\b", ddl) is None
