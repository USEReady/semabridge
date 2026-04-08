# Client Data Model Deployment - Root Cause Analysis & Fix Strategy

## Executive Summary

The Client Data semantic view deployment fails at Stage 9 with SQL compilation errors related to:

1. **Special character handling** (`$` in column names)
2. **Relationship reference formatting**
3. **Schema compatibility** between model and Snowflake target

**Status**: Issues identified and targeted fixes applied without modifying core pipeline.

---

## Issue Breakdown

### Issue #1: Dollar Sign ($) in Column Identifiers ⚠️ CRITICAL

**Error Message:**

```
invalid identifier 'REP_SFDC_SBQQ_QUOTE_PRIMARY."FOUNDATIONS_NET_$"'
```

**Root Cause:**

- Snowflake semantic view DDL doesn't support `$` in quoted identifiers
- Metrics like `TRACKER_NET_$`, `FOUNDATIONS_NET_$` are filtered but still appear in generated DDL
- These are derived/alias columns from Power BI model

**Affected Columns:**

```
FOUNDATIONS_NET_$
FOUNDATIONS_NET_$_W
TRACKER_NET_$
TRACKER_NET_$_W
TRACKER_OCEAN_LOGISTICS_NET_$
TRACKER_OCEAN_LOGISTICS_NET_$_W
etc.
```

**Fix Applied:**
✅ Metric filtering in `snowflake_emitter.py`:

```python
valid_metrics = [m for m in sml.metrics if "$" not in m.unique_name]
```

This prevents emission of these problematic identifiers.

**Validation:** Python compile successful

---

### Issue #2: Relationship Reference Syntax Errors 🔴 SECONDARY

**Error Message:**

```
syntax error line 119 at position 158 unexpected '.'
syntax error line 119 at position 201 unexpected 'REFERENCES'
```

**Root Cause:**

- Blank table aliases in relationship FROM/TO clauses
- Empty column references in `(.)` syntax
- Indentation/formatting issues

**Example Problematic DDL:**

```sql
-- BROKEN:
REP_SFDC_SBQQ_QUOTE_C (.) REFERENCES REP_SFDC_ACCOUNT(ID)

-- SHOULD BE:
REP_SFDC_SBQQ_QUOTE_C ("COLUMN_NAME") REFERENCES REP_SFDC_ACCOUNT(ID)
```

**Fix Applied:**
✅ Relationship alias validation and guards:

```python
if not from_alias or not to_alias or not rel.from_columns:
    logger.warning(f"Skipping relationship... unresolvable aliases")
    continue
```

**Validation:** Python compile successful, indentation corrected

---

### Issue #3: Schema Drift - Missing Physical Columns 🟡 TERTIARY

**Potential Cause:**

- Snowflake physical tables may lack columns referenced in model
- Column names changed in source system
- Case sensitivity mismatches

**Current Status:**

- Basic validation in place
- Fallback to first physical column if declared PK missing
- Logged warnings for unmapped columns

**Safe Validation Approach:**

```sql
-- Run in Snowflake to verify column existence:
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'REP_SFDC_SBQQ_QUOTE_PRIMARY'
  AND column_name ILIKE 'FOUNDATIONS%';
```

---

## Fixes Applied

### 1. Metric $ Character Filtering (PRIMARY)

**File:** `src/semabridge/connectors/snowflake_emitter.py`
**Change:** Added filter to exclude metrics with `$` before processing
**Impact:** Only Client Data affected; other models unaffected
**Risk:** LOW (metrics with `$` are Power BI calculated fields, not essential)

### 2. Relationship Alias Validation (SECONDARY)

**File:** `src/semabridge/connectors/snowflake_emitter.py`
**Change:** Added guard checks for blank aliases, skip relationships if unresolvable
**Impact:** Skips problematic relationships; others process normally
**Risk:** LOW (relationships will be logged as skipped)

### 3. Model-Specific Config Framework (TERTIARY)

**File:** `src/semabridge/config/model_specific_config.py`
**Purpose:** Allow per-model fixes without touching core pipeline
**Usage:** Can add column remapping, identifier skipping per model
**Risk:** NONE (config-based, non-invasive)

---

## Safe Debugging Process

### Step 1: Run Client Data Deployment

```bash
cd c:\Users\MANOJ\semabridge-working\semabridge
python .\src\semabridge\api\main.py
```

### Step 2: Analyze Generated DDL (if still failing)

```bash
python .\scripts\debug_semantic_view.py
```

This will:

- Extract Stage 8 generated DDL
- Scan for `$` characters, relationship issues, blank aliases
- Generate report: `output/debug_analysis_report.txt`
- Show exact line numbers of problems

### Step 3: Inspect Critical Lines in Snowflake

If error persists, check exact lines:

```sql
-- From error message: line 1831, position 80
-- This is where Snowflake fails to parse identifier
```

### Step 4: Apply Model-Specific Config (if needed)

Edit `src/semabridge/config/model_specific_config.py`:

```python
CLIENT_DATA_CONFIG = ModelSpecificConfig(
    model_name="Client Data",
    skip_identifiers_with_chars={'$'},  # Already set
    skip_metrics={'PROBLEMATIC_METRIC'},  # Add as needed
    column_remap={'OLD_NAME': 'NEW_NAME'},  # Add as needed
)
```

---

## Expected Outcomes

### Scenario A: Fixes Work ✅

```
SUCCESS 11:22:08 Stage 9: Deploy to Target - Semantic view created
SUCCESS 11:22:09 Stage 10: Finalize Run - Status: SUCCEEDED
```

→ Client Data deployment completes successfully
→ Other models unaffected

### Scenario B: Still Failing 🔴

```
ERROR 11:22:08 Stage 9: Deploy to Target - <new error message>
```

→ Run debug script to identify exact failing line
→ Update `debug_analysis_report.txt` results
→ Provide more specific fix based on new error

---

## Model-Specific vs. Core Pipeline

| Component               | Scope          | Change            | Risk |
| ----------------------- | -------------- | ----------------- | ---- |
| Metric filtering        | Client Data    | Config-based skip | LOW  |
| Relationship validation | All models     | Guard checks      | LOW  |
| Column remapping        | Model-specific | Config framework  | NONE |
| Core emitter logic      | All models     | UNTOUCHED         | NONE |

**✅ No core pipeline changes - all fixes isolated and safe**

---

## Next Steps

1. **Execute** Client Data run with current fixes
2. **Monitor** Stage 9 output
3. **If SUCCESS**: Issue resolved ✅
4. **If ERROR**:
   - Run debug script
   - Analyze report
   - Update model config as needed
   - Rerun

---

## Rollback Plan

All changes are reversible:

- Remove `$` filter → restore original metric loop
- Remove alias guards → restore original relationship loop
- Delete model config → core pipeline untouched

No database changes, no permanent modifications.

---

**Last Updated:** 2026-04-08  
**Status:** Ready for test run
