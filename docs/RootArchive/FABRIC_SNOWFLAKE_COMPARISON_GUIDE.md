# Fabric ↔ Snowflake Sync Comparison Tools

I've created three comparison scripts to analyze what's synced between your Fabric model and Snowflake:

## Scripts Created

### 1. **test_fabric_snowflake_analysis.py** (RECOMMENDED)
Command-line tool to compare a specific model.

**Usage:**
```bash
# List all available Fabric models
python test_fabric_snowflake_analysis.py --list

# Analyze a specific model (replace <MODEL_ID> with actual ID from list)
python test_fabric_snowflake_analysis.py <MODEL_ID> "Display Name"

# Example:
python test_fabric_snowflake_analysis.py "abc-123-def-456" "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"
```

**Output Shows:**
- ✓ Tables synced between Fabric and Snowflake
- ✗ Tables in Fabric but NOT synced to Snowflake (ACTION REQUIRED!)
- ⚠ Column count mismatches between platforms
- Column-by-column comparison for synced tables
- Metrics defined in Fabric
- Relationships/references
- Summary with action items

---

### 2. **test_fabric_snowflake_sync_compare_interactive.py**
Interactive script that walks you through the comparison.

**Usage:**
```bash
python test_fabric_snowflake_sync_compare_interactive.py
# Follow the prompts to select a model
```

---

### 3. **test_fabric_snowflake_sync_comparison.py**
Automated comparison (for known models by display name).

**Usage:**
```bash
python test_fabric_snowflake_sync_comparison.py
# Requires COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC to exist by exact name
```

---

## Output Interpretation

### Status Indicators
- `[OK]` - Synced successfully
- `[MISMATCH]` - Table exists but column count differs
- `[MISSING]` - Table/column in Fabric but not in Snowflake (needs sync)
- `[EXTRA]` - Table in Snowflake but not in Fabric

### Action Items
The tool will suggest what needs to be done:
1. **Sync Missing Tables** - Tables that exist in Fabric but weren't synced to Snowflake
2. **Fix Column Mismatches** - Tables exist in both but have different columns
3. **Verify Metrics** - Check if all metrics were properly converted
4. **Check Relationships** - Ensure foreign keys and relationships are set up

---

## Quick Start

### Step 1: Install Dependencies
Make sure `requirements.txt` includes:
- `snowflake-connector-python`
- `requests`
- `msal` (for Fabric auth)

### Step 2: Configure .env
Ensure .env has both Fabric and Snowflake credentials:
```
# Fabric
FABRIC_TENANT_ID=xxx
FABRIC_CLIENT_ID=xxx
FABRIC_WORKSPACE_ID=xxx
FABRIC_CLIENT_SECRET=xxx (optional, for service principal auth)

# Snowflake
SNOWFLAKE_ACCOUNT=xxx
SNOWFLAKE_USER=xxx
SNOWFLAKE_PASSWORD=xxx
SNOWFLAKE_WAREHOUSE=xxx
SNOWFLAKE_DATABASE=xxx
SNOWFLAKE_SCHEMA=xxx
```

### Step 3: Run Comparison
```bash
# First, list available models
python test_fabric_snowflake_analysis.py --list

# Then analyze your model
python test_fabric_snowflake_analysis.py <YOUR_MODEL_ID> "Your Model Name"
```

---

## Example Output

```
================================================================================
FABRIC <-> SNOWFLAKE SYNC ANALYSIS
Model: COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC
================================================================================

[SUMMARY]
  Fabric Tables:     18
  Synced:            16 (88.9%)
  NOT Synced:        2
  Extra in SF:       1
  Metrics:           42
  Relationships:     5

[SYNCED TABLES] (16)
  [OK]       PRODUCTS                           Fabric: 12 cols  Snowflake: 12 cols
  [OK]       CUSTOMERS                          Fabric: 15 cols  Snowflake: 15 cols
  [MISMATCH] SALES_FACT                         Fabric: 25 cols  Snowflake: 23 cols
       [NOT IN SF] INTERNAL_ID, TEMP_FLAG
       [EXTRA IN SF] SYNC_DATE

[NOT SYNCED - IN FABRIC ONLY] (2)
  [MISSING] INTERNAL_CACHE_TABLE              8 columns
  [MISSING] TEMP_STAGING_DATA                 12 columns

[ACTION ITEMS]
1. SYNC MISSING TABLES (2 tables)
   1. INTERNAL_CACHE_TABLE
   2. TEMP_STAGING_DATA

2. FIX COLUMN MISMATCHES (1 tables)
   1. SALES_FACT (Fabric: 25 cols, Snowflake: 23 cols)
```

---

## Common Issues

### Issue: Model not found
**Cause:** Model ID or name doesn't match exactly
**Solution:** Run with `--list` flag to see exact names and IDs

### Issue: Snowflake connection failed
**Cause:** Network, credentials, or warehouse not running
**Solution:** Test manually: `snowsql -a ACCOUNT -u USER -d DATABASE`

### Issue: Missing columns in Snowflake
**Cause:** Sync may have been incomplete or columns added after initial sync
**Solution:** Re-run sync from backend or manually add missing columns

### Issue: Extra columns in Snowflake
**Cause:** Snowflake may have additional tracking/system columns
**Solution:** Review if safe to ignore or clean up

---

## Removed Logging

As requested, the backend no longer outputs verbose conversion details. It only logs:
- ✓ INFO messages for important operations
- ⚠ WARNING messages for issues
- ✗ ERROR messages for failures

This keeps the output clean and focused on actionable information.

---

## Next Steps

1. **Run the comparison** for each model you've synced
2. **Review missing tables** and decide if they should be synced
3. **Investigate column mismatches** to understand what differs
4. **Take action** to sync missing tables or fix column differences
5. **Re-run comparison** to verify the sync is complete

For questions or issues, check the logs in:
- `output/logs/` - Application logs
- `output/debug/` - Debug artifacts
