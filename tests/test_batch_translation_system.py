"""
Comprehensive test suite for batch translation system.

Tests the batch translation capability added to GeminiDAXTranslator:
- Batch processing (multiple metrics in one API call)
- Cache reuse across batches
- Batch splitting (max metrics per API call)
- Rate limit retry logic
- Response parsing and validation
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from semabridge.converter.gemini_dax_translator import (
    GeminiDAXTranslator,
    GeminiTranslationResult,
    RateLimitError,
)


class TestBatchTranslationSystem:
    """Tests for batch translation functionality."""

    @pytest.fixture
    def translator(self):
        """Create a translator instance with mocked Gemini client."""
        translator = GeminiDAXTranslator()
        # Clear cache before each test
        if hasattr(translator, 'cache'):
            translator.cache.clear()
        return translator

    @pytest.fixture
    def sample_batch(self):
        """Create sample metrics for testing."""
        return [
            ("total_revenue", "SUM([Revenue])", "sales", "SalesDataset", None),
            ("avg_price", "AVERAGE([Price])", "products", "ProductsDataset", None),
            ("count_orders", "COUNT([OrderID])", "orders", "OrdersDataset", None),
        ]

    @pytest.fixture
    def large_batch(self):
        """Create a larger batch to test splitting."""
        return [
            (f"metric_{i}", f"SUM([Value{i}])", "table", "Dataset", None)
            for i in range(47)
        ]

    # =========================================================================
    # BASIC BATCH TRANSLATION TESTS
    # =========================================================================

    def test_batch_translation_returns_result(self, translator, sample_batch):
        """Test that batch translation returns a GeminiBatchTranslationResult."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            mock_retry.return_value = {
                "total_revenue": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT SUM(revenue) FROM sales",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "avg_price": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT AVG(price) FROM products",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "count_orders": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT COUNT(order_id) FROM orders",
                    model="gemini-2.0-flash",
                    error=None,
                ),
            }

            result = translator.translate_batch(sample_batch)

            assert result is not None
            assert hasattr(result, "results")
            assert hasattr(result, "api_calls")
            assert hasattr(result, "batch_size")
            assert hasattr(result, "successful_count")

    def test_batch_translation_successful_count(self, translator, sample_batch):
        """Test that successful_count is calculated correctly."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            mock_retry.return_value = {
                "total_revenue": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT SUM(revenue)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "avg_price": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT AVG(price)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "count_orders": GeminiTranslationResult(
                    is_valid=False,
                    sql=None,
                    model="gemini-2.0-flash",
                    error="Invalid response",
                ),
            }

            result = translator.translate_batch(sample_batch)

            assert result.batch_size == 3
            assert result.successful_count == 2
            assert result.failed_count == 1

    # =========================================================================
    # CACHING TESTS
    # =========================================================================

    def test_cache_hit_reduces_api_calls(self, translator, sample_batch):
        """Test that cached metrics don't generate API calls."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            mock_retry.return_value = {
                "total_revenue": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT SUM(revenue)",
                    model="gemini-2.0-flash",
                    error=None,
                ),
            }

            # First call - should hit API
            result1 = translator.translate_batch(
                [sample_batch[0]]
            )  # Just first metric
            assert mock_retry.call_count == 1

            # Second call - should use cache
            result2 = translator.translate_batch(
                [sample_batch[0]]
            )  # Same metric again
            assert mock_retry.call_count == 1  # No additional call

            # Verify cached result is returned
            assert result2.cached_count == 1
            assert result2.api_calls == 0

    def test_partial_cache_hit(self, translator, sample_batch):
        """Test that partially cached batches only query non-cached metrics."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            # Return different results for different calls
            mock_retry.side_effect = [
                {
                    "total_revenue": GeminiTranslationResult(
                        is_valid=True,
                        sql="SELECT SUM(revenue)",
                        model="gemini-2.0-flash",
                        error=None,
                    ),
                },
                {
                    "avg_price": GeminiTranslationResult(
                        is_valid=True,
                        sql="SELECT AVG(price)",
                        model="gemini-2.0-flash",
                        error=None,
                    ),
                    "count_orders": GeminiTranslationResult(
                        is_valid=True,
                        sql="SELECT COUNT(order_id)",
                        model="gemini-2.0-flash",
                        error=None,
                    ),
                },
            ]

            # Cache first metric
            result1 = translator.translate_batch(
                [sample_batch[0]]
            )  # Cache total_revenue
            assert mock_retry.call_count == 1

            # Request all three - should query only the 2 uncached
            result2 = translator.translate_batch(sample_batch)
            assert mock_retry.call_count == 2  # One more call for 2 new metrics

            # Verify statistics
            assert result2.cached_count == 1  # total_revenue from cache
            assert result2.results[
                "total_revenue"
            ]  # Should have result (from cache)

    # =========================================================================
    # BATCH SPLITTING TESTS
    # =========================================================================

    def test_batch_splitting_with_default_size(self, translator, large_batch):
        """Test that batches are split at default batch_size."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            # Setup return values for each batch call
            mock_retry.side_effect = [
                {f"metric_{i}": GeminiTranslationResult(
                    is_valid=True,
                    sql=f"SELECT SUM(value{i})",
                    model="gemini-2.0-flash",
                    error=None,
                ) for i in range(0, 20)},  # First 20
                {f"metric_{i}": GeminiTranslationResult(
                    is_valid=True,
                    sql=f"SELECT SUM(value{i})",
                    model="gemini-2.0-flash",
                    error=None,
                ) for i in range(20, 40)},  # Next 20
                {f"metric_{i}": GeminiTranslationResult(
                    is_valid=True,
                    sql=f"SELECT SUM(value{i})",
                    model="gemini-2.0-flash",
                    error=None,
                ) for i in range(40, 47)},  # Last 7
            ]

            result = translator.translate_batch(large_batch, batch_size=20)

            # Should have made 3 API calls (47 metrics, batch_size=20)
            # 47 / 20 = 2.35, so ceil(2.35) = 3 calls
            assert mock_retry.call_count == 3

            # Verify statistics
            assert result.batch_size == 47
            assert result.api_calls == 3

    def test_batch_splitting_efficiency(self, translator, large_batch):
        """Test the efficiency gain from batching."""
        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            # Create a simple mock that returns all results
            def return_all_results(batch):
                return {
                    metric[0]: GeminiTranslationResult(
                        is_valid=True,
                        sql=f"SELECT SUM({metric[0]})",
                        model="gemini-2.0-flash",
                        error=None,
                    )
                    for metric in batch
                }

            mock_retry.side_effect = [
                return_all_results(large_batch[0:20]),
                return_all_results(large_batch[20:40]),
                return_all_results(large_batch[40:47]),
            ]

            result = translator.translate_batch(large_batch, batch_size=20)

            # Efficiency = 1 - (api_calls / batch_size)
            efficiency = (1 - result.api_calls / result.batch_size) * 100
            assert efficiency > 93  # 47 metrics, 3 API calls

            print(f"Batch Efficiency: {efficiency:.1f}%")
            print(f"API Calls: {result.api_calls} (vs {result.batch_size} per-metric)")

    # =========================================================================
    # RATE LIMIT RETRY TESTS
    # =========================================================================

    def test_rate_limit_retry_exponential_backoff(self, translator):
        """Test that rate limit retries use exponential backoff."""
        batch = [
            ("metric_1", "SUM([Value])", "table", "Dataset", None),
        ]

        with patch.object(
            translator, "_translate_batch_with_retry", wraps=translator._translate_batch_with_retry
        ) as mock_retry:
            with patch("time.sleep") as mock_sleep:
                with patch.object(translator, "_call_gemini_with_batch_prompt") as mock_api:
                    # Simulate: fail twice with 429, then succeed
                    mock_api.side_effect = [
                        RateLimitError("Rate limit exceeded"),
                        RateLimitError("Rate limit exceeded"),
                        {
                            "metric_1": {
                                "sql": "SELECT SUM(value)",
                                "valid": True,
                            }
                        },
                    ]

                    result = translator.translate_batch(batch)

                    # Verify sleep was called with exponential backoff times
                    sleep_calls = mock_sleep.call_args_list
                    # Should have sleep calls for backoff (5s, then 10s)
                    assert len(sleep_calls) >= 2

    # =========================================================================
    # RESPONSE PARSING TESTS
    # =========================================================================

    def test_parse_batch_response_valid_json(self, translator):
        """Test parsing of valid JSON batch response."""
        batch = [
            ("revenue_sum", "SUM([Revenue])", "sales", "Sales", None),
            ("avg_order_value", "AVERAGE([OrderValue])", "orders", "Orders", None),
        ]

        response_text = """{
            "revenue_sum": "SELECT SUM(revenue) FROM sales",
            "avg_order_value": "SELECT AVG(order_value) FROM orders"
        }"""

        results = translator._parse_batch_response(response_text, batch)

        assert len(results) == 2
        assert "revenue_sum" in results
        assert "avg_order_value" in results
        assert results["revenue_sum"].is_valid is True
        assert "SELECT SUM" in results["revenue_sum"].sql

    def test_parse_batch_response_with_markdown(self, translator):
        """Test that markdown in responses is stripped."""
        batch = [("metric_1", "SUM([X])", "t", "d", None)]

        response_text = """```json
        {
            "metric_1": "SELECT SUM(x) FROM t"
        }
        ```"""

        results = translator._parse_batch_response(response_text, batch)

        assert len(results) == 1
        assert results["metric_1"].is_valid is True

    def test_parse_batch_response_invalid_json(self, translator):
        """Test handling of invalid JSON in response."""
        batch = [("metric_1", "SUM([X])", "t", "d", None)]

        response_text = "This is not JSON at all!"

        results = translator._parse_batch_response(response_text, batch)

        # Should return empty or error results
        assert len(results) == 0 or any(not r.is_valid for r in results.values())

    def test_parse_batch_response_partial_results(self, translator):
        """Test handling when some metrics are missing from response."""
        batch = [
            ("metric_1", "SUM([X])", "t", "d", None),
            ("metric_2", "AVG([Y])", "t", "d", None),
        ]

        response_text = """{
            "metric_1": "SELECT SUM(x) FROM t"
        }"""

        results = translator._parse_batch_response(response_text, batch)

        # metric_1 should be valid, metric_2 missing
        assert results.get("metric_1", GeminiTranslationResult(False, None, "", "")).is_valid is True
        assert (
            results.get("metric_2", GeminiTranslationResult(False, None, "", "")).is_valid
            is False
        )

    # =========================================================================
    # INTEGRATION TESTS
    # =========================================================================

    def test_batch_translation_end_to_end(self, translator):
        """Test complete batch translation flow (without actual API calls)."""
        batch = [
            ("revenue", "SUM([Revenue])", "sales", "Sales", None),
            ("profit", "SUM([Profit])", "sales", "Sales", None),
            ("units", "SUM([Units])", "sales", "Sales", None),
        ]

        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            mock_retry.return_value = {
                "revenue": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT SUM(revenue) FROM sales",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "profit": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT SUM(profit) FROM sales",
                    model="gemini-2.0-flash",
                    error=None,
                ),
                "units": GeminiTranslationResult(
                    is_valid=True,
                    sql="SELECT SUM(units) FROM sales",
                    model="gemini-2.0-flash",
                    error=None,
                ),
            }

            result = translator.translate_batch(batch)

            # Verify results
            assert result.batch_size == 3
            assert result.api_calls == 1  # All 3 fit in 1 batch
            assert result.successful_count == 3
            assert result.failed_count == 0

    def test_compliance_with_original_translate_method(self, translator):
        """Test that original translate() method still works (backward compatibility)."""
        with patch.object(translator.client.models, "generate_content") as mock_api:
            mock_response = MagicMock()
            mock_response.text = "SELECT SUM(revenue) FROM sales"
            mock_api.return_value = mock_response

            # Original single metric translation should still work
            result = translator.translate("SUM([Revenue])", "sales", "Sales", "revenue")

            assert result is not None


