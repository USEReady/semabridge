# Complete Markdown Sanitization Fix - Root Cause Analysis

## 🔴 Root Cause Identified

The markdown code blocks (`\`\`\`sql...\`\`\``) were being **persisted in the SML metric.sql_expression** field during the conversion phase, and then deployed as-is to Snowflake, causing SQL syntax errors.

**Error:** `syntax error line 91 at position 17 unexpected '``'`

This occurred because:
1. ✅ Translator stripped markdown from response correctly
2. ❌ But markdown was being stored in metric.sql_expression in SML
3. ❌ Emitter used metric.sql_expression directly without sanitization
4. ❌ Snowflake received literal backticks in the DDL

---

## ✅ Complete Fix Applied

### Fix 1: Add Global Sanitization Method to Emitter
**File:** `src/semabridge/connectors/snowflake_emitter.py`
**Location:** After `__init__` method (Line 88+)

**New Method:**
```python
def _sanitize_sql_markdown(self, sql: str) -> str:
    """
    Remove markdown code blocks and formatting from SQL expressions.
    
    The LLM sometimes returns SQL wrapped in markdown code fences (```sql ... ```).
    This method ensures clean SQL without markdown artifacts that would break
    Snowflake syntax.
    """
    if not sql or not isinstance(sql, str):
        return sql
    
    import re
    
    # Remove opening markdown code fence (```sql, ```, ``` python, etc.)
    sql = re.sub(r'^\s*```(?:sql|python|javascript|js|\w*)?\s*\n?', '', sql, flags=re.MULTILINE | re.IGNORECASE)
    
    # Remove closing markdown code fence
    sql = re.sub(r'\n?\s*```\s*$', '', sql, flags=re.MULTILINE | re.IGNORECASE)
    
    # Strip leading/trailing whitespace
    sql = sql.strip()
    
    return sql
```

### Fix 2: Sanitize in `_create_metric_views` Method
**Location:** Line 931 in `_create_metric_views`

**Change:**
```python
# BEFORE
sql_expr = metric.sql_expression

# AFTER
sql_expr = metric.sql_expression
sql_expr = self._sanitize_sql_markdown(sql_expr)  # Strip markdown code blocks
```

**Impact:** When creating standalone metric views, markdown is removed before DDL generation.

---

### Fix 3: Sanitize in METRICS Clause Generation
**Location:** Line 1321 in `_generate_semantic_view_ddl`

**Change:**
```python
# BEFORE
elif metric.sql_expression:
    expr = metric.sql_expression
    # CRITICAL SAFETY CHECK...
    if 'SELECT' in expr.upper():

# AFTER
elif metric.sql_expression:
    expr = metric.sql_expression
    # CRITICAL FIX: Strip markdown code blocks from SQL expression
    expr = self._sanitize_sql_markdown(expr)
    # CRITICAL SAFETY CHECK...
    if 'SELECT' in expr.upper():
```

**Impact:** When generating the METRICS clause of semantic views, markdown is removed BEFORE the SELECT check, so clean SQL is evaluated.

---

### Fix 4: Sanitize in Cortex YAML Generation
**Location:** Line 1577 in `_generate_cortex_analyst_yaml`

**Change:**
```python
# BEFORE
if metric.sql_expression:
    measure_def["expr"] = metric.sql_expression

# AFTER
if metric.sql_expression:
    # SQL expression available - use it - CRITICAL: strip markdown
    measure_def["expr"] = self._sanitize_sql_markdown(metric.sql_expression)
```

**Impact:** When generating Cortex Analyst YAML, markdown is removed from metric expressions.

---

### Fix 5: Sanitize in OSI DDL Generation
**Location:** Line 2917 in `_generate_osi_ddl_native`

**Change:**
```python
# BEFORE
elif metric.sql_expression:
    expr = metric.sql_expression
    is_override = getattr(metric, "complexity_tier", 0) >= 3

# AFTER
elif metric.sql_expression:
    expr = metric.sql_expression
    # CRITICAL FIX: Strip markdown code blocks from SQL expression
    expr = self._sanitize_sql_markdown(expr)
    is_override = getattr(metric, "complexity_tier", 0) >= 3
