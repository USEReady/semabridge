# Metrics Sync Fix: Testing & Verification Guide

## Quick Test (5 minutes)

### Step 1: Deploy a Model with Metrics
```bash
# Make sure your Fabric model with metrics is accessible
python test_fabric_snowflake_analysis.py <model_id> "Your Model Name"
```

### Step 2: Check Snowflake for Metric Views
```sql
-- Connect to your Snowflake workspace
USE DATABASE your_database;
USE SCHEMA your_schema;

-- List all metric views (should match Fabric metric count)
SELECT TABLE_NAME, TABLE_TYPE
FROM INFORMATION_SCHEMA.VIEWS
WHERE TABLE_NAME LIKE 'metric_%'
ORDER BY TABLE_NAME;

-- Expected: N rows where N = number of metrics in Fabric model
```

### Step 3: Verify Test Script Output
```bash
python test_fabric_snowflake_analysis.py <model_id> "Your Model Name"
```

**Expected Output:**
```
[SUMMARY]
  Fabric Tables:     X
  Synced:            X (100.0%)
  NOT Synced:        0
  Extra in SF:       0
  Fabric Metrics:    47
  Fabric Dimensions: 50
  Snowflake Metrics: 47      ← Should match Fabric count!
  Snowflake Dimensions: 50
  Relationships:     X

[METRICS ANALYSIS]
  Fabric Only:       0 metrics
  Snowflake Only:    0 metrics
  Synced:            47 metrics
    1. metric_<name1>
    2. metric_<name2>
    ...
```

## Detailed Verification Steps

### 1. Verify Metric View Creation

**Check Snowflake logs:**
```sql
-- List recently created views
SELECT 
    TABLE_SCHEMA,
    TABLE_NAME, 
    TABLE_TYPE,
    CREATED
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'YOUR_SCHEMA'
  AND TABLE_NAME LIKE 'metric_%'
ORDER BY CREATED DESC
LIMIT 20;
```

**Expected:** Multiple `VIEW` entries with `metric_*` names

### 2. Verify Metric View Structure

```sql
-- Check a specific metric view definition
SELECT GET_DDL('VIEW', 'your_schema.metric_<name>');

-- Expected output example:
-- CREATE VIEW metric_total_sales AS SELECT SUM("AMOUNT") AS metric_value;
```

### 3. Verify Metadata Extraction

Enable debug logging to see extraction details:

```python
import logging
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)
logger.setLevel(logging.DEBUG)

# Run extraction
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
from semabridge.core.settings import get_settings

settings = get_settings()
extractor = SnowflakeExtractor(settings.snowflake)
metadata = extractor.extract_all()

# Check metrics
print(f"Found {len(metadata['metrics'])} metrics:")
for m in metadata['metrics']:
    print(f"  - {m['name']}: {m['normalized_name']}")
```

### 4. Test Script Detailed Output

Run with verbose logging enabled:

```bash
# Set environment variable for debug logging
export SEMABRIDGE_LOG_LEVEL=DEBUG

# Run test
python test_fabric_snowflake_analysis.py <model_id> "Model Name" 2>&1 | tee test_output.log

# Check for these log messages:
# - "[OK] Found N metric views"
# - "Extracted N metric views"
# - "DIMENSIONS ANALYSIS" section showing dimension counts match
```

## Troubleshooting

### Issue: Snowflake Metrics Still Shows 0

**Possible Causes:**

1. **Metric views not created**
   - Check Snowflake for `metric_*` views: `SHOW VIEWS LIKE 'metric_%';`
   - If missing, check deployment logs for `_create_metric_views` step
   - Verify SML metrics have `sql_expression` field

2. **Extractor not finding views**
   - Enable debug logging in `_extract_metrics()`
   - Run SQL: `SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_NAME LIKE 'metric_%'`
   - Verify schema name is correct in config

3. **Test script not using the metrics**
   - Verify `metadata.get("metrics", [])` returns data
   - Check that `normalized_name` is being extracted correctly
   - Print debug info: `print(metadata["metrics"])`

**Solutions:**

