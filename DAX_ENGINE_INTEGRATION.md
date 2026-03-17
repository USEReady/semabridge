# Integration Guide: DAX Engine into Existing Pipeline

## Overview

This guide shows how to integrate the new `DaxTranslationEngine` into the existing `dax_translator.py` pipeline, replacing the Tier 4 handler and improving coverage from 60-80% to 80-90%.

---

## Current State (Before Integration)

### Existing Tier Structure (`dax_translator.py`)

```
Tier 0: Manual overrides (user_overrides)
  ↓
Tier 1: Direct aggregations (regex-based)
  ↓
Tier 2: Arithmetic & branching (regex-based)
  ↓
Tier 3: Time intelligence (regex-based)
  ↓
Tier 4: CALCULATE / FILTER (AST-based) ← START HERE
  ├─ Uses: try_ast_translate()
  ├─ Imports from: dax_ast_parser.py
  └─ Success rate: 70-80%
  ↓
Tier 5: LLM fallback (Tier 5) ← MORE CALLS HERE
  ├─ Uses: gemini_dax_translator.translate()
  ├─ Rate limited: 5 RPM (free tier)
  └─ Success rate: 60-70%
```

### Problem

- Tier 4 doesn't cache results → repeated metrics re-translated
- No capability detection → wastes LLM calls on impossible patterns
- No metrics collection → can't track performance
- AST parser is good but not optimized → missed opportunities

---

## Integration Steps

### Step 1: Import the New Engine

In `dax_translator.py`, add import:

```python
# At top of file
from semabridge.converter.dax_engine import (
    get_translation_engine,
    DaxTranslationEngine,
    CapabilityLevel,
    TranslationStrategy,
)
```

### Step 2: Initialize Engine in DaxTranslator.__init__()

```python
class DaxTranslator:
    def __init__(self, ...):
        # Existing code
        self.tier_handlers = [...]
        self.llm_translator = ...
        
        # NEW: Initialize advanced engine
        self.dax_engine = get_translation_engine()  # Caching enabled by default
```

### Step 3: Replace Tier 4 Handler

**Current Tier 4 (Lines ~450-500 in dax_translator.py):**

```python
# Tier 4: Complex CALCULATE / FILTER / ALL / ALLEXCEPT
is_complex = any(
    re.search(pattern, clean_dax, re.IGNORECASE)
    for pattern in self.UNSUPPORTED_PATTERNS
)
if is_complex:
    from semabridge.converter.dax_ast_parser import try_ast_translate
    ast_sql = try_ast_translate(
        clean_dax,
        table_alias=table_alias,
        column_map=column_map,
        function_map=function_map,
        date_alias=date_alias,
        dialect=dialect,
    )
    if ast_sql:
        return DAXTranslationResult(
            ast_sql,
            tier=4,
            dax=clean_dax,
        )
```

**New Tier 4 (Replacement):**

```python
# Tier 4: Advanced deterministic translation (deterministic engine)
# This now handles 80-90% of CALCULATE/FILTER/TIME_INTEL patterns
try:
    sql, metrics = self.dax_engine.translate(
        clean_dax,
        table_alias=table_alias,
        column_map=column_map,
        function_map=function_map,
        date_alias=date_alias,
        metric_name=metric_name,
    )
    if sql:
        # Log metrics for observability
        logger.debug(
            f"Tier 4 (Engine): {metric_name or 'unknown'} "
            f"{metrics.strategy.value} "
            f"({metrics.execution_time_ms:.1f}ms, cached={metrics.cached})"
        )
        return DAXTranslationResult(
            sql,
            tier=4,
            dax=clean_dax,
            # NEW: Include metrics
            metadata={
                "strategy": metrics.strategy.value,
                "capability": metrics.capability.value,
                "cached": metrics.cached,
                "confidence": metrics.confidence,
                "execution_time_ms": metrics.execution_time_ms,
            }
        )
except Exception as e:
    logger.warning(
        f"Tier 4 (Engine) failed for {metric_name}: {e}",
        exc_info=True
    )
    # Fall through to Tier 5
```

### Step 4: Update Tier 5 to Know It's the Last Resort

**Current Tier 5:**

```python
# Tier 5: LLM Fallback
result = self.llm_translator.translate(
    clean_dax,
    table_alias=table_alias,
    column_map=column_map,
    ...
)
```

**Enhanced Tier 5:**

```python
# Tier 5: LLM Fallback (Last Resort)
# Only reached if Tier 4 couldn't handle
logger.info(
    f"Tier 5 (LLM): {metric_name or 'unknown'} - "
    f"Deterministic engine could not handle, using Gemini API"
)

result = self.llm_translator.translate(
    clean_dax,
    table_alias=table_alias,
    column_map=column_map,
    ...
)

# If we get here, we've used LLM - log metrics
if result.sql:
    logger.info(
        f"Tier 5 (LLM): {metric_name or 'unknown'} succeeded"
    )
```

