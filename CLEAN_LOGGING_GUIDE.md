# Clean Backend Logging Guide

## What You'll See When Running Backend

When you start the backend with:
```powershell
python run_backend.py
```

The terminal will show **clean, user-focused logs only**:

### Startup Phase (Essential Info)

```
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

That's it! No verbose database initialization logs, no MSAL warmup messages, no credential injection spam.

### During Deployment (What Matters)

When you deploy configurations from the frontend, you'll see:

```
[REQUEST] GET /api/discovery/fabric
[RESPONSE] GET /api/discovery/fabric -> 200 (1.23s)

[REQUEST] POST /api/config/generate
[RESPONSE] POST /api/config/generate -> 200 (0.45s)

[REQUEST] POST /api/config/validate
[RESPONSE] POST /api/config/validate -> 200 (0.88s)

[REQUEST] POST /api/sync
[RESPONSE] POST /api/sync -> 200 (45.67s)
```

### What Each Line Means

| Log Entry | What It Shows |
|-----------|---|
| `[REQUEST] POST /api/sync` | Frontend sent a deployment request |
| `[RESPONSE] ... -> 200` | Request succeeded (200 = OK) |
| `(45.67s)` | How long the operation took |
| `[RESPONSE] ... -> 400` | Request failed (client error) |
| `[RESPONSE] ... -> 500` | Request failed (server error) |

### If Something Goes Wrong

If you see a **400 or 500 status code**:
```
[REQUEST] POST /api/sync
[RESPONSE] POST /api/sync -> 500 (2.34s)
```

The response code tells you the issue:
- **400** - Bad request (invalid YAML, missing fields, etc.)
- **401** - Authentication failed (credentials issue)
- **404** - Endpoint not found
- **500** - Server error (check logs for details)

### What's Hidden (Debug Only)

These verbose logs are now hidden in normal operation:
- ✓ SQLAlchemy query logs
- ✓ Connection pool noise
- ✓ Alembic migration details
- ✓ DuckDB/Snowflake initialization messages
- ✓ MSAL authentication warmup
- ✓ Credential injection details
- ✓ Uvicorn/Starlette framework noise

**If you need them for debugging:**
Set the log level to DEBUG when running:
```powershell
# Future enhancement - for now, verbose logs can be enabled
# by modifying src/semabridge/utils/logger.py
```

### Actual Examples from Real Deployments

#### Successful Discovery
```
[REQUEST] GET /api/discovery/fabric?workspace_id=d875c0c3-59e9-4d55-a7f0-99595b756718
[RESPONSE] GET /api/discovery/fabric -> 200 (1.56s)
```

#### Successful Config Generation
```
[REQUEST] POST /api/config/generate
[RESPONSE] POST /api/config/generate -> 200 (0.34s)
```

#### Successful Full Deployment
```
[REQUEST] POST /api/sync
[RESPONSE] POST /api/sync -> 200 (89.23s)
```
*89 seconds is normal for a full deployment - Fabric and Snowflake sync take time*

#### Error Example
```
[REQUEST] POST /api/config/validate
[RESPONSE] POST /api/config/validate -> 400 (0.21s)
```
*Quick failure indicates a validation issue in the YAML config*

### Performance Indicators

- **< 2 seconds**: Discovery endpoints (Fabric/Snowflake)
- **< 1 second**: Config generation/validation
- **30-120 seconds**: Full sync/deployment (depends on model count)

If times are significantly longer, check:
- Network connectivity
- Database performance
- Fabric/Snowflake API availability

## Summary

✅ **Clutter removed** - No debug spam
✅ **User-friendly** - Shows only what you need to know
✅ **Request tracking** - See every operation with timing
✅ **Error visibility** - Status codes help diagnose issues
✅ **Path context** - Knows which route is being called

Just watch the terminal while using the frontend - it's now clear what's happening!
