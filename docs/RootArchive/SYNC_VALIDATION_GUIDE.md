# Sync Validation Scripts - Usage Guide

## Overview

These scripts let you verify that measures synced correctly from Fabric to Snowflake, and that they're actually queryable.

## Scripts

### 1. `quick_sync_status.py` ⚡
**Purpose:** Quick check - is the semantic view deployed with metrics?

**What it does:**
- Connects to Snowflake
- Lists semantic view name
- Counts total metrics
- Tests querying one sample metric

**When to use:**
- Quick verification that sync happened
- See how many metrics are there
- 30 seconds execution

**Run:**
```powershell
python quick_sync_status.py
```

**Output:**
```
✓ Found: COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC
✓ Found 47 metrics

📋 Metric Names:
  1. TOTAL_REVENUE
  2. CAMPAIGN_ROI
  ...

🧪 Testing query on sample metric: TOTAL_REVENUE
✓ Query successful - Value: 1234567.89
```

---

### 2. `compare_fabric_vs_snowflake_measures.py` 📊
**Purpose:** Side-by-side comparison of Fabric vs Snowflake

**What it does:**
- Extracts measures from Fabric (real-time API call)
- Extracts metrics from Snowflake semantic view
- Normalizes names and compares
- Shows:
  - ✅ SYNCED: Measures successfully converted to metrics
  - ❌ MISSING: Measures that didn't sync (translation failures?)
  - ⚠️  EXTRA: Metrics in Snowflake not in Fabric

**When to use:**
- Validate *which specific* measures made it
- See *why* some didn't sync
- Identify translation failures
- 1-2 minutes execution

**Run:**
```powershell
python compare_fabric_vs_snowflake_measures.py
```

**Output:**
```
📊 SUMMARY
Total Fabric measures:        52
Synced to Snowflake:          47 ( 90.4%)
Missing in Snowflake:          5 (  9.6%)

✅ SYNCED MEASURES (47)
TOTAL_REVENUE              | TOTAL_REVENUE       | Sales
CAMPAIGN_ROI               | CAMPAIGN_ROI        | Marketing
...

❌ MISSING IN SNOWFLAKE (5)
COMPLEX_CALCULATED_MEASURE | Calculations        | Not Synced
...
```

**Output File:**
- Saved to: `output/sync_comparison_competitive_marketing_YYYYMMDD_HHMMSS.json`

---

### 3. `validate_synced_metrics.py` ✅
**Purpose:** Test that each synced metric actually works

**What it does:**
- Gets all metrics from Snowflake
- Runs `SELECT metric_name FROM semantic_view` for each
- Reports success/failure for each metric
- Shows actual query results

**When to use:**
- Verify metrics are queryable
- Find any broken or malformed metrics
- Validate data is correct
- 2-5 minutes execution

**Run:**
```powershell
python validate_synced_metrics.py
```

**Output:**
```
📊 Testing: TOTAL_REVENUE
   Query: SELECT TOTAL_REVENUE FROM COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC
   ✓ SUCCESS - Value: 1234567.89

📊 Testing: CAMPAIGN_ROI
   Query: SELECT CAMPAIGN_ROI FROM COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC
   ✓ SUCCESS - Value: 45.23
...

QUERY TEST SUMMARY
Total metrics:     47
Successful:        47 (100.0%)
Failed:            0
```

**Output File:**
- Saved to: `output/metric_query_validation_YYYYMMDD_HHMMSS.json`

---

### 4. `run_complete_sync_validation.py` 🚀
**Purpose:** Run everything in sequence and get full report

**What it does:**
- Runs comparison script
- Runs validation script
- Shows executive summary

**When to use:**
- Complete end-to-end validation
- Get full picture after a sync
- Confidence that everything works
- 5-10 minutes execution

**Run:**
```powershell
python run_complete_sync_validation.py
```

