# Measure Pipeline Debugging - Quick Start

## ðŸŽ¯ What You Get

I've created **two test scripts** that trace measures through your SemaBridge pipeline to identify which ones are dropped and why:

```
Fabric Model (47 measures)
    â†“ [Extract via TMSL]
    â†“
SML Model (45 measures) 
    â†“ [2 dropped: reason = hidden/system tables]
    â†“ [Convert to SQL]
    â†“
Snowflake METRICS (43 measures)
    â†“ [2 dropped: reason = complex DAX/time intel]
```

---

## ðŸš€ Quick Start

### Run the Analysis (5 minutes)

```bash
cd /c/Users/chara/semabridge_merged

# Basic command (auto-detects workspace from .env)
uv run tests/test_measure_pipeline_detailed.py --dataset COMPETITIVE_MARKETING_ANALYSIS

# With explicit workspace
uv run tests/test_measure_pipeline_detailed.py \
  --workspace-id 12345-abcde \
  --dataset COMPETITIVE_MARKETING_ANALYSIS
```

### What You'll See

**Console Output:**
```
================================================================================
MEASURE PIPELINE ANALYSIS REPORT
================================================================================
Stage Counts:
  â€¢ Fabric (extracted):      47
  â€¢ SML (canonical):         45
  â€¢ Snowflake (target):      43

Status Distribution:
  âœ… COMPLETE                    43 measures
  âš ï¸  PARTIAL (Fabric â†’ SML)     2 measures
  âš ï¸  PARTIAL (SML â†’ Snowflake)  2 measures

...detailed breakdown of each measure...
```

**Files Generated:**
- `output/measure_analysis_DATASET_2026-03-14_163045.txt` - Human readable
- `output/measure_analysis_DATASET_2026-03-14_163045.json` - Machine readable

---

## ðŸ“Š Understanding the Report

### Complete Measures (âœ…)
```
âœ… COMPLETE (43 measures):
  sales.Total Sales
    Expression: SUM('Sales'[Amount])
    SQL: SUM(sales."AMOUNT")
```
âœ“ Made it through all 3 stages successfully

### Partial Measures (âš ï¸)
```
âš ï¸  PARTIAL (SML â†’ Snowflake Dropoff):
  marketing.Complex Revenue  
    Reason: Complex DAX expression requires decomposition
    Complexity: Tier 3
    Time Intelligence: True
```
âœ“ Stopped at some stage (see "dropoff analysis" for why)

### Dropoff Analysis
Shows which measures were dropped at each stage and **why**:

**Fabric â†’ SML Dropoffs (typical reasons):**
- Hidden measures (`isHidden: true`)
- System date tables (`LocalDateTable_*`)
- Calculation groups (not supported in V1)

**SML â†’ Snowflake Dropoffs (typical reasons):**
- **Complexity Tier 3+** - Complex DAX formulas
- **Time Intelligence** - DATEADD, PARALLELPERIOD, etc.
- **Multiple Filters** - CALCULATE/FILTER with multiple conditions
- **Row-Level Security** - Dynamic evaluation needed

---

## ðŸ” Interpreting Results

### Success Rate > 85%
âœ… **Good** - Most measures are simple aggregations (SUM, COUNT, AVG)
- These sync directly to Snowflake without issues

### Success Rate 50-85%
âš ï¸ **Mixed** - Combination of simple and complex measures
- Simple ones sync fine
- Complex ones may need decomposition

### Success Rate < 50%
âŒ **Challenging** - Heavy use of Time Intelligence or complex DAX
- Consider pre-materialized approach
- May need custom SQL conversions

---

## ðŸ“‹ Example: Finding Why a Measure Didn't Convert

**Scenario:** You deployed "Total Revenue (YoY)" but it's missing from Snowflake

**Steps:**

1. Run the script:
   ```bash
   uv run tests/test_measure_pipeline_detailed.py --dataset YOUR_DATASET
   ```

2. Look for "Total Revenue (YoY)" in the "SML â†’ Snowflake Dropoff" section

