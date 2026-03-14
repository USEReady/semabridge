"""
Tests for MeasureTriage — the Triage Protocol classifier.

Validates that DAX expressions are correctly classified into
Tier 1 (Passthrough), Tier 2 (Aligned History), or Tier 3 (Decomposition).
"""

import pytest

from semabridge.converter.measure_triage import (
    MaterializationStrategy,
    MeasureTriage,
    TriageResult,
)
from semabridge.formats.sml.models import SMLMetric


@pytest.fixture
def triage() -> MeasureTriage:
    return MeasureTriage()


def _make_metric(name: str, expression: str, **kwargs) -> SMLMetric:
    """Helper to build an SMLMetric with minimal required fields."""
    return SMLMetric(
        unique_name=name,
        dataset="Sales",
        expression=expression,
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: Additive Primitives
# ─────────────────────────────────────────────────────────────────────────────

class TestTier1Passthrough:
    """Tier 1 covers simple, additive single-column aggregations."""

    def test_sum_simple(self, triage: MeasureTriage) -> None:
        metric = _make_metric("Revenue", "SUM([Amount])")
        result = triage.classify(metric)
        assert result.tier == 1
        assert result.strategy == MaterializationStrategy.PASSTHROUGH

    def test_count_simple(self, triage: MeasureTriage) -> None:
        metric = _make_metric("OrderCount", "COUNT([OrderID])")
        result = triage.classify(metric)
        assert result.tier == 1
        assert result.strategy == MaterializationStrategy.PASSTHROUGH

    def test_average_simple(self, triage: MeasureTriage) -> None:
        metric = _make_metric("AvgPrice", "AVERAGE([UnitPrice])")
        result = triage.classify(metric)
        assert result.tier == 1
        assert result.strategy == MaterializationStrategy.PASSTHROUGH

    def test_min_simple(self, triage: MeasureTriage) -> None:
        metric = _make_metric("MinQty", "MIN([Quantity])")
        result = triage.classify(metric)
        assert result.tier == 1
        assert result.strategy == MaterializationStrategy.PASSTHROUGH

    def test_max_simple(self, triage: MeasureTriage) -> None:
        metric = _make_metric("MaxDiscount", "MAX([Discount])")
        result = triage.classify(metric)
        assert result.tier == 1
        assert result.strategy == MaterializationStrategy.PASSTHROUGH

    def test_sum_with_table_qualifier(self, triage: MeasureTriage) -> None:
        metric = _make_metric("Revenue", "SUM('Sales'[Amount])")
        result = triage.classify(metric)
        assert result.tier == 1
        assert result.strategy == MaterializationStrategy.PASSTHROUGH

    def test_empty_expression_defaults_to_tier1(self, triage: MeasureTriage) -> None:
        metric = _make_metric("EmptyMeasure", "")
        result = triage.classify(metric)
        assert result.tier == 1
        assert result.strategy == MaterializationStrategy.PASSTHROUGH


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2: Time Intelligence / CALCULATE
# ─────────────────────────────────────────────────────────────────────────────

class TestTier2AlignedHistory:
    """Tier 2 covers time-intelligence and CALCULATE with temporal context."""

    def test_sameperiodlastyear(self, triage: MeasureTriage) -> None:
        expr = "CALCULATE(SUM([Revenue]), SAMEPERIODLASTYEAR('Date'[Date]))"
        metric = _make_metric("Revenue_LY", expr)
        result = triage.classify(metric)
        assert result.tier == 2
        assert result.strategy == MaterializationStrategy.ALIGNED_HISTORY
        assert "_LY" in result.aligned_measures

    def test_totalytd(self, triage: MeasureTriage) -> None:
        expr = "TOTALYTD(SUM([Revenue]), 'Date'[Date])"
        metric = _make_metric("Revenue_YTD", expr)
        result = triage.classify(metric)
        assert result.tier == 2
        assert result.strategy == MaterializationStrategy.ALIGNED_HISTORY
        assert "_YTD" in result.aligned_measures

    def test_previousmonth(self, triage: MeasureTriage) -> None:
        expr = "CALCULATE([Sales], PREVIOUSMONTH('Date'[Date]))"
        metric = _make_metric("Sales_PM", expr)
        result = triage.classify(metric)
        assert result.tier == 2
        assert "_PM" in result.aligned_measures

    def test_dateadd(self, triage: MeasureTriage) -> None:
        expr = "CALCULATE([Revenue], DATEADD('Date'[Date], -1, YEAR))"
        metric = _make_metric("Revenue_PY", expr)
        result = triage.classify(metric)
        assert result.tier == 2
        assert result.strategy == MaterializationStrategy.ALIGNED_HISTORY


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3: Decomposition (Non-Additive / Ratios)
# ─────────────────────────────────────────────────────────────────────────────

class TestTier3Decomposition:
    """Tier 3 covers non-additive measures requiring component materialisation."""

    def test_divide_ratio(self, triage: MeasureTriage) -> None:
        metric = _make_metric("Margin", "DIVIDE([TotalProfit], [TotalRevenue])")
        result = triage.classify(metric)
        assert result.tier == 3
        assert result.strategy == MaterializationStrategy.DECOMPOSITION
        assert result.components.get("_Num") == "[TotalProfit]"
        assert result.components.get("_Denom") == "[TotalRevenue]"

    def test_simple_division_ratio(self, triage: MeasureTriage) -> None:
        metric = _make_metric("AOV", "[TotalSales] / [TotalOrders]")
        result = triage.classify(metric)
        assert result.tier == 3
        assert result.components.get("_Num") == "[TotalSales]"
        assert result.components.get("_Denom") == "[TotalOrders]"

    def test_distinctcount(self, triage: MeasureTriage) -> None:
        metric = _make_metric("UniqueCustomers", "DISTINCTCOUNT([CustomerID])")
        result = triage.classify(metric)
        assert result.tier == 3
        assert result.strategy == MaterializationStrategy.DECOMPOSITION

    def test_if_expression(self, triage: MeasureTriage) -> None:
        metric = _make_metric("ConditionalSales", "IF([Revenue] > 100, [Revenue], 0)")
        result = triage.classify(metric)
        assert result.tier == 3

    def test_switch_expression(self, triage: MeasureTriage) -> None:
        metric = _make_metric("StatusBucket", "SWITCH([Status], 1, 'Active', 'Other')")
        result = triage.classify(metric)
        assert result.tier == 3

    def test_var_return(self, triage: MeasureTriage) -> None:
        expr = "VAR _total = SUM([Amount]) RETURN _total * 1.1"
        metric = _make_metric("AdjustedTotal", expr)
        result = triage.classify(metric)
        assert result.tier == 3

    def test_unrecognised_defaults_to_tier3(self, triage: MeasureTriage) -> None:
        metric = _make_metric("Complex", "SUMX(FILTER('Sales', [Qty] > 10), [Amount])")
        result = triage.classify(metric)
        assert result.tier == 3
        assert result.strategy == MaterializationStrategy.DECOMPOSITION


# ─────────────────────────────────────────────────────────────────────────────
# Batch classification
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyAll:
    """Tests for classify_all batch operation."""

    def test_mixed_batch(self, triage: MeasureTriage) -> None:
        metrics = [
            _make_metric("Revenue", "SUM([Amount])"),
            _make_metric("Revenue_YTD", "TOTALYTD(SUM([Revenue]), 'Date'[Date])"),
            _make_metric("Margin", "DIVIDE([Profit], [Revenue])"),
        ]
        results = triage.classify_all(metrics)

        assert len(results) == 3
        assert results["Revenue"].tier == 1
        assert results["Revenue_YTD"].tier == 2
        assert results["Margin"].tier == 3

    def test_empty_list(self, triage: MeasureTriage) -> None:
        results = triage.classify_all([])
        assert results == {}