**Output:**
Combines output from both scripts plus summary:
```
📊 Sync Statistics:
  • Total Fabric Measures:     52
  • Synced to Snowflake:       47
  • Missing in Snowflake:       5
  • Sync Rate:                 90.4%

✅ Query Validation:
  • Total Metrics Tested:      47
  • Successful Queries:        47
  • Success Rate:              100.0%
```

---

## Workflow Examples

### After Each Sync
```powershell
# Quick check
python quick_sync_status.py

# Details on what happened
python compare_fabric_vs_snowflake_measures.py
```

### Full Validation
```powershell
# Complete end-to-end validation
python run_complete_sync_validation.py
```

### Debugging Specific Issues
```powershell
# See which measures didn't sync
python compare_fabric_vs_snowflake_measures.py

# Check if metrics can be queried
python validate_synced_metrics.py

# Look at the JSON reports in output/ for detailed info
```

---

## Understanding the Results

### Synced Measures
✅ Metric appears in Snowflake semantic view AND can be queried successfully.

**Reasons for success:**
- DAX expression translated correctly
- SQL valid and executable
- Data structure correct

### Missing Measures
❌ Measure exists in Fabric but not in Snowflake.

**Reasons for failure:**
- DAX expression too complex (translation tier insufficient)
- Contains unsupported DAX functions
- Hidden measure in Fabric
- Translation service returned no SQL

### Failed Query Validation
⚠️  Metric exists in Snowflake but query failed.

**Possible causes:**
- SQL expression syntax error
- Invalid column references
- Data type mismatch
- Table not accessible

---

## Reports

All scripts save JSON reports to `output/` directory:

```
output/
├── sync_comparison_competitive_marketing_20250314_143052.json
└── metric_query_validation_20250314_143103.json
```

These include:
- Detailed measure-by-measure comparison
- Query results for each metric
- Error messages (if any)
- Timestamps

---

## Commands Reference

```powershell
# Quick status
python quick_sync_status.py

# Compare Fabric vs Snowflake
python compare_fabric_vs_snowflake_measures.py

# Validate queryability
python validate_synced_metrics.py

# Full validation
python run_complete_sync_validation.py

# View latest reports
Get-ChildItem output/ -Filter "*sync_comparison*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | ForEach-Object { Get-Content $_.FullName | ConvertFrom-Json | ConvertTo-Json }
```

---

## Troubleshooting

If scripts fail, check:

1. **Snowflake Credentials**
   - Verify `.env` has correct SNOWFLAKE_* values
   - Confirm warehouse/database/schema exist
   - Test connectivity: `python -c "import snowflake.connector; print('OK')"`

2. **Fabric Credentials**
   - Verify `.env` has FABRIC_* values
   - MSAL token should auto-refresh
   - Check if model is accessible via Fabric API

3. **Semantic View**
   - Run: `SHOW SEMANTIC VIEWS LIKE '%COMPETITIVE%'`
   - Should return exactly one view
   - Check Snowflake for errors: `SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME LIKE '%COMPETITIVE%'`

---

## Pro Tips

1. **After each full sync**, run:
   ```powershell
   python run_complete_sync_validation.py | Tee-Object "sync_log_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
   ```

2. **Compare before/after changes**, save both:
   ```powershell
   python compare_fabric_vs_snowflake_measures.py
   # Make changes
   python compare_fabric_vs_snowflake_measures.py
   # Compare the two JSON files
   ```

3. **Monitor specific metric quality**:
   ```powershell
   python validate_synced_metrics.py | Select-String "FAILED|ERROR"
   ```

---

## Next Steps After Validation

- **If 100% synced:** ✅ All systems working correctly
- **If <100% synced:** 
  - Check `missing_measures` in comparison report
  - Review DAX translation issues
  - May need to configure `GeminiDAXTranslator` for complex expressions
- **If queries fail:**
  - Check Snowflake error logs
  - Validate semantic view DDL
  - Verify column security policies