3. Check the **Reason** column:
   ```
   âŒ SML â†’ Snowflake Dropoff:
     â€¢ dataset.Total Revenue (YoY)
       Reason: Uses Time Intelligence (PARALLELPERIOD)
       Complexity: Tier 3
       Time Intelligence: True
   ```

4. **Action items:**
   - If `Time Intelligence: True` â†’ Need custom handling
   - If `Complexity: Tier 3` â†’ Too complex for direct sync
   - If `Depends on other measures` â†’ Sync dependencies first

---

## ðŸ› ï¸ Troubleshooting

### "Fabric extractor not available"
```bash
# Check .env has Fabric credentials:
cat .env | grep FABRIC_

# If missing, add:
export FABRIC_WORKSPACE_ID=your_workspace_id
export FABRIC_CLIENT_ID=your_client_id
```

### "No measures found"
```bash
# Verify dataset name is correct (case-sensitive):
# Try with quotes if it has spaces:
uv run tests/test_measure_pipeline_detailed.py --dataset "Competitive Marketing Analysis"
```

### Empty Snowflake stage
```bash
# This is normal! Snowflake output requires running the full emitter
# The script shows which measures are *eligible* for Snowflake generation
# Actual DDL would be generated during deployment
```

---

## ðŸ“š Available Scripts

| Script | Use Case | Speed | Detail |
|--------|----------|-------|--------|
| `test_measure_pipeline_detailed.py` | â­ **Primary** - Full pipeline analysis | 30-120s | High |
| `test_measure_pipeline.py` | Quick overview | 10-30s | Medium |

---

## ðŸ“ Files Created

**Test Scripts:**
- âœ… `tests/test_measure_pipeline.py` (basic)
- âœ… `tests/test_measure_pipeline_detailed.py` (advanced, recommended)
- âœ… `tests/MEASURE_PIPELINE_GUIDE.md` (comprehensive guide)

**Auto-generated Reports:**
- `output/measure_analysis_DATASET_TIMESTAMP.txt`
- `output/measure_analysis_DATASET_TIMESTAMP.json`

---

## ðŸŽ“ Common Findings

### Typical for Power BI â†’ Snowflake

**High Success (90%+):**
- SUM, COUNT, AVG, MIN, MAX measures â†’ âœ… All convert
- Simple conditional measures â†’ âœ… Most convert
- Measures without time intelligence â†’ âœ… Convert well

**Medium Success (60-90%):**
- Year-to-date calculations â†’ âš ï¸ May need custom handling
- Conditional aggregations â†’ âš ï¸ Complex filters drop
- Measures on implicit measures â†’ âš ï¸ Some drop

**Low Success (<60%):**
- Time intelligence measures (DATEADD, etc.) â†’ âŒ Complex patterns
- Measures with RLS logic â†’ âŒ Not compatible
- Measures across multiple tables â†’ âŒ Decomposition needed

---

## âœ… Next Steps

1. **Run the script:**
   ```bash
   uv run tests/test_measure_pipeline_detailed.py --dataset COMPETITIVE_MARKETING_ANALYSIS
   ```

2. **Review the report** for:
   - Which measures succeeded (will be in Snowflake)
   - Which measures dropped (see reason why)
   - Success rate (target: >85%)

3. **For dropped measures:**
   - If hidden â†’ OK (intentional)
   - If complex DAX â†’ May need pre-aggregation in Snowflake
   - If time intelligence â†’ Use specialized conversion

4. **Track over time:**
   - Run script after each model update
   - Compare success rates
   - Identify new dropoffs early

---

## ðŸ“ž Questions?

Check `MEASURE_PIPELINE_GUIDE.md` for:
- Detailed explanation of each metric
- Why measures drop at each stage
- How to interpret complexity tiers
- Alternative materialization strategies

---

**Total time to answer:** 2-5 minutes
**Report generation:** 30-120 seconds depending on model size
**Actionable insights:** Immediate

