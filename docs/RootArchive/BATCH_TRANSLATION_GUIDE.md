# Batch Translation Integration Guide

## Overview

The `GeminiDAXTranslator` now supports batch translation to reduce API calls by ~90%.

**Before:** 47 metrics → 47 API calls  
**After:** 47 metrics → 2-3 API calls (batches of 20)

---

## Batch Translation Method

### `translate_batch()` Signature

```python
def translate_batch(self,
                   metrics: List[Tuple[str, str, str, str, Optional[Dict]]] = None,
                   batch_size: int = 20) -> GeminiBatchTranslationResult:
    """
    Args:
        metrics: List of (metric_name, dax, table_alias, dataset_name, schema_context) tuples
        batch_size: Number of metrics per API request (default: 20)
    
    Returns:
        GeminiBatchTranslationResult containing:
        - results: Dict[metric_name -> GeminiTranslationResult]
        - api_calls: Number of API calls made
        - retry_attempts: Retry attempts for rate limits
        - batch_size: Total metrics processed
        - cached_count: Metrics from cache
        - successful_count: Valid translations
        - failed_count: Failed translations
    """
```

---

## Usage Example

### Collecting Metrics for Batch Translation

```python
from semabridge.converter.gemini_dax_translator import get_gemini_translator

# Collect metrics that need LLM translation
translator = get_gemini_translator()

# Prepare batch: List of (name, dax, alias, dataset, schema_context)
batch_metrics = []
for metric in sml_model.metrics:
    # Only include metrics needing LLM translation
    if needs_llm_translation(metric):
        batch_metrics.append((
            metric.unique_name,
            metric.expression,
            "salesfact",  # table alias
            metric.dataset,
            None  # schema_context
        ))

# Translate all at once
batch_result = translator.translate_batch(batch_metrics, batch_size=20)

# Process results
for metric_name, translation_result in batch_result.results.items():
    if translation_result.is_valid:
        # Store SQL expression
        sml_model.metrics[metric_name].sql_expression = translation_result.sql
        print(f"✓ {metric_name}: {translation_result.sql}")
    else:
        # Log failure
        print(f"✗ {metric_name}: {translation_result.error}")

print(f"\nStats: {batch_result.api_calls} API calls for {batch_result.batch_size} metrics")
```

---

## Integration Points

### 1. DAXTranslator Integration

In `src/semabridge/converter/dax_translator.py`, replace individual LLM calls with batch:

**Before:**
```python
for metric in metrics_needing_llm:
    result = translator.translate(metric.expression, alias, dataset, metric.unique_name)
    metric.sql_expression = result.sql
```

**After:**
```python
# Collect all metrics needing LLM translation
metrics_batch = [
    (m.unique_name, m.expression, alias, dataset, None)
    for m in metrics_needing_llm
]

# Batch translate (1 API call for ~20 metrics)
batch_result = translator.translate_batch(metrics_batch)

# Apply results
for metric_name, result in batch_result.results.items():
    in_model = model.find_metric_by_name(metric_name)
    if in_model and result.is_valid:
        in_model.sql_expression = result.sql
```

---

## Logging Output

### Batch Translation Logs

```
INFO     🔄 Starting batch translation for 47 metrics (batch size: 20)
INFO        ├─ Cached: 5
INFO        ├─ To Translate: 42
INFO     📦 Processing batch 1/3 (20 metrics)
INFO     📦 Processing batch 2/3 (20 metrics)
INFO     📦 Processing batch 3/3 (2 metrics)
INFO     ✅ Batch translation complete:
INFO        ├─ Total metrics: 47
INFO        ├─ API calls: 3 (vs 47 without batching = 94% reduction)
INFO        ├─ Cached: 5
INFO        ├─ Successful: 40
INFO        └─ Failed: 2
```

---

## Rate Limit Handling

### Automatic Retry with Exponential Backoff

When a 429 (Too Many Requests) error occurs:

1. **First retry after 5 seconds**
2. **Second retry after 10 seconds**
3. **Third retry after 20 seconds**

```
WARNING  ⏳ Rate limit hit. Retrying in 10s (attempt 2/3)
DEBUG    Calling models/gemini-2.5-flash (attempt 2/3)
DEBUG    ✅ Batch translation succeeded with models/gemini-2.5-flash
```

---

## Performance Improvements

### Comparison: Single vs Batch

