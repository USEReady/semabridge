# Gemini API Integration Architecture

## BEFORE (Broken - 429 Errors)

```
┌─────────────────────────────────────────────────────────────────┐
│                  GeminiDAXTranslator                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  model_preferences = [                                           │
│    "gemini-1.5-flash"      ❌ 0 RPM (free tier)                 │
│    "gemini-2.0-flash"      ❌ 0 RPM (free tier)                 │
│  ]                                                               │
│                                                                   │
│  for model in preferences:                                       │
│    try:                                                          │
│      response = genai.GenerativeModel(model).generate_content()  │
│      # Direct call - no rate limiting!                           │
│      # No exponential backoff                                    │
│      # Instant retry on failure                                  │
│                                                                   │
│    except Exception:                                             │
│      continue  # Try next model                                  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘

❌ PROBLEMS:
   • 429 errors: using 0-quota models
   • No rate limiting: rapid fire requests
   • Instant retries: spam quota
   • No fallback: deployment fails
```

---

## AFTER (Fixed - Stable)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DAX Translator                                    │
│            (Uses Centralized API Service)                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  translate(dax, table_alias):                                        │
│    prompt = build_prompt(dax)                                        │
│                                                                       │
│    response = call_gemini(                                           │
│      prompt,                                                         │
│      fallback_fn=self._generate_fallback_sql()                       │
│    )                                                                  │
│                                                                       │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│           Centralized Gemini API Service                             │
│              (NEW - All Magic Here)                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  MODEL = "gemini-2.5-flash"  ✅ 5 RPM (free tier)                   │
│                                                                       │
│  def call(prompt, fallback_fn):                                      │
│    ┌─ Rate Limiting ─────────────────────────────────────────────┐  │
│    │ if elapsed < 12 seconds:                                     │  │
│    │   wait(12 - elapsed)   ✅ Stay under 5 RPM                  │  │
│    └────────────────────────────────────────────────────────────┘  │
│                                                                       │
│    for attempt in range(3):                                          │
│      try:                                                            │
│        response = genai.GenerativeModel(MODEL).generate_content()    │
│        return response.text                                          │
│      except RateLimitError:                                          │
│        if attempt < 2:                                              │
│          ┌─ Exponential Backoff ────────────────────────────────┐  │
│          │ delay = [15, 30, 60][attempt]  ✅ Intelligent delay  │  │
│          │ time.sleep(delay)                                    │  │
│          └────────────────────────────────────────────────────┘  │
│        else:                                                        │
│          break                                                       │
│                                                                      │
│    ┌─ Fallback Logic ────────────────────────────────────────────┐  │
│    │ if api_failed and fallback_fn:                              │  │
│    │   return fallback_fn(prompt)  ✅ Safe default response      │  │
│    └────────────────────────────────────────────────────────────┘  │
│                                                                      │
│    ┌─ Centralized Logging ───────────────────────────────────────┐  │
│    │ logger.info("Calling gemini-2.5-flash")                    │  │
│    │ logger.warning("Rate limit hit, waiting 15s")              │  │
│    │ logger.info("Used fallback response")                      │  │
│    └────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

✅ BENEFITS:
   • Single model with quota (2.5-flash: 5 RPM)
   • Rate limiting: 1 request per 12 seconds
   • Smart retries: 15s → 30s → 60s exponential backoff
   • Graceful fallback: deployment always succeeds
   • Centralized logging: easy debugging
```

---

## Data Flow - Translation Request

```
translate() Call
      │
      ├─→ Check Cache
      │   └─→ Found? Return immediately (no API call)
      │   └─→ Not found? Continue...
      │
      ├─→ call_gemini(prompt, fallback_fn)
      │   │
      │   ├─ Rate Limit Check
      │   │  └─ Last request < 12s ago?
      │   │     └─ Yes:Wait until 12s elapsed
      │   │
      │   ├─ API Call Attempt (loop up to 3 times)
      │   │  ├─ Try: genai.GenerativeModel("gemini-2.5-flash").generate_content()
      │   │  │
      │   │  ├─ Success? → Return response
      │   │  │
      │   │  ├─ 429 Error? → Exponential backoff (15s, 30s, 60s) then retry
      │   │  │
      │   │  └─ Other error? → Use fallback_fn()
      │   │
      │   └─ All retries exhausted? → Use fallback_fn()
      │
      ├─→ Validate & Score Response
      │   ├─ Parse SQL
      │   ├─ Validate syntax
      │   └─ Score confidence
      │
      ├─→ Cache Valid Results
      │   └─ Save to .llm_dax_cache.json
      │
      └─→ Return GeminiTranslationResult