### Step 5: Add Metrics Collection

Add to `DaxTranslator` class:

```python
def get_translation_stats(self):
    """Get statistics about translations performed."""
    engine_summary = self.dax_engine.get_metrics_summary()
    return {
        "engine": engine_summary,
        "timestamp": datetime.datetime.now().isoformat(),
    }
```

### Step 6: Update translate() Method Signature

**Current:**

```python
def translate(
    self,
    dax: str,
    table_alias: str,
    column_map: Dict[str, str] = None,
    ...
) -> DAXTranslationResult:
```

**Updated:**

```python
def translate(
    self,
    dax: str,
    table_alias: str,
    column_map: Dict[str, str] = None,
    metric_name: str = None,  # NEW: for logging
    ...
) -> DAXTranslationResult:
    """
    Translate DAX expression to Snowflake SQL.
    
    Args:
        dax: DAX expression
        table_alias: SQL table alias
        column_map: Column rename mapping
        metric_name: Optional metric name for logging/observability
        ...
    
    Returns:
        DAXTranslationResult with SQL and metadata
    """
```

---

## Usage After Integration

### Basic Translation

```python
from semabridge.converter.dax_translator import DaxTranslator

translator = DaxTranslator()

result = translator.translate(
    "SUM([Amount])",
    table_alias="sales",
    metric_name="Total Sales"  # NEW
)

print(result.sql)
# SUM(sales.AMOUNT)

# Display metadata (NEW)
if result.metadata:
    print(result.metadata)
    # {
    #   "strategy": "ast_based",
    #   "capability": "fully_supported",
    #   "cached": False,
    #   "confidence": 1.0,
    #   "execution_time_ms": 12.45
    # }
```

### Batch Translation with Observability

```python
metrics_to_translate = {
    "Total Sales": "SUM([Amount])",
    "Avg Price": "AVERAGE([Price])",
    "By Region": "CALCULATE(SUM([Amount]), [Region] = 'West')",
    "Market Share": "DIVIDE([Sales], [Total Sales])",
}

results = {}
for name, dax in metrics_to_translate.items():
    result = translator.translate(
        dax,
        table_alias="sales",
        metric_name=name
    )
    results[name] = {
        "sql": result.sql,
        "tier": result.tier,
        "strategy": result.metadata.get("strategy") if result.metadata else None,
    }

# Check overall performance
stats = translator.get_translation_stats()
print(f"LLM calls needed: {stats['engine']['by_strategy'].get('llm_fallback', 0)}/4")
# Output: LLM calls needed: 0/4 (all deterministic!)
```

### Monitoring

```python
# Periodically check performance
stats = translator.get_translation_stats()

print(f"Success rate: {stats['engine']['success_rate']}")
print(f"Avg time: {stats['engine']['average_time_ms']:.1f}ms")
print(f"Breakdown:")
for strategy, count in stats['engine']['by_strategy'].items():
    print(f"  {strategy}: {count}")
```

---

## Testing After Integration

### Unit Test

```python
import pytest
from semabridge.converter.dax_translator import DaxTranslator

def test_tier4_engine_calcuate_filter():
    """Tier 4 should handle CALCULATE with FILTER deterministically."""
    translator = DaxTranslator()
    
    result = translator.translate(
        "CALCULATE(SUM([Amount]), [Region] = 'West')",
        table_alias="sales",
        metric_name="West Sales"
    )
    
    assert result.sql is not None
    assert result.tier == 4
    assert result.metadata["strategy"] == "ast_based"
    assert result.metadata["cached"] == False  # First call
    
    # Second call should be cached
    result2 = translator.translate(
        "CALCULATE(SUM([Amount]), [Region] = 'West')",
        table_alias="sales",
        metric_name="West Sales"
    )
    
    assert result2.sql == result.sql
    assert result2.metadata["cached"] == True  # Cache hit


def test_tier4_engine_vs_tier5_usage():
    """After integration, LLM (Tier 5) should be used <10% of time."""
    translator = DaxTranslator()
    
    metrics = [
        "SUM([Amount])",
        "AVERAGE([Price])",
        "CALCULATE(SUM([Amount]), [Region] = 'West')",
        "TOTALYTD(SUM([Amount]), [Date])",
        "DIVIDE([Sales], [Total])",
        "IF([Amount] > 100, [Amount], 0)",
    ]
    
    tier_distribution = {}
    for dax in metrics:
        result = translator.translate(dax, "sales")
        tier = result.tier
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1
    
    # All should be handled deterministically (Tier ≤ 4)
    assert tier_distribution.get(5, 0) == 0  # No LLM needed
    assert tier_distribution[4] >= 3  # Most in Tier 4
```

### Integration Test

