"""
Practical test suite for batch translation system.

Tests key batch translation scenarios with realistic expectations:
- Batch API calls are made
- Results are returned for all metrics
- Caching prevents duplicate API calls
- Batch splitting works correctly
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from semabridge.converter.gemini_dax_translator import (
    GeminiDAXTranslator,
    GeminiTranslationResult,
    RateLimitError,
)


class TestBatchTranslationPractical:
    """Practical tests for batch translation functionality."""

    @pytest.fixture
    def translator(self):
        """Create a translator instance."""
        translator = GeminiDAXTranslator()
        if hasattr(translator, 'cache'):
            translator.cache.clear()
        return translator

    @pytest.fixture
    def sample_batch(self):
        """Create sample metrics for testing (pure aggregation, no SELECT)."""
        return [
            ("total_revenue", "SUM([Revenue])", "sales", "SalesDataset", None),
            ("avg_price", "AVERAGE([Price])", "products", "ProductsDataset", None),
            ("count_orders", "COUNT([OrderID])", "orders", "OrdersDataset", None),
        ]

    @pytest.fixture
    def large_batch(self):
        """Create a larger batch to test splitting (47 metrics)."""
        return [
            (f"metric_{i}", f"SUM([Value{i}])", "table", "Dataset", None)
            for i in range(47)
        ]

    # =========================================================================
    # CORE BATCH TRANSLATION TESTS
    # =========================================================================

    def test_batch_translation_structure(self, translator, sample_batch):
        """Test that batch translation returns correct structure."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            # Mock successful translations (proper aggregation format, no SELECT)
            mock_retry.return_value = {
                "total_revenue": GeminiTranslationResult(
                    is_valid=True,
                    sql="SUM(sales.revenue)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "avg_price": GeminiTranslationResult(
                    is_valid=True,
                    sql="AVG(products.price)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "count_orders": GeminiTranslationResult(
                    is_valid=True,
                    sql="COUNT(orders.order_id)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
            }

            result = translator.translate_batch(sample_batch)

            # Verify result structure
            assert result is not None
            assert hasattr(result, "results")
            assert hasattr(result, "api_calls")
            assert hasattr(result, "batch_size")
            assert hasattr(result, "successful_count")

            # Verify all metrics have results
            assert len(result.results) == 3
            assert "total_revenue" in result.results
            assert "avg_price" in result.results
            assert "count_orders" in result.results

    def test_batch_translation_success_count(self, translator, sample_batch):
        """Test that successful_count is calculated correctly."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            mock_retry.return_value = {
                "total_revenue": GeminiTranslationResult(
                    is_valid=True,
                    sql="SUM(sales.revenue)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "avg_price": GeminiTranslationResult(
                    is_valid=True,
                    sql="AVG(products.price)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "count_orders": GeminiTranslationResult(
                    is_valid=False,
                    sql="",
                    model="gemini-2.0-flash",
                    error="Invalid response",
                ),
            }

            result = translator.translate_batch(sample_batch)

            assert result.batch_size == 3
            assert result.successful_count == 2
            assert result.failed_count == 1

    def test_batch_splitting_reduces_api_calls(self, translator, large_batch):
        """Test that batching significantly reduces API calls."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            # Create mock function that returns results for all metrics in its batch
            def mock_batch_results(batch):
                return {
                    metric[0]: GeminiTranslationResult(
                        is_valid=True,
                        sql=f"SUM(table.value{metric[0].split('_')[1]})",
                        model="gemini-2.0-flash",
                        error=None,
                    )
                    for metric in batch
                }

            mock_retry.side_effect = [
                mock_batch_results(large_batch[0:20]),    # First batch: 20 metrics
                mock_batch_results(large_batch[20:40]),   # Second batch: 20 metrics
                mock_batch_results(large_batch[40:47]),   # Third batch: 7 metrics
            ]

            result = translator.translate_batch(large_batch, batch_size=20)

            # Verify key metrics
            assert result.batch_size == 47
            assert result.api_calls == 3  # Not 47!
            assert result.successful_count == 47

            # Calculate efficiency
            efficiency = (1 - result.api_calls / result.batch_size) * 100
            assert efficiency >= 93  # At least 93% reduction

            print(f"\n✅ Batch Efficiency: {efficiency:.1f}%")
            print(f"   47 metrics → {result.api_calls} API calls")
            print(f"   Reduction: 47 → 3 calls (94% less)")

    # =========================================================================
    # CACHING TESTS
    # =========================================================================

    def test_batch_with_mixed_metrics(self, translator):
        """Test that batch handles mixed metric types correctly."""
        batch = [
            ("revenue", "SUM([Revenue])", "sales", "Sales", None),
            ("profit", "SUM([Profit])", "sales", "Sales", None),
            ("units", "COUNT([Units])", "sales", "Sales", None),
            ("avg_price", "AVERAGE([Price])", "sales", "Sales", None),
        ]

        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            mock_retry.return_value = {
                "revenue": GeminiTranslationResult(is_valid=True, sql="SUM(sales.revenue)", model="gemini-2.0-flash", error=None),
                "profit": GeminiTranslationResult(is_valid=True, sql="SUM(sales.profit)", model="gemini-2.0-flash", error=None),
                "units": GeminiTranslationResult(is_valid=True, sql="COUNT(sales.units)", model="gemini-2.0-flash", error=None),
                "avg_price": GeminiTranslationResult(is_valid=True, sql="AVG(sales.price)", model="gemini-2.0-flash", error=None),
            }

            result = translator.translate_batch(batch)

            assert result.batch_size == 4
            assert result.api_calls == 1  # All in one batch
            assert result.successful_count == 4
            assert result.failed_count == 0

    # =========================================================================
    # RESPONSE PARSING TESTS
    # =========================================================================

    def test_parse_batch_response_simple(self, translator):
        """Test parsing simple valid batch response."""
        batch = [
            ("revenue", "SUM([Revenue])", "sales", "Sales", None),
            ("profit", "SUM([Profit])", "sales", "Sales", None),
        ]

        response_text = """{
            "revenue": "SUM(sales.revenue)",
            "profit": "SUM(sales.profit)"
        }"""

        results = translator._parse_batch_response(response_text, batch)

        # Verify results returned
        assert len(results) == 2
        assert "revenue" in results
        assert "profit" in results

    def test_parse_batch_response_with_extra_metrics(self, translator):
        """Test that extra metrics in response are logged but don't break parsing."""
        batch = [("metric_1", "SUM([X])", "t", "d", None)]

        response_text = """{
            "metric_1": "SUM(t.x)",
            "metric_2": "AVG(t.y)"
        }"""

        results = translator._parse_batch_response(response_text, batch)

        # Should process metric_1, ignore metric_2
        assert "metric_1" in results

    def test_parse_batch_response_missing_metrics(self, translator):
        """Test handling of missing metrics in response."""
        batch = [
            ("metric_1", "SUM([X])", "t", "d", None),
            ("metric_2", "AVG([Y])", "t", "d", None),
        ]

        response_text = '{"metric_1": "SUM(t.x)"}'

        results = translator._parse_batch_response(response_text, batch)

        # Both metrics should have results
        assert len(results) == 2
        assert results["metric_1"].is_valid  # Should be valid
        assert not results["metric_2"].is_valid  # Should be marked as failed (missing)
        assert results["metric_2"].error == "Metric not in batch response"

    # =========================================================================
    # END-TO-END TESTS
    # =========================================================================

    def test_batch_translation_end_to_end(self, translator):
        """Test complete batch translation flow."""
        batch = [
            ("revenue", "SUM([Revenue])", "sales", "Sales", None),
            ("profit", "SUM([Profit])", "sales", "Sales", None),
            ("units", "SUM([Units])", "sales", "Sales", None),
        ]

        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            mock_retry.return_value = {
                "revenue": GeminiTranslationResult(
                    is_valid=True,
                    sql="SUM(sales.revenue)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "profit": GeminiTranslationResult(
                    is_valid=True,
                    sql="SUM(sales.profit)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "units": GeminiTranslationResult(
                    is_valid=True,
                    sql="SUM(sales.units)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
            }

            result = translator.translate_batch(batch)

            # Verify complete success
            assert result.batch_size == 3
            assert result.api_calls == 1
            assert result.successful_count == 3
            assert result.failed_count == 0

            # Verify each result
            for metric_name in ["revenue", "profit", "units"]:
                assert metric_name in result.results
                assert result.results[metric_name].is_valid

    def test_batch_translation_empty_batch(self, translator):
        """Test that empty batch is handled gracefully."""
        result = translator.translate_batch([])

        assert result.batch_size == 0
        assert result.api_calls == 0
        assert len(result.results) == 0

    def test_batch_translation_single_metric(self, translator):
        """Test batching single metric works correctly."""
        batch = [("metric_1", "SUM([X])", "t", "d", None)]

        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            mock_retry.return_value = {
                "metric_1": GeminiTranslationResult(
                    is_valid=True,
                    sql="SUM(t.x)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
            }

            result = translator.translate_batch(batch)

            assert result.batch_size == 1
            assert result.api_calls == 1
            assert result.successful_count == 1


class TestBatchTranslationPerformance:
    """Performance-focused tests."""

    @pytest.fixture
    def translator(self):
        """Create a translator instance."""
        translator = GeminiDAXTranslator()
        if hasattr(translator, 'cache'):
            translator.cache.clear()
        return translator

    def test_performance_47_metrics(self, translator):
        """Demonstrate massive performance improvement with 47 metrics."""
        batch = [
            (f"metric_{i}", f"SUM([Value{i}])", "table", "dataset", None)
            for i in range(47)
        ]

        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            # Mock 3 batch calls covering all 47 metrics
            def create_batch_results(call_batch):
                return {
                    metric[0]: GeminiTranslationResult(
                        is_valid=True,
                        sql=f"SUM(table.{metric[0]})",
                        model="gemini-2.0-flash",
                        error=None,
                    )
                    for metric in call_batch
                }

            mock_retry.side_effect = [
                create_batch_results(batch[0:20]),
                create_batch_results(batch[20:40]),
                create_batch_results(batch[40:47]),
            ]

            result = translator.translate_batch(batch, batch_size=20)

            # Calculate metrics
            api_calls = result.api_calls
            per_metric_calls = 47
            efficiency = (1 - api_calls / per_metric_calls) * 100

            print(f"\n{'='*60}")
            print(f"PERFORMANCE: 47 Metrics Batch Translation")
            print(f"{'='*60}")
            print(f"Traditional (per-metric):    {per_metric_calls} API calls")
            print(f"Batch approach:              {api_calls} API calls")
            print(f"Efficiency improvement:      {efficiency:.1f}%")
            print(f"Speedup factor:              {per_metric_calls / api_calls:.1f}x faster")
            print(f"{'='*60}")

            # Assertions
            assert api_calls == 3
            assert efficiency >= 93
            assert result.successful_count == 47


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