```

---

## Rate Limiting Example

```
Timeline (showing 5 RPM = 12 second minimum intervals)

11:00:00 - call_gemini("prompt 1")
           └─ API call made
           └─ last_request_time = 11:00:00
           └─ Response: "SUM(...)"

11:00:05 - call_gemini("prompt 2")
           └─ Elapsed: 5 seconds < 12 seconds required
           └─ Sleep 7 seconds
           └─ Wait until exactly 12 seconds elapsed
           └─ API call made at 11:00:12
           └─ last_request_time = 11:00:12

11:00:22 - call_gemini("prompt 3")
           └─ Elapsed: 10 seconds < 12 seconds required
           └─ Sleep 2 seconds
           └─ Wait until exactly 12 seconds elapsed
           └─ API call made at 11:00:24
           └─ last_request_time = 11:00:24

Result: 3 API calls in ~24 seconds = consistently under 5 RPM ✅
```

---

## Retry Logic Example

```
First API Call:
  11:00:00 - Attempt 1: 429 Rate Limit Error
           └─ Wait 15 seconds
           
  11:00:15 - Attempt 2: 429 Rate Limit Error (still recovering)
           └─ Wait 30 seconds
           
  11:00:45 - Attempt 3: 429 Rate Limit Error (still bad luck)
           └─ Max retries reached
           └─ Use fallback: SUM(table.column)
           └─ Return safe response
           
Result: Total wait time: 45 seconds, but deployment continues ✅
        (vs. Before: instant retry loop causing cascade failures ❌)
```

---

## Fallback Generation Example

```
Input DAX:
  "SUMX(Table[Sales], Table[Amount])"

Fallback Processing:
  1. Detect aggregation keyword: "SUMX" → SQL: "SUM"
  2. Extract column: Table[Amount] → AMOUNT
  3. Format: "SUM(table_alias.AMOUNT)"
  
Output:
  "SUM(sales_fact.AMOUNT)"

Deployment continues successfully ✅
```

---

## Configuration

```env
# Required
GEMINI_API_KEY=AIza...                           # API key

# Optional (defaults shown)
GEMINI_MODEL=models/gemini-2.5-flash             # Only model
USE_GEMINI=true                                  # Enable/disable
GEMINI_MIN_REQUEST_INTERVAL=12                   # Rate limit (seconds)
```

---

## Monitoring & Health Check

```python
from semabridge.converter.gemini_api_service import gemini_health_check

health = gemini_health_check()

# Output:
{
    "enabled": true,                              # USE_GEMINI=true
    "available": true,                            # API key valid
    "model": "models/gemini-2.5-flash",          # Always this one
    "quota_rpm": 5,                               # Free tier limit
    "min_interval_seconds": 12.0,                # Rate limiting
    "last_request": "2026-03-17T15:23:45.123",  # Last API call
    "api_key_configured": true,                  # GEMINI_API_KEY set
    "status": "healthy"                          # Overall status
}
```

---

## Error Handling Flow

```
call_gemini() called
      │
      ├─ Is USE_GEMINI=false?
      │  └─ Yes: Use fallback_fn immediately
      │
      ├─ Is GEMINI_API_KEY missing?
      │  └─ Yes: Use fallback_fn immediately
      │
      ├─ Rate limit check & API call
      │  ├─ Success (200): Return response
      │  ├─ 429 (Rate limit): Retry with backoff
      │  │  ├─ After 3 retries still failing: Use fallback_fn
      │  └─ Other error (500, etc.): Use fallback_fn immediately
      │
      └─ Return response (from API or fallback)
         └─ Never throws error to caller
            └─ Graceful degradation always
```

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Architecture** | Multiple models tried sequentially | Single centralized service |
| **Rate Limiting** | None - instant fire | 1 request per 12 seconds |
| **Retries** | Instant (causes cascade failures) | Exponential backoff (15s, 30s, 60s) |
| **Fallback** | None - crashes | Deterministic SQL generation |
| **Logging** | Basic | Detailed with timestamps |
| **Config** | Hard-coded | Environment variables |
| **Reliability** | 429 errors frequent | Stay within quota |
| **Failure Mode** | Deployment fails | Graceful degradation |

---

**Result**: Stable, quota-aware, resilient Gemini API integration ✅