```python
# Debug script to test extraction
from semabridge.core.settings import get_settings
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor

settings = get_settings()
extractor = SnowflakeExtractor(settings.snowflake)

# Test connection
try:
    extractor.test_connection()
    print("✓ Connection successful")
except Exception as e:
    print(f"✗ Connection failed: {e}")
    exit(1)

# Extract all metadata
metadata = extractor.extract_all()

# Check tables (should work as before)
print(f"Tables: {len(metadata.get('tables', {}))}")
print(f"Columns: {sum(len(c) for c in metadata.get('columns', {}).values())}")

# Check metrics (NEW)
metrics = metadata.get('metrics', [])
print(f"Metrics: {len(metrics)}")
if metrics:
    for m in metrics[:3]:
        print(f"  {m}")
else:
    print("  WARNING: No metrics found!")
    print("  Check if metric_* views exist in Snowflake")
```

### Issue: Metric View Creation Failed

**Check logs in deployment:**
```
# In deploy() output, look for:
# "Creating standalone metric views..."
# "Created metric view: metric_<name>"
# or
# "Failed to create metric view for '<name>': <error>"
```

**Causes:**
- Metric `sql_expression` is too complex (contains SELECT)
- Invalid SQL syntax in converted DAX expression
- Permissions issue in Snowflake

**Fix:**
- For complex metrics, they're already in semantic view METRICS clause
- For simple metrics, check the SQL in the metric object
- Verify Snowflake role has CREATE VIEW permissions

### Issue: Dimension Count Mismatch

**This is expected** if:
- Fabric has calculated columns that don't sync to Snowflake
- Snowflake has extra system columns (METADATA$*)

**Not an error** - The test script filters these appropriately.

## Performance Notes

### Metric Extraction Time
- `_extract_metrics()` adds minimal overhead (1 INFORMATION_SCHEMA query)
- View creation during deployment: ~50-100ms per metric
- Metadata extraction: ~1-2s for 50 metrics

### Optimization Tips
- If many metrics, use parallel=True in extract_all()
- Metric views are lightweight and don't affect query performance
- Semantic view METRICS clause is more efficient for Cortex Analyst

## Success Criteria

✓ **All of these must be true:**

1. Test script shows `Fabric Metrics` > 0
2. Test script shows `Snowflake Metrics` > 0
3. `Fabric Metrics` == `Snowflake Metrics`
4. `Synced` count equals total metrics
5. No warnings or errors in deployment logs for metric creation
6. `SHOW VIEWS LIKE 'metric_%'` lists expected metric views
7. Metric view definitions are valid SQL

## Success Example

```
================================================================================
COMPARISON RESULTS
================================================================================

[SYNCED TABLES] (4)
────────────────────────────────────────────────────────────────────────────
  [OK] SALES_FACTS                         Fabric: 15 cols  Snowflake: 15 cols
  [OK] DIM_DATE                            Fabric:  7 cols  Snowflake:  7 cols
  [OK] DIM_CUSTOMER                        Fabric: 12 cols  Snowflake: 12 cols
  [OK] DIM_PRODUCT                         Fabric:  8 cols  Snowflake:  8 cols

[METRICS ANALYSIS]
────────────────────────────────────────────────────────────────────────────
  Fabric Only:       0 metrics
  Snowflake Only:    0 metrics
  Synced:            47 metrics
    1. metric_total_sales
    2. metric_avg_price
    3. metric_product_units
    ... and 44 more

[DIMENSIONS ANALYSIS]
────────────────────────────────────────────────────────────────────────────
  Fabric Only:       0 dimensions
  Snowflake Only:    0 dimensions
  Synced:            42 dimensions

================================================================================
ACTION ITEMS
================================================================================

[SUCCESS] All Fabric tables, columns, metrics, and dimensions are synced to Snowflake!
```

## Next Steps After Fix Verification

1. **Run end-to-end sync test**
   - Deploy model with metrics from start to finish
   - Verify all artifacts created in Snowflake

2. **Test Cortex Analyst integration**
   - Verify semantic view works with Cortex Analyst
   - Confirm metrics are queryable

3. **Load testing**
   - Test with larger models (100+ metrics)
   - Check performance of metadata extraction

4. **Deployment automation**
   - Add test-metric-sync to CI/CD pipeline
   - Monitor for metric sync failures in production