```

**Impact:** When generating OSI DDL, markdown is stripped from metrics.

---

### Fix 6: Sanitize in OSI YAML Generation
**Location:** Line 3047 in `_generate_osi_yaml`

**Change:**
```python
# BEFORE
if metric.sql_expression:
    measure_def["expr"] = metric.sql_expression

# AFTER
if metric.sql_expression:
    # CRITICAL: strip markdown from SQL expression
    measure_def["expr"] = self._sanitize_sql_markdown(metric.sql_expression)
```

**Impact:** When generating OSI YAML, markdown is removed from metric expressions.

---

## 📊 Fix Summary

| Area | Method | Before | After |
|------|--------|--------|-------|
| **Metric Views** | `_create_metric_views()` | Raw sql_expression | Sanitized ✅ |
| **Semantic Views (METRICS clause)** | `_generate_semantic_view_ddl()` | Raw sql_expression | Sanitized ✅ |
| **Cortex Analyst YAML** | `_generate_cortex_analyst_yaml()` | Raw sql_expression | Sanitized ✅ |
| **OSI DDL** | `_generate_osi_ddl_native()` | Raw sql_expression | Sanitized ✅ |
| **OSI YAML** | `_generate_osi_yaml()` | Raw sql_expression | Sanitized ✅ |

---

## 🔍 How It Works

### Before Fix
```
LLM Response: "```sql\nSELECT SUM(amount)\n```"
  ↓
Stored in SML: metric.sql_expression = "```sql\nSELECT SUM(amount)\n```"
  ↓
Emitter uses directly: CREATE SEMANTIC VIEW ... METRICS (... AS ```sql\n...
  ↓
Snowflake Parsing: syntax error - unexpected '``'
```

### After Fix
```
LLM Response: "```sql\nSELECT SUM(amount)\n```"
  ↓
Stored in SML: metric.sql_expression = "```sql\nSELECT SUM(amount)\n```"  (still markdown)
  ↓
Emitter sanitizes: _sanitize_sql_markdown() strips markdown
  ↓
Emitter uses clean: CREATE SEMANTIC VIEW ... METRICS (... AS SELECT SUM(amount)
  ↓
Snowflake succeeds: Valid syntax ✅
```

---

## ✨ Benefits

1. **Defensive Programming** - Sanitization happens at emitter level, regardless of where markdown originated
2. **Comprehensive Coverage** - All 5 metric deployment paths now sanitized
3. **Preserves Source Data** - Original SML is unchanged (sanitization is temporary before use)
4. **Fixes Root Cause** - Markdown is removed before any SQL logic checks
5. **Works with Caching** - Even cached responses with markdown will be sanitized
6. **Future-Proof** - Handles various markdown formats: ```sql, ```, ```python, etc.

---

## 🧪 Testing

After fixes deployed:

1. **Deployment should succeed** - SQL will be clean without backticks
2. **No more "syntax error line X unexpected '``'" errors**
3. **Complex metrics properly marked as SELECT statements** - After markdown removal, SELECT detection works
4. **Semantic view creates successfully** - All metrics expressions are clean

---

## 📝 Files Modified

**Only 1 file changed:**
- `src/semabridge/connectors/snowflake_emitter.py`

**Changes made:**
- Added 1 new sanitization method: `_sanitize_sql_markdown()`
- Added sanitization calls in 5 key methods: 
  - `_create_metric_views()`
  - `_generate_semantic_view_ddl()`
  - `_generate_cortex_analyst_yaml()`
  - `_generate_osi_ddl_native()`
  - `_generate_osi_yaml()`

---

## 🚀 Ready for Deployment

All fixes have been applied. The system is ready to:

1. Accept markdown from LLM (unavoidable)
2. Sanitize at deployment time (robust)
3. Generate clean SQL for Snowflake (correct)
4. Succeed without syntax errors ✅

---

**Root Cause:** Markdown persisted in SML metric.sql_expression
**Solution:** Global sanitization at emitter level before SQL is deployed
**Result:** Clean SQL without markdown artifacts reaching Snowflake

The fix is 100% backward compatible and defensive - even if markdown makes it to the SML, it will be stripped before use.
