# Measure Pipeline Tracing Guide

This directory contains comprehensive test scripts for debugging measure conversion through the SemaBridge pipeline.

## Overview

Measures flow through three stages in the SemaBridge pipeline:

```
Fabric Semantic Model
        â†“
    (TMSL extraction)
        â†“
    SML Canonical Model
        â†“
    (SML â†’ SQL conversion)
        â†“
    Snowflake Semantic View (METRICS clause)
```

These scripts help identify **where and why measures are dropped** or not converted at each stage.

---

## Test Scripts

### 1. **test_measure_pipeline_detailed.py** (Recommended)

Advanced debugging script with detailed transformation tracking.

**Usage:**
```bash
# Basic usage (auto-detects workspace from .env)
uv run tests/test_measure_pipeline_detailed.py --dataset COMPETITIVE_MARKETING_ANALYSIS

# With explicit workspace ID
uv run tests/test_measure_pipeline_detailed.py --workspace-id 12345-abcde --dataset COMPETITIVE_MARKETING_ANALYSIS

# Custom output location
uv run tests/test_measure_pipeline_detailed.py --dataset COMPETITIVE_MARKETING_ANALYSIS --output ./debug_reports/my_analysis
```

**Output:**
- `measure_analysis_DATASET_TIMESTAMP.txt` - Human-readable analysis report
- `measure_analysis_DATASET_TIMESTAMP.json` - Machine-readable JSON for programmatic access

**What it does:**
1. âœ… Extracts all measures directly from Fabric TMSL
2. âœ… Converts them through the tmsl_to_sml transformer
3. âœ… Checks which measures are eligible for Snowflake generation
4. âœ… Generates detailed comparison report showing:
   - Complete measures (all 3 stages: Fabric â†’ SML â†’ Snowflake)
   - Partial measures (stopped at some stage)
   - Dropoff analysis with reasons
   - Success rates and statistics

**Example Output:**
```
================================================================================
MEASURE PIPELINE ANALYSIS REPORT
================================================================================
Dataset: COMPETITIVE_MARKETING_ANALYSIS
Workspace: 12345-abcde-fghij-klmno
Generated: 2026-03-14T16:30:45.123456

================================================================================
PIPELINE OVERVIEW
================================================================================

Stage Counts:
  â€¢ Fabric (extracted):      47 measures
  â€¢ SML (canonical):         45 measures
  â€¢ Snowflake (target):      43 measures

Status Distribution:
  âœ… COMPLETE                    43 measures
  âš ï¸  PARTIAL (Fabric â†’ SML)      2 measures
  âš ï¸  PARTIAL (SML â†’ Snowflake)   2 measures

================================================================================
MEASURES BY STATUS
================================================================================

âœ… COMPLETE (43 measures):
  competitive_marketing_analysis.Total Sales
    Expression: SUM('Sales Table'[Sales Amount])
    SQL: SUM(sales_table."SALES_AMOUNT")
  
  competitive_marketing_analysis.Total Customers
    Expression: DISTINCTCOUNT('Customer'[CustomerID])
    SQL: COUNT(DISTINCT customer."CUSTOMERID")
  
  ...

âš ï¸  PARTIAL (4 measures):

âŒ Fabric â†’ SML Dropoff (2 measures):
  â€¢ competitive_marketing_analysis.Hidden Measure 1
    Why: Likely filtered by tmsl_to_sml converter (hidden, system table, etc)
  
  â€¢ competitive_marketing_analysis.Hidden Measure 2
    Why: Likely filtered by tmsl_to_sml converter (hidden, system table, etc)

âŒ SML â†’ Snowflake Dropoff (2 measures):
  â€¢ competitive_marketing_analysis.Complex Time Measure
    Reason: Complex DAX expression requires decomposition
    Complexity: Tier 3
    Time Intelligence: True
  
  â€¢ competitive_marketing_analysis.Conditional Calc
    Reason: Uses CALCULATE() with multiple filters
    Complexity: Tier 3
    Time Intelligence: False

================================================================================
STATISTICS
================================================================================
Total unique measures: 47
Successfully converted: 43/47 (91.5%)
Fabric â†’ SML drop rate: 2/47 (4.3%)
SML â†’ Snowflake drop rate: 2/45 (4.4%)
```

---

### 2. **test_measure_pipeline.py** (Quick Overview)

Lighter-weight script for quick checks.

**Usage:**
```bash
uv run tests/test_measure_pipeline.py --workspace-id WS_ID --dataset DATASET_NAME --output markdown
```

**Output:**
- Markdown report with measure comparison
- JSON format for programmatic parsing

---

## What Information You Get

### Stage 1: Fabric Extraction
- âœ… All measures from Fabric TMSL
- âŒ Hidden measures (if they exist)
- âŒ System tables (auto-skipped)

### Stage 2: SML Conversion
- âœ… Measures that made it through tmsl_to_sml
- âŒ Filtered measures (why they were dropped)
- Transformation details (expression â†’ SQL)
- Complexity tier classification

### Stage 3: Snowflake SQL Generation
- âœ… Measures eligible for METRICS clause
- âŒ Measures that failed to convert (reasons)
- SQL expression validation

