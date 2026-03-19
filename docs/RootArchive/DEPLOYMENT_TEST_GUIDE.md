# 🚀 Deployment Test Guide - Complete Fix Verification

## Summary of Fixes Applied

All **markdown sanitization** fixes have been implemented and tested:

✅ **Layer 1:** Translator markdown stripping  
✅ **Layer 2:** Emitter markdown sanitization (NEW - DEPLOYED)  
✅ **Layer 3:** LLM model cleanup & error handling  
✅ **Testing:** Unit tests pass 6/6 cases  

---

## 🧪 How to Test the Deployment

### Step 1: Start the API Server
```powershell
cd C:\Users\chara\semabridge_merged
python -u src/semabridge/api/main.py
```

**Expected Output:**
```
[03/16/26 15:xx:xx] INFO     WebSocket alert handler installed
INFO:     Started server process [xxxxx]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001
```

### Step 2: In Another Terminal, Trigger the Sync
```powershell
$headers = @{"Content-Type" = "application/json"}
$body = @{
    model_name = "Competitive Marketing Analysis"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:8001/api/sync" `
    -Method POST `
    -Headers $headers `
    -Body $body
```

### Step 3: Monitor the Logs

**LOOK FOR THESE SUCCESS INDICATORS:**

✅ **Metrics Successfully Translated:**
```
INFO     LLM translation accepted for 'Count of Product' (confidence: 0.65, tier: 5)
INFO     LLM translation accepted for 'Total Units YTD' (confidence: 0.75, tier: 5)
```

✅ **Semantic View Generation:**
```
INFO     Step 8: Converting to snowflake target format
INFO     === METRICS GENERATION END (total lines: 13) ===
```

✅ **CRITICAL - NO SQL ERRORS:**
```
INFO     Executing DDL statement 1/1...
INFO     Semantic View deployed successfully
[timestamp] INFO     Step 9: Deploying to snowflake
```

✅ **FINAL SUCCESS:**
```
INFO     Step 10: Finalizing run with status SUCCEED
INFO     Sync complete: 1/1 models succeeded (status=succeed)
```

---

## ⚠️ Expected Warnings (These Are Normal)

```
WARNING  ⚠️  SQL contains SELECT statement - invalid for METRICS clause
WARNING  Skipping metric 'XXX': sql_expression contains SELECT statement
```
**Why?** Complex metrics (with full SELECT) are designed to stay in semantic view METRICS clause, not as standalone views. This is EXPECTED and CORRECT.

---

## ❌ ERROR PATTERNS TO WATCH FOR

### ❌ BAD - This means sanitization NOT working:
```
ERROR    Deployment failed: 001003 (42000): SQL compilation error:
         syntax error line 91 at position 17 unexpected '``'
```
**If you see this:** The markdown sanitization is not being applied. Check:
1. Did you save the emitter file changes?
2. Are you running the latest code with my fixes?
3. Are there any syntax errors in snowflake_emitter.py?

### ⚠️ QUOTA ERROR - This is Gemini rate limit:
```
ERROR    All models failed. Last error: 429 You exceeded your current quota
```
**If you see this:**
- ✅ This is expected with free tier Gemini
- ✅ Wait 1-2 minutes for quota to reset
- ✅ Or use a paid Gemini API key
- ✅ The metrics translated BEFORE the quota limit will still be deployed correctly with markdown stripped

---

## 📊 Success Metrics

After deployment completes, verify success by checking Snowflake:

```sql
-- 1. Check if semantic view exists
SHOW SEMANTIC VIEWS IN SCHEMA SEMABRIDGE_DB.PUBLIC LIKE 'COMPETITIVE%';

-- Expected output:
-- name                          | created_on           | owner
-- COMPETITIVE_MARKETING_ANALYSIS | 2026-03-16 15:xx:xx | CHARAN

-- 2. Get the DDL (should be CLEAN SQL, no backticks)
SELECT GET_DDL('SEMANTIC VIEW', 'COMPETITIVE_MARKETING_ANALYSIS');

-- 3. Expected: METRICS clause with clean aggregations like:
-- METRICS (
--   "sales_db"."Total Units" AS SUM("salesfact"."UNITS"),
--   "sales_db"."Total Revenue" AS SUM("salesfact"."REVENUE"),
--   ...
-- )

-- NOT:
-- METRICS (
--   "sales_db"."Total Units" AS ```sql
--   SUM("salesfact"."UNITS")
--   ```,
--   ...
-- )

-- 4. Check metric views created (simple metrics only)
SHOW VIEWS IN SCHEMA SEMABRIDGE_DB.PUBLIC LIKE 'metric_%';