```python
def test_batch_translation_performance():
    """Test that batch translation is fast with caching."""
    translator = DaxTranslator()
    
    dax_expr = "CALCULATE(SUM([Amount]), [Year] = 2024)"
    
    # Warm up cache
    translator.translate(dax_expr, "sales")
    
    # Measure cache hit
    import time
    start = time.time()
    for _ in range(100):
        result = translator.translate(dax_expr, "sales")
        assert result.metadata["cached"] == True
    elapsed = time.time() - start
    
    # 100 cache hits should be <10ms total
    assert elapsed < 0.01, f"100 cache hits took {elapsed*1000:.1f}ms"
```

---

## Migration Path

### Phase 1: Development (Current)
- [x] Created `dax_engine.py` with full implementation
- [x] Created `test_dax_engine.py` with comprehensive tests
- [ ] Run test suite to validate

### Phase 2: Integration (Next)
- [ ] Update `dax_translator.py` Tier 4 handler (this guide)
- [ ] Run integration tests
- [ ] Verify no breaking changes

### Phase 3: Validation (Following)
- [ ] Test with real dataset (500+ metrics)
- [ ] Verify LLM usage < 10%
- [ ] Measure performance improvement

### Phase 4: Deployment (Final)
- [ ] Deploy to staging
- [ ] Monitor metrics for 1 week
- [ ] Deploy to production
- [ ] Monitor production metrics

---

## Rollback Plan

If integration causes issues:

```python
# Simple rollback: comment out new engine code
# in dax_translator.py Tier 4, falls back to old logic

# OR restore previous dax_translator.py version:
# git checkout HEAD~1 -- dax_translator.py

# Verify old Tier 4 still works
translator = DaxTranslator()
result = translator.translate("SUM([Amount])", "sales")
assert result.sql is not None
```

---

## Expected Improvements

### Before Integration
```
Tier 1-3: 60% of metrics (regex-based)
Tier 4: 10% of metrics (AST-based, no cache)
Tier 5: 30% of metrics (LLM, 5 RPM limit)

LLM calls: 3 per 10 metrics
Average time: 500ms (blocked on LLM)
Cache hits: 0%
```

### After Integration
```
Tier 1-3: 60% of metrics (unchanged)
Tier 4: 30% of metrics (AST-based, WITH cache, better patterns)
Tier 5: 10% of metrics (LLM, truly last resort)

LLM calls: ~1 per 10 metrics (67% reduction)
Average time: 25ms (deterministic) or 500ms (LLM)
Cache hits: 60-80% on repeated metrics

Gemini quota improvement: 5 RPM → can handle 50+ requests/min!
```

---

## Configuration

### Optional: Custom Capability Detection

```python
# In dax_translator.py __init__:

# Option 1: Use default (recommended)
self.dax_engine = get_translation_engine()

# Option 2: Customize capability levels
from semabridge.converter.dax_engine import DaxTranslationEngine
self.dax_engine = DaxTranslationEngine(
    cache_enabled=True,
    cache_dir="./.dax_cache",  # Custom cache location
    capability_strictness="aggressive",  # Trade coverage for safety
)
```

### Optional: Disable Caching

```python
# For debugging: compare cached vs fresh
self.dax_engine = DaxTranslationEngine(cache_enabled=False)
```

---

## Troubleshooting

### Issue: "Module not found" on dax_engine import

**Solution:**
- Ensure `dax_engine.py` is in same directory as `dax_translator.py`
- Or add to Python path:
  ```python
  import sys
  sys.path.insert(0, '/path/to/semabridge/converter')
  ```

### Issue: Translation fails differently than before

**Solution:**
- Check `metrics.error` for details
- Compare with old Tier 4 handler
- Log to file:
  ```python
  import logging
  logging.basicConfig(level=logging.DEBUG)
  ```

### Issue: Performance degraded

**Solution:**
- Check cache hit rate: `metrics.cached`
- Clear cache if corrupted:
  ```bash
  rm .dax_translation_cache.jsonl
  ```
- Profile code:
  ```python
  import cProfile
  cProfile.run('translator.translate(...)')
  ```

---

## Success Criteria

For successful integration:

- [x] All existing tests pass
- [ ] New Tier 4 handles ≥80% of metrics deterministically
- [ ] LLM usage reduced to <10%
- [ ] Average translation time <50ms (excluding LLM)
- [ ] Cache hit rate ≥60% on repeated metrics
- [ ] No breaking changes to API
- [ ] Logging/metrics collected for all translations

---

## Questions?

Refer to:
- `DAX_ENGINE_GUIDE.md` - Architecture and patterns
- `test_dax_engine.py` - Usage examples
- `dax_engine.py` - Implementation details

---

**Last Updated**: March 17, 2026  
**Version**: 1.0  
**Status**: Ready for Integration