---

## Key Insights

### Why Measures Are Dropped at Each Stage

**Fabric â†’ SML (typical reasons):**
- Hidden measures (`isHidden: true`) - intentionally filtered
- System date tables (`LocalDateTable_*`, `DateTableTemplate_*`) - auto-filtered
- Calculation groups - not yet supported in V1

**SML â†’ Snowflake (typical reasons):**
- **Complexity Tier 3+** - Complex DAX (CALCULATE, FILTER, TIME_INTELLIGENCE)
- **Time Intelligence Required** - DATEADD, PARALLELPERIOD, SAMEPERIODLASTYEAR, etc.
- **Complex Filters** - Multiple FILTER/ALL clauses
- **Conditional Logic** - IF/SWITCH with external dependencies
- **Row-Level Security** - Dynamic measure evaluation

---

## Interpreting Results

### Success Metrics

**High Success Rate (>85%):**
- Most measures are simple aggregations (SUM, COUNT, AVG, etc.)
- Limited time intelligence usage
- Good for direct Snowflake sync

**Medium Success Rate (50-85%):**
- Mix of simple and complex measures
- Some require decomposition strategies
- May need materialized tables approach

**Low Success Rate (<50%):**
- Heavy use of Time Intelligence
- Complex DAX with multiple context modifications
- Consider pre-materialized approach in Snowflake

---

## Running the Tests

### Option 1: Direct uv run ```bash
# Make sure you're in the project root
cd /path/to/semabridge_merged

# Run with uv run uv run tests/test_measure_pipeline_detailed.py --dataset "My Dataset Name"
```

### Option 2: PyTest (with verbose output)
```bash
pytest tests/test_measure_pipeline_detailed.py -v -s --dataset "My Dataset Name"
```

### Option 3: Debug Mode
```bash
# Enable debug logging
LOGLEVEL=DEBUG uv run tests/test_measure_pipeline_detailed.py --dataset "My Dataset Name"
```

---

## Common Issues & Troubleshooting

### Issue: "Fabric extractor not available"
**Cause:** Fabric credentials not configured
**Solution:** 
```bash
# Option 1: Set environment variables
export FABRIC_CLIENT_ID=your_client_id
export FABRIC_TENANT_ID=your_tenant_id

# Option 2: Update .env file
FABRIC_CLIENT_ID=your_client_id
FABRIC_TENANT_ID=your_tenant_id
FABRIC_WORKSPACE_ID=your_workspace_id
```

### Issue: "No valid Fabric access token"
**Cause:** Authentication failed
**Solution:**
- Ensure you've authenticated with Fabric in the UI first
- Or use `FABRIC_ACCESS_TOKEN` env var with a pre-issued token
- Or configure `FABRIC_CLIENT_SECRET` for service principal auth

### Issue: Empty measure lists in all stages
**Cause:** Dataset not found or extraction failed
**Solution:**
```bash
# Verify dataset name is exact (case-sensitive)
# Verify workspace ID is correct
# Check logs for specific error messages
```

---

## Example: Finding Missing Measures

Let's say you deployed a model but some measures didn't appear in Snowflake:

```bash
# 1. Run the tracer
uv run tests/test_measure_pipeline_detailed.py --dataset COMPETITIVE_MARKETING_ANALYSIS

# 2. Look for "SML â†’ Snowflake Dropoff" section
# 3. Check the "Reason" column for each dropped measure
# 4. Based on reason:

# If "Complexity: Tier 3" or "Time Intelligence: True"
#   â†’ Need to use performance aggregation or decompose the measure

# If "Custom expression"
#   â†’ May need manual conversion to SQL

# If "Depends on other measures"
#   â†’ Ensure dependent measures are synced first
```

---

## Next Steps

After running these scripts:

1. **For Simple Measures (Tier 1-2):**
   - Direct sync to Snowflake should work
   - Check for any SQL syntax issues in generated DDL

2. **For Complex Measures (Tier 3+):**
   - Consider alternative materialization strategies:
     - Pre-aggregated tables in Snowflake
     - Scheduled DAX queries cached to Snowflake
     - Decompose complex DAX into simpler components

3. **For Dropped Measures:**
   - Review business requirements
   - Determine if measure can be approximated with simpler logic
   - Or implement via alternative sync strategy (scheduled refresh)

---

## File Locations

- **Test Scripts:** `tests/test_measure_pipeline_*.py`
- **Reports:** `output/measure_analysis_*.txt` (auto-generated)
- **Configuration:** `.env` file in project root
- **Logs:** Output to console (configurable in script)

---

## Performance Considerations

- **Typical Runtime:** 30 seconds - 2 minutes depending on model size
- **Large Models (100+ measures):** May take 3-5 minutes
- **Network:** Requires connection to Fabric and optionally Snowflake
- **Memory:** Minimal impact (<100MB)

---

## Questions or Issues?

When reporting issues, include:
1. Output from the script
2. `--dataset` name you used
3. Error messages (full stack trace if available)
4. Approximate number of measures in the model