class TestBatchTranslationPerformance:
    """Performance-focused tests for batch translation."""

    @pytest.fixture
    def translator(self):
        """Create a translator instance."""
        translator = GeminiDAXTranslator()
        if hasattr(translator, 'cache'):
            translator.cache.clear()
        return translator

    def test_performance_improvement_47_metrics(self, translator):
        """Demonstrate 90% API call reduction with 47 metrics."""
        batch = [
            (f"metric_{i}", f"SUM([Value{i}])", "table", "dataset", None)
            for i in range(47)
        ]

        with patch.object(translator, "_translate_batch_with_retry") as mock_retry:
            # Mock that creates proper results for all calls
            def create_batch_results(call_batch):
                return {
                    metric[0]: GeminiTranslationResult(
                        is_valid=True,
                        sql=f"SELECT SUM({metric[0]})",
                        model="gemini-2.0-flash",
                        error=None,
                    )
                    for metric in call_batch
                }

            # Simulate 3 batches of 20, 20, 7 metrics
            mock_retry.side_effect = [
                create_batch_results(batch[0:20]),
                create_batch_results(batch[20:40]),
                create_batch_results(batch[40:47]),
            ]

            result = translator.translate_batch(batch, batch_size=20)

            # Verify 90% reduction
            api_calls = result.api_calls
            per_metric_calls = 47  # Would be if not batched
            efficiency = (1 - api_calls / per_metric_calls) * 100

            print(f"\n{'='*60}")
            print(f"PERFORMANCE TEST: 47 Metrics")
            print(f"{'='*60}")
            print(f"Per-metric approach: {per_metric_calls} API calls")
            print(f"Batch approach:      {api_calls} API calls")
            print(f"Efficiency gain:     {efficiency:.1f}%")
            print(f"{'='*60}")

            assert api_calls == 3
            assert efficiency >= 90


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
