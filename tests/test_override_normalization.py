"""
End-to-end tests for metric_override pre-sanitization.

Validates that metric_overrides containing arbitrary short SQL prefixes
(e.g. FACT, PRODUCT, CALENDAR) are fully normalized to lowercase aliases
with double-quoted columns before they enter the Snowflake DDL pipeline,
and that the final DDL passes validate_semantic_view_sql() without errors.
"""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.core.behavior import ConnectorBehavior, SemanticModelBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.intermediate.models import (
    OSIAggregationType,
    OSICardinality,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)
from semabridge.utils.naming import validate_semantic_view_sql


# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture()
def snowflake_config() -> SnowflakeConfig:
    return SnowflakeConfig(
        account="acc",
        user="usr",
        password=SecretStr("pwd"),
        warehouse="wh",
        database="ANALYTICS_DB",
        schema_name="SEMANTIC_LAYER",
        role="rl",
    )


@pytest.fixture()
def osi_model_with_short_aliases() -> OSIModel:
    """OSI model with four datasets whose metric_overrides reference via
    short aliases (FACT, PRODUCT, CALENDAR, SCENARIO)."""
    return OSIModel(
        unique_name="revenue_model",
        label="Revenue Model",
        version="1.0.0",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="Fact_Sales",
                label="Fact Sales",
                is_fact=True,
                columns=[
                    OSIColumn(unique_name="REVENUE", data_type=OSIDataType.DECIMAL),
                    OSIColumn(unique_name="CUSTOMER_ID", data_type=OSIDataType.INTEGER, is_key=True),
                ],
            ),
            OSIDataset(
                unique_name="ProductDim",
                label="Product Dimension",
                columns=[
                    OSIColumn(unique_name="PRODUCT_KEY", data_type=OSIDataType.INTEGER, is_key=True),
                    OSIColumn(unique_name="NAME", data_type=OSIDataType.STRING),
                ],
            ),
            OSIDataset(
                unique_name="CalendarDim",
                label="Calendar Dimension",
                columns=[
                    OSIColumn(unique_name="YEARPERIOD", data_type=OSIDataType.INTEGER, is_key=True),
                    OSIColumn(unique_name="YEAR", data_type=OSIDataType.INTEGER),
                ],
            ),
            OSIDataset(
                unique_name="ScenarioDim",
                label="Scenario Dimension",
                columns=[
                    OSIColumn(unique_name="SCENARIO", data_type=OSIDataType.STRING, is_key=True),
                ],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="Total Revenue",
                dataset="Fact_Sales",
                source_column="REVENUE",
                aggregation=OSIAggregationType.SUM,
            ),
            OSIMetric(
                unique_name="Revenue SPLY",
                dataset="Fact_Sales",
                source_column="REVENUE",
                aggregation=OSIAggregationType.SUM,
                expression='LAG(SUM([REVENUE]), 12)',
            ),
            OSIMetric(
                unique_name="Revenue Budget",
                dataset="Fact_Sales",
                source_column="REVENUE",
                aggregation=OSIAggregationType.SUM,
                expression="CALCULATE([Total Revenue], [SCENARIO] = 'Budget')",
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="Sales_Product",
                from_dataset="Fact_Sales",
                from_columns=["CUSTOMER_ID"],
                to_dataset="ProductDim",
                to_columns=["PRODUCT_KEY"],
                cardinality=OSICardinality.MANY_TO_ONE,
            ),
        ],
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestOsiToSmlOverrideSanitization:
    """The pre-sanitizer in from_osi rewrites short-alias SQL overrides."""

    def test_override_with_short_aliases_is_sanitized(
        self, osi_model_with_short_aliases: OSIModel
    ) -> None:
        """After from_osi, metric.sql_expression must not contain FACT. PRODUCT.
        CALENDAR. or SCENARIO. uppercase prefixes."""
        overrides = {
            "Revenue SPLY": (
                'LAG(SUM(FACT."REVENUE"), 12) OVER '
                '(PARTITION BY PRODUCT."PRODUCT KEY" ORDER BY CALENDAR."YEARPERIOD")'
            ),
            "Revenue Budget": (
                "SUM(CASE WHEN SCENARIO.\"SCENARIO\" = 'Budget' "
                "THEN FACT.\"REVENUE\" ELSE 0 END)"
            ),
        }
        override_alias_map = {
            "FACT": "Fact_Sales",
            "PRODUCT": "ProductDim",
            "CALENDAR": "CalendarDim",
            "SCENARIO": "ScenarioDim",
        }

        converter = OSIToSMLConverter()
        sml = converter.from_osi(
            osi_model_with_short_aliases,
            metric_overrides=overrides,
            override_alias_map=override_alias_map,
        )

        sply_metric = next(m for m in sml.metrics if m.unique_name == "Revenue SPLY")
        budget_metric = next(m for m in sml.metrics if m.unique_name == "Revenue Budget")

        for metric in (sply_metric, budget_metric):
            assert metric.sql_expression is not None
            expr = metric.sql_expression
            # No uppercase short prefixes followed by a dot
            assert "FACT." not in expr, f"FACT. found in: {expr}"
            assert "PRODUCT." not in expr, f"PRODUCT. found in: {expr}"
            assert "CALENDAR." not in expr, f"CALENDAR. found in: {expr}"
            assert "SCENARIO." not in expr, f"SCENARIO. found in: {expr}"
            # Correct lowercase aliases should be present
            assert "factsales." in expr or "fact_sales." not in expr  # alias not physical
            assert 'factsales."REVENUE"' in expr or 'factsales."REVENUE"' in expr

    def test_override_inferred_without_explicit_alias_map(
        self, osi_model_with_short_aliases: OSIModel
    ) -> None:
        """Auto-inference: FACT is inferred to Fact_Sales via substring match
        even without an explicit override_alias_map."""
        overrides = {"Revenue SPLY": 'SUM(FACT."REVENUE")'}

        converter = OSIToSMLConverter()
        sml = converter.from_osi(
            osi_model_with_short_aliases,
            metric_overrides=overrides,
            # No override_alias_map — rely on auto-inference
        )

        metric = next(m for m in sml.metrics if m.unique_name == "Revenue SPLY")
        assert metric.sql_expression is not None
        # FACT. must not appear in the sanitized result
        assert "FACT." not in metric.sql_expression


