# Root Cause Analysis: Missing Metrics in Snowflake

## Executive Summary

**Problem**: The test script shows:
- Fabric Metrics: 47
- Snowflake Metrics: 0
- Synced Metrics: 0

But the pipeline successfully converts DAX metrics to SQL using Google AI.

## Root Cause

The issue is **THREE-FOLD**:

### Issue #1: Metrics Embedded (Not Views)
- **Current behavior**: Converted SQL is embedded in the METRICS clause of a Snowflake semantic view
- **Location**: `src/semabridge/connectors/snowflake_emitter.py:1212-1280`
- **Example**:
  ```sql
  CREATE OR REPLACE SEMANTIC VIEW model_name_SEMANTIC
  TABLES (...)
  DIMENSIONS (...)
  METRICS (
    dataset."metric_name" AS SUM(dataset."amount")
  )
  ```
- **Problem**: Metrics are part of the semantic view DDL, NOT standalone views
- **Why it fails**: `SnowflakeExtractor` queries `INFORMATION_SCHEMA.TABLES` (only BASE TABLEs)
- **Missing**: No code creates standalone `metric_<metric_name>` views

### Issue #2: Extractor Doesn't Scan Views
- **Location**: `src/semabridge/connectors/snowflake_extractor.py:300+`
- **Current query**:
  ```sql
  SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES 
  WHERE TABLE_SCHEMA = 'schema' 
  AND TABLE_TYPE = 'BASE TABLE'
  ```
- **Missing**: No scan of `INFORMATION_SCHEMA.VIEWS` or semantic views
- **No metric discovery**: Never extracts metrics from semantic view METRICS clause

### Issue #3: Test Script Doesn't Detect Metrics from Views
- **Location**: `test_fabric_snowflake_analysis.py:145+`
- **Current logic**: Only scans tables, never checks views
- **Result**: `snowflake_metrics = set()` remains empty

## Fix Strategy (3 Steps)

### Step 1: Create Standalone Metric Views
Modify `snowflake_emitter.py` to create `CREATE VIEW metric_<name> AS <sql>` after semantic view deployment.

### Step 2: Extend SnowflakeExtractor
Add metric detection by querying:
```sql
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS 
WHERE TABLE_SCHEMA = 'schema' 
AND TABLE_NAME LIKE 'metric_%'
```

### Step 3: Update Test Script
Modify analysis script to collect metrics from Snowflake views.

## Expected Result

After fixes:
```
Fabric Metrics: 47
Snowflake Metrics: 47
Synced Metrics: 47
```

## Files to Modify

1. `src/semabridge/connectors/snowflake_emitter.py` - Create metric views
2. `src/semabridge/connectors/snowflake_extractor.py` - Extract metrics from views
3. `test_fabric_snowflake_analysis.py` - Detect metrics in comparison