| Scenario | Single Calls | Batch Calls | Reduction |
|----------|--------------|-------------|-----------|
| 47 metrics | 47 calls | 3 calls | **94%** |
| 100 metrics | 100 calls | 5 calls | **95%** |
| 200 metrics | 200 calls | 10 calls | **95%** |

### API Quota Savings

Using Gemini free tier (1,500 requests/minute):

- **Before:** 47 metrics = 47 requests ✅ (within limit)
- **After:** 47 metrics = 3 requests ✅ (much more overhead capacity)

For larger datasets:
- **Before:** 200 metrics = 200 requests ✅ (close to limit)
- **After:** 200 metrics = 10 requests ✅ (plenty of capacity for other uses)

---

## Caching

Batch translation includes cache checking:

1. **Check cache first** for all metrics
2. **Only translate uncached** metrics
3. **Cache all successful** results

Example:
```
INFO     🔄 Starting batch translation for 50 metrics
INFO        ├─ Cached: 20 ← Reused from cache
INFO        ├─ To Translate: 30 ← Need API calls
INFO     📦 Processing batch 1/2 (20 metrics)
INFO     📦 Processing batch 2/2 (10 metrics)
INFO     ✅ API calls: 2 (vs 30 uncached metrics)
```

---

## Error Handling

### Parse Errors

If Gemini response is not valid JSON:
```
ERROR    Failed to parse batch response: JSON decode error
WARNING  ❌ [metric_name] Not in response
```

### Rate Limits

If all retries fail:
```
ERROR    ❌ Rate limit persisted after 3 attempts
```

### Validation Errors

If SQL is invalid (contains SELECT, dangerous patterns):
```
WARNING  ⚠️  [metric_name] Invalid: SELECT SUM(...) FROM...
```

---

## Configuration

### Batch Size

Default is 20 metrics per request. Can be customized:

```python
# Smaller batches (more API calls, less per-request time)
batch_result = translator.translate_batch(metrics, batch_size=10)

# Larger batches (fewer API calls, more per-request time)
batch_result = translator.translate_batch(metrics, batch_size=50)
```

**Recommended:** 20 metrics per batch (good balance)

### Retry Configuration

Currently hardcoded to 3 retries with exponential backoff (5s, 10s, 20s).  
To customize, modify `_translate_batch_with_retry()` method.

---

## Testing Batch Translation

### Unit Test

```python
# test_batch_translation.py
import pytest
from semabridge.converter.gemini_dax_translator import GeminiDAXTranslator

def test_batch_translation():
    translator = GeminiDAXTranslator()
    
    # Sample batch
    batch = [
        ("total_revenue", "SUM([Amount])", "sales", "SalesDataset", None),
        ("avg_quantity", "AVERAGE([Quantity])", "sales", "SalesDataset", None),
        ("count_orders", "COUNT([OrderID])", "sales", "SalesDataset", None),
    ]
    
    result = translator.translate_batch(batch)
    
    assert result.api_calls <= 1
    assert result.batch_size == 3
    assert result.successful_count >= 2
```

### Manual Test

```python
from semabridge.converter.gemini_dax_translator import get_gemini_translator

translator = get_gemini_translator()

batch = [
    ("total_sales", "SUM([Sales])", "fact", "Sales", None),
    ("avg_price", "AVERAGE([Price])", "fact", "Sales", None),
]

result = translator.translate_batch(batch)
print(f"Successful: {result.successful_count}")
print(f"Failed: {result.failed_count}")
print(f"API Calls: {result.api_calls}")
```

---

## Future Enhancements

1. **Parallel batch processing** - Process multiple batches concurrently
2. **Adaptive batch sizing** - Reduce batch size if hitting size limits
3. **Metrics grouping** - Group similar metrics for better LLM context
4. **Incremental caching** - Cache intermediate results while processing batches

---

## Troubleshooting

### "GEMINI_API_KEY not configured"
Check your `.env` file has `GEMINI_API_KEY=...`

### "All metrics from cache (0 API calls)"
This is expected if metrics were translated before. Remove `.llm_dax_cache.json` to force retranslation.

### Rate limits still hit after retries
- Use paid Gemini API tier for higher quotas
- Reduce batch size to spread load over time
- Implement longer retry wait times

---

## Summary

✅ **90% reduction in API calls** through intelligent batching  
✅ **Automatic cache reuse** to avoid redundant translations  
✅ **Robust rate limit handling** with exponential backoff  
✅ **Comprehensive logging** for monitoring and debugging  
✅ **Easy integration** into existing DAX translation pipeline  

The batch translation system dramatically improves performance and stability for large-scale metric translation!