class TestEmitterDDLValidation:
    """Generated DDL passes validate_semantic_view_sql after override sanitization."""

    def _build_behavior(self, overrides: dict, alias_map: dict) -> ConnectorBehavior:
        behavior = ConnectorBehavior()
        behavior.semantic_model = SemanticModelBehavior(
            metric_overrides=overrides,
            override_alias_map=alias_map,
        )
        return behavior

    def test_ddl_passes_validation_with_short_alias_overrides(
        self,
        snowflake_config: SnowflakeConfig,
        osi_model_with_short_aliases: OSIModel,
    ) -> None:
        """Full pipeline: OSI → SML → DDL must pass validate_semantic_view_sql."""
        overrides = {
            "Revenue SPLY": (
                'LAG(SUM(FACT."REVENUE"), 12) OVER '
                '(PARTITION BY PRODUCT."PRODUCT KEY" ORDER BY CALENDAR."YEARPERIOD")'
            ),
            "Revenue Budget": (
                "SUM(CASE WHEN SCENARIO.\"SCENARIO\" = 'Budget' "
                "THEN FACT.\"REVENUE\" ELSE 0 END)"
            ),
        }
        alias_map = {
            "FACT": "Fact_Sales",
            "PRODUCT": "ProductDim",
            "CALENDAR": "CalendarDim",
            "SCENARIO": "ScenarioDim",
        }

        behavior = self._build_behavior(overrides, alias_map)
        converter = OSIToSMLConverter()
        sml = converter.from_osi(
            osi_model_with_short_aliases,
            metric_overrides=overrides,
            override_alias_map=alias_map,
        )

        emitter = SnowflakeEmitter(snowflake_config, behavior=behavior)
        ddl = emitter._generate_semantic_view(sml)

        # The DDL must be a non-empty string
        assert "CREATE OR REPLACE SEMANTIC VIEW" in ddl

        # Build dataset_aliases as the emitter would
        dataset_aliases = {
            ds.unique_name: __import__(
                "semabridge.utils.naming", fromlist=["to_alias"]
            ).to_alias(ds.unique_name)
            for ds in sml.datasets
        }
        result = validate_semantic_view_sql(
            ddl=ddl,
            dataset_aliases=dataset_aliases,
            logical_names=[ds.unique_name for ds in sml.datasets],
            database=snowflake_config.database,
            schema_name=snowflake_config.schema_name,
        )

        assert result.valid, (
            "DDL validation failed.\nErrors:\n"
            + "\n".join(result.errors)
            + "\nDDL:\n"
            + ddl
        )

    def test_no_uppercase_alias_dot_in_ddl(
        self,
        snowflake_config: SnowflakeConfig,
        osi_model_with_short_aliases: OSIModel,
    ) -> None:
        """After conversion, the generated DDL must not contain UPPERCASE. patterns
        in the METRICS body (the exact pattern the validator flags)."""
        import re

        overrides = {
            "Revenue SPLY": 'LAG(SUM(FACT."REVENUE"), 12) OVER (ORDER BY CALENDAR."YEARPERIOD")',
        }
        alias_map = {"FACT": "Fact_Sales", "CALENDAR": "CalendarDim"}

        behavior = self._build_behavior(overrides, alias_map)
        converter = OSIToSMLConverter()
        sml = converter.from_osi(
            osi_model_with_short_aliases,
            metric_overrides=overrides,
            override_alias_map=alias_map,
        )

        emitter = SnowflakeEmitter(snowflake_config, behavior=behavior)
        ddl = emitter._generate_semantic_view(sml)

        # Extract METRICS body and scan for uppercase prefix pattern
        metrics_match = re.search(r"METRICS\s*\((.*?)\)", ddl, re.DOTALL)
        if metrics_match:
            metrics_body = metrics_match.group(1)
            bad_patterns = re.findall(r"\b([A-Z][A-Z0-9_]+)\.(?!\")", metrics_body)
            assert not bad_patterns, (
                f"Uppercase prefix(es) found in METRICS: {bad_patterns}\n"
                f"DDL:\n{ddl}"
            )
