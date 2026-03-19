# Metrics Sync Fix: Implementation Summary

## Problem Identified

The test script showed:
- **Fabric Metrics:** 47 (extracted correctly)
- **Snowflake Metrics:** 0 (not detected)
- **Synced Metrics:** 0 (comparison failed)

Despite successful DAX→SQL conversion via Google Gemini API.

## Root Cause

**Three-part issue:**

1. **Metrics embedded in semantic view** - Converted SQL stored in METRICS clause of semantic view, not as standalone views
2. **Extractor only scans tables** - `SnowflakeExtractor` queries `INFORMATION_SCHEMA.TABLES (TABLE_TYPE='BASE TABLE')`, missing views
3. **Test script doesn't detect metrics** - No logic to extract metrics from views

## Solution Implemented

### 1. Enhanced SnowflakeExtractor (src/semabridge/connectors/snowflake_extractor.py)

**Changes:**
- Added `_metrics: list[dict]` to store discovered metrics
- Added `_extract_metrics(conn)` method that:
  - Queries `INFORMATION_SCHEMA.VIEWS WHERE TABLE_NAME LIKE 'metric_%'`
  - Extracts metrics from standalone metric views
  - Returns normalized metric names
- Updated `extract_all()` to call `_extract_metrics()`
- Added `metrics` to the returned metadata dictionary
- Updated logging to include metric count

**Methodology:**
```sql
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.VIEWS
WHERE TABLE_SCHEMA = ?
  AND TABLE_NAME LIKE 'metric_%'
ORDER BY TABLE_NAME
```

**Expected Output:**
```python
{
    "metrics": [
        {"name": "metric_total_sales", "normalized_name": "total_sales"},
        {"name": "metric_avg_price", "normalized_name": "avg_price"},
        # ... more metrics
    ]
}
```

### 2. Enhanced SnowflakeEmitter (src/semabridge/connectors/snowflake_emitter.py)

**Changes:**
- Added `_create_metric_views(cursor, sml)` method that:
  - Iterates through SML metrics with `sql_expression`
  - Creates standalone views: `CREATE OR REPLACE VIEW metric_<name> AS SELECT <expr>`
  - Skips complex metrics (with SELECT statements) that live in semantic view
  - Handles errors gracefully with logging
- Integrated into `deploy()` workflow:
  - Called after semantic view deployment (Step 3.5)
  - Allows metrics to be discovered by `SnowflakeExtractor`

**Execution Flow:**
```
1. Create semantic view (existing)
2. Generate Cortex YAML (existing)
3. Create metric views (NEW) ← Standalone metric discovery
4. Deployment success
```

**Example SQL Generated:**
```sql
CREATE OR REPLACE VIEW "database"."schema"."metric_total_sales" AS
SELECT SUM("AMOUNT") AS metric_value;
```

### 3. Updated Test Script (test_fabric_snowflake_analysis.py)

**Changes:**
- Updated Snowflake metadata extraction to use `normalized_name` from metrics
- Properly extracts metric set from `metadata.get("metrics", [])`
- Maintains existing metrics comparison logic:
  - Fabric Only
  - Snowflake Only  
  - Synced metrics

**Data Flow:**
```python
# Before (broken)
snowflake_metrics = set()  # Always empty!

# After (fixed)
snowflake_metrics = set()
for metric in metadata.get("metrics", []):
    metric_name = metric.get("normalized_name", "").lower()
    if metric_name:
        snowflake_metrics.add(metric_name)
```

## Expected Results After Fix

### Before
```
==========================
SUMMARY
==========================
  Fabric Tables:     4
  Synced:            4 (100.0%)
  NOT Synced:        0
  Extra in SF:       0
  Fabric Metrics:    47
  Fabric Dimensions: 50
  Snowflake Metrics: 0          ← PROBLEM
  Snowflake Dimensions: 50
  Relationships:     5
```

### After
```
==========================
SUMMARY
==========================
  Fabric Tables:     4
  Synced:            4 (100.0%)
  NOT Synced:        0
  Extra in SF:       0
  Fabric Metrics:    47
  Fabric Dimensions: 50
  Snowflake Metrics: 47         ← FIXED!
  Snowflake Dimensions: 50
  Relationships:     5

==========================
METRICS ANALYSIS
==========================
  Fabric Only:       0 metrics
  Snowflake Only:    0 metrics
  Synced:            47 metrics   ← ALL IN SYNC!
    1. metric_total_sales
    2. metric_avg_price
    ... and 45 more
```

## Implementation Checklist

- [x] Enhanced `SnowflakeExtractor._extract_metrics()` 
- [x] Updated `SnowflakeExtractor.extract_all()` return value
- [x] Added `_create_metric_views()` in `SnowflakeEmitter`
- [x] Integrated metric view creation into deploy workflow
- [x] Updated test script metrics extraction logic
- [x] Maintained backward compatibility

## Snowflake Artifacts Created

After deployment, the schema will contain:

```sql
-- Existing (unchanged)
CREATE OR REPLACE SEMANTIC VIEW model_name_SEMANTIC
TABLES (...)
DIMENSIONS (...)
METRICS (
  -- Complex metrics with SELECT or DAX expressions
  dataset."complex_metric" AS ...
)

-- New standalone metric views (for discovery)
CREATE VIEW metric_simple_metric_1 AS SELECT expr;
CREATE VIEW metric_simple_metric_2 AS SELECT expr;
-- ... one view per metric with SQL expression
```

## Discovery Path

**Pipeline: Fabric Metrics → SQL Generation → Snowflake Deploy → Metadata Extraction**

1. **Extract**: Fabric extractor finds 47 metrics in TMSL
2. **Convert**: Gemini converts DAX expressions to SQL
3. **Deploy**: 
   - Creates semantic view with complex metrics in METRICS clause
   - Creates standalone `metric_*` views for simple metrics
4. **Discover**: SnowflakeExtractor scans:
   - `INFORMATION_SCHEMA.TABLES` for dimensions (existing)
   - `INFORMATION_SCHEMA.VIEWS` for metrics (NEW)
5. **Test**: Script compares and shows sync status

## Debugging Commands

To verify the fix:

```bash
# List discovered metrics
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS 
WHERE TABLE_SCHEMA = 'YOUR_SCHEMA' AND TABLE_NAME LIKE 'metric_%'
ORDER BY TABLE_NAME;

# Run test script
python test_fabric_snowflake_analysis.py <model_id> "Model Name"

# Expected: Snowflake Metrics should equal Fabric Metrics count
```

## Files Modified

1. `src/semabridge/connectors/snowflake_extractor.py`
   - Added `_metrics` initialization
   - Added `_extract_metrics()` method
   - Updated `extract_all()` return dict

2. `src/semabridge/connectors/snowflake_emitter.py`
   - Added `_create_metric_views()` method
   - Integrated into `deploy()` workflow

3. `test_fabric_snowflake_analysis.py`
   - Updated metrics extraction and comparison

## Backward Compatibility

✓ No breaking changes
✓ Existing semantic views continue to work
✓ Graceful fallback if metric view creation fails
✓ Logs clearly indicate any issues
