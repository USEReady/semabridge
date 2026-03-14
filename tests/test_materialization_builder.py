"""
Tests for MaterializationQueryBuilder.

Validates that the builder produces correct SUMMARIZECOLUMNS DAX queries
based on MeasureTriage results for Tier 1, 2, and 3 measures.
"""

import pytest

from semabridge.converter.materialization_builder import (
    MaterializationQueryBuilder,
)
from semabridge.converter.measure_triage import (
    MaterializationStrategy,
    MeasureTriage,
    TriageResult,
)
from semabridge.formats.sml.models import SMLMetric


@pytest.fixture
def builder() -> MaterializationQueryBuilder:
    return MaterializationQueryBuilder()


@pytest.fixture
def triage() -> MeasureTriage:
    return MeasureTriage()


def _make_metric(name: str, expression: str, **kwargs) -> SMLMetric:
    return SMLMetric(
        unique_name=name,
        dataset="Sales",
        expression=expression,
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Basic query generation
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildQuery:
    """Tests for single-query generation."""

    def test_tier1_produces_single_column(
        self, builder: MaterializationQueryBuilder, triage: MeasureTriage
    ) -> None:
        metrics = [_make_metric("Revenue", "SUM([Amount])")]
        results = triage.classify_all(metrics)

        query = builder.build_query(metrics, results, ["'Date'[Year]"])

        assert "SUMMARIZECOLUMNS" in query
        assert '"Revenue"' in query
        assert "[Revenue]" in query

    def test_tier2_produces_base_and_aligned(
        self, builder: MaterializationQueryBuilder, triage: MeasureTriage
    ) -> None:
        expr = "CALCULATE(SUM([Revenue]), SAMEPERIODLASTYEAR('Date'[Date]))"
        metrics = [_make_metric("Revenue_LY", expr)]
        results = triage.classify_all(metrics)

        query = builder.build_query(metrics, results, ["'Date'[Year]"])

        assert "SUMMARIZECOLUMNS" in query
        assert '"Revenue_LY"' in query
        assert '"Revenue_LY_LY"' in query  # aligned companion

    def test_tier3_with_components(
        self, builder: MaterializationQueryBuilder, triage: MeasureTriage
    ) -> None:
        metrics = [_make_metric("Margin", "DIVIDE([Profit], [Revenue])")]
        results = triage.classify_all(metrics)

        query = builder.build_query(metrics, results, ["'Date'[Year]"])

        assert "SUMMARIZECOLUMNS" in query
        assert '"Margin_Num"' in query
        assert '"Margin_Denom"' in query

    def test_mixed_tiers(
        self, builder: MaterializationQueryBuilder, triage: MeasureTriage
    ) -> None:
        metrics = [
            _make_metric("Revenue", "SUM([Amount])"),
            _make_metric("Revenue_YTD", "TOTALYTD(SUM([Revenue]), 'Date'[Date])"),
            _make_metric("Margin", "DIVIDE([Profit], [Revenue])"),
        ]
        results = triage.classify_all(metrics)

        query = builder.build_query(
            metrics, results, ["'Date'[Year]", "'Product'[Category]"]
        )

        assert "SUMMARIZECOLUMNS" in query
        # Tier 1
        assert '"Revenue"' in query
        # Tier 2 aligned
        assert '"Revenue_YTD"' in query
        # Tier 3 components
        assert '"Margin_Num"' in query
        assert '"Margin_Denom"' in query
        # Both dimensions present
        assert "'Date'[Year]" in query
        assert "'Product'[Category]" in query

    def test_empty_grain_raises(
        self, builder: MaterializationQueryBuilder, triage: MeasureTriage
    ) -> None:
        metrics = [_make_metric("Revenue", "SUM([Amount])")]
        results = triage.classify_all(metrics)

        with pytest.raises(ValueError, match="grain_dimensions"):
            builder.build_query(metrics, results, [])

    def test_no_metrics_returns_empty(
        self, builder: MaterializationQueryBuilder
    ) -> None:
        query = builder.build_query([], {}, ["'Date'[Year]"])
        assert query == ""


# ─────────────────────────────────────────────────────────────────────────────
# Partitioned queries
# ─────────────────────────────────────────────────────────────────────────────

class TestPartitionedQueries:
    """Tests for build_queries_partitioned."""

    def test_splits_into_batches(
        self, builder: MaterializationQueryBuilder, triage: MeasureTriage
    ) -> None:
        metrics = [_make_metric(f"M{i}", "SUM([Amount])") for i in range(5)]
        results = triage.classify_all(metrics)

        queries = builder.build_queries_partitioned(
            metrics, results, ["'Date'[Year]"], max_measures_per_query=2
        )

        assert len(queries) == 3  # 5 metrics / 2 per query = 3 queries

    def test_single_batch_when_under_limit(
        self, builder: MaterializationQueryBuilder, triage: MeasureTriage
    ) -> None:
        metrics = [_make_metric(f"M{i}", "SUM([Amount])") for i in range(3)]
        results = triage.classify_all(metrics)

        queries = builder.build_queries_partitioned(
            metrics, results, ["'Date'[Year]"], max_measures_per_query=20
        )

        assert len(queries) == 1