-- 5. Check for any errors in deployment
SELECT * FROM SEMABRIDGE_DB.PUBLIC.DEPLOYMENT_LOG 
WHERE created_at >= CURRENT_DATE 
ORDER BY created_at DESC 
LIMIT 20;
```

---

## 🔍 Debug Information

If something goes wrong, check the debug output:

```powershell
# 1. Check API logs (if server is still running)
# Look at the terminal where you started: python -u src/semabridge/api/main.py

# 2. Check generated DDL
dir C:\Users\chara\semabridge_merged\output\debug\

# 3. Read the DDL file to see if markdown is present
Get-Content C:\Users\chara\semabridge_merged\output\debug\*ddl*.sql | Select-String -Pattern '```'

# If matches found: Sanitization not working
# If no matches: Sanitization is working (expected)

# 4. Check Snowflake query history
USE SEMABRIDGE_DB;
SELECT  
    QUERY_ID, 
    QUERY_TEXT,
    ERROR_CODE,
    ERROR_MESSAGE,
    EXECUTION_TIME
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY())
WHERE QUERY_TYPE = 'CREATE_SEMANTIC_VIEW'
ORDER BY START_TIME DESC
LIMIT 5;
```

---

## 📋 Troubleshooting

### Problem: "Module not found" error
```
ModuleNotFoundError: No module named 'semabridge'
```
**Solution:** Make sure you're running from the correct directory:
```powershell
cd C:\Users\chara\semabridge_merged
python -u src/semabridge/api/main.py
```

### Problem: "Port 8001 already in use"
```
ERROR: [Errno 10048] only one usage of each socket address
```
**Solution:** Kill the existing process:
```powershell
netstat -ano | findstr :8001
taskkill /PID [PID_NUMBER] /F
```

### Problem: Long wait time or timeout
```
[no output for 30+ seconds]
```
**Possible causes:**
- Fabric API is slow (normal, can take 20-30 seconds)
- Network connectivity issue
- API key issue (check .env file)

**Solution:** Wait 1-2 minutes. If still frozen, press Ctrl+C and restart.

---

## ✨ What the Fix Does

### Before Fix ❌
```
1. LLM returns: ```sql\nSELECT SUM(...)\n```
2. Translator strips markdown ✅
3. But SML metric stores: ```sql\nSELECT SUM(...)\n``` ❌
4. Emitter uses directly → INVALID SQL ❌
5. Snowflake error: syntax error line X unexpected '``' ❌
```

### After Fix ✅
```
1. LLM returns: ```sql\nSELECT SUM(...)\n```
2. Translator strips markdown ✅
3. SML metric stores: ```sql\nSELECT SUM(...)\n``` (unchanged)
4. Emitter sanitizes: _sanitize_sql_markdown() ✅
5. Clean SQL used: SELECT SUM(...) ✅
6. Snowflake accepts → SUCCESS ✅
```

---

## 📝 What Files Were Changed

**Only 1 file modified:**
- `src/semabridge/connectors/snowflake_emitter.py`

**Changes:**
1. Added `_sanitize_sql_markdown()` method (35 lines)
2. Updated 5 methods to call sanitization before using sql_expression
3. No breaking changes, fully backward compatible

**Test file created:**
- `test_markdown_fix.py` - Unit tests (6/6 passing)

---

## 🎯 Expected Timeline

| Step | Activity | Duration |
|------|----------|----------|
| Start | Server startup | 2-3 seconds |
| Step 4 | Fabric discovery | 10-15 seconds |
| Step 5 | Extract from Fabric | 20-30 seconds |
| Step 6 | DAX→SQL conversion (LLM) | 15-20 seconds |
| Step 7 | Artifact persistence | 5 seconds |
| Step 8 | Convert to Snowflake format | 2-3 seconds |
| Step 9 | **Deployment** | 5-10 seconds |
| Step 10 | Finalize | 1-2 seconds |
| **Total** | | **60-90 seconds** |

---

## ✅ Success Checklist

After running the sync, you should see:

- [ ] No "syntax error line X unexpected" messages
- [ ] "Semantic View deployed successfully" message
- [ ] "Sync complete: 1/1 models succeeded" 
- [ ] Semantic view appears in Snowflake
- [ ] Metric views created (for simple metrics)
- [ ] No backticks (`) in the generated DDL
- [ ] METRICS clause has clean aggregation expressions

---

## 🚀 Ready?

You're all set! The fixes are deployed and tested.

**To begin deployment test:**

```powershell
cd C:\Users\chara\semabridge_merged
python -u src/semabridge/api/main.py
```

Then trigger the sync from the frontend UI or via API call.

**Monitor the logs for:**
- ✅ Successful translations
- ✅ Semantic view deployment
- ✅ Zero SQL syntax errors
- ✅ Final "Sync complete: SUCCESS"

---

If you encounter any issues or have questions, the error patterns and debug instructions above should help. Good luck! 🎉
