# DEPLOYMENT LOGGING FIX GUIDE

## The Issue

When you click "Deploy" in the frontend, you should see detailed logs on the backend terminal showing each step of the deployment process. However, the logs are invisible because **Python output is being buffered**.

## Why This Happens

By default, Python buffers output when running in non-interactive mode (like a terminal running a server). This means logs are held in memory and never flushed to the console until:
- The buffer is full
- The process exits
- The buffer is explicitly flushed

## The Solution

### Option 1: Use the Backend Startup Script (RECOMMENDED)

Run the backend using the provided startup script which ensures unbuffered output:

```bash
python run_backend.py
```

This script:
- Runs Python with the `-u` (unbuffered) flag
- Ensures all logs appear in real-time
- Shows you what's happening during deployment

### Option 2: Run Backend with -u Flag Directly

If you prefer to run manually:

```bash
python -u src/semabridge/api/main.py
```

The `-u` flag tells Python to run in unbuffered mode.

### Option 3: Set Environment Variable (Cross-Platform)

On Windows PowerShell:
```powershell
$env:PYTHONUNBUFFERED=1
python src/semabridge/api/main.py
```

On macOS/Linux:
```bash
export PYTHONUNBUFFERED=1
python src/semabridge/api/main.py
```

## Verification

After starting the backend with one of the above methods:

1. Open the frontend at http://localhost:5174
2. Click "Deploy" (or trigger a sync)
3. On the backend terminal, you should NOW see logs like:

```
[timestamp] INFO     Step 1: Configuration initialized
[timestamp] INFO     Step 2: Validating source format
[timestamp] INFO     Step 3: Extracting source model...
[timestamp] INFO     Step 4: Translating to semantic format...
[timestamp] INFO     Step 5: Generating Snowflake DDL...
[timestamp] INFO     Step 6: Preparing Snowflake schema
[timestamp] INFO     Step 7: Deploying to Snowflake
[timestamp] INFO     Step 8: Verifying deployment
[timestamp] INFO     Step 9: Querying deployed artifacts
```

If you see these logs appearing in real-time, the issue is **FIXED**.

## What Was Changed

To ensure proper logging:

1. **Logger Configuration** ([src/semabridge/utils/logger.py](src/semabridge/utils/logger.py)):
   - Updated `Console` initialization with `force_terminal=True` to ensure output works in all contexts
   - Added `show_level=True` to RichHandler for clearer log levels
   - Ensured stdout stream is properly assigned to handlers

2. **Backend Startup Script** ([run_backend.py](run_backend.py)):
   - New easy-to-use script that runs backend with unbuffered output
   - Provides startup confirmation messages

3. **This Guide** ([LOGGING_FIX_GUIDE.md](LOGGING_FIX_GUIDE.md)):
   - Explains the issue and provides multiple solutions

## Troubleshooting

### Still not seeing logs?

1. **Verify backend is running:**
   ```bash
   curl http://localhost:8000/api/health
   ```
   Should return: `{"status": "ok", "service": "semabridge-api", ...}`

2. **Check if you're using an old terminal session:**
   - Close and restart your terminal
   - Ensure you're running the backend command in the current terminated session

3. **Verify the frontend is actually calling the backend:**
   - Open browser DevTools (F12)
   - Go to Network tab
   - Click Deploy
   - Look for `/api/sync` POST request
   - Verify it returns 200 and shows "success"

4. **Check for errors in the logs:**
   - Look for `ERROR` or `ERROR` level logs
   - If you see errors, the deployment might be failing
   - Error messages will give you details on what went wrong

### Logs appearing but with encoding issues?

If you see weird characters or encoding issues:

1. Verify your terminal encoding is UTF-8 in Windows:
   ```powershell
   chcp 65001  # Set to UTF-8
   ```

2. Then run the backend:
   ```bash
   python -u src/semabridge/api/main.py
   ```

## Next Steps

Once you can see the deployment logs clearly:

1. **Monitor the entire sync process** - You'll see exactly what's happening at each step
2. **Identify any errors** - If deployment fails, you'll see the specific error step
3. **Debug issues faster** - Real-time logs make it much easier to diagnose problems

## Technical Details

For developers interested in the logging system:

- **Main Logger**: `src/semabridge/utils/logger.py` - Centralized logging configuration
- **Handler Types**: 
  - RichHandler (console) - Pretty formatted output
  - RotatingFileHandler (file) - log file at `~/.semabridge/logs/semabridge.log`
  - WebSocketAlertHandler (async) - Sends warnings/errors to frontend
- **Log Levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL
- **File Handler**: Rotates at 10 MB with 5 backup files

---

**TL;DR**: Run `python run_backend.py` instead of `python src/semabridge/api/main.py` and all logs will appear in real-time!
