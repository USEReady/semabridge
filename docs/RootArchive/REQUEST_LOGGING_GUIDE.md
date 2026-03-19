#!/usr/bin/env python3
"""
Guide: How to see request/response logging in the backend
"""

print("""
╔════════════════════════════════════════════════════════════════════════════╗
║                 REQUEST/RESPONSE LOGGING IS NOW ENABLED                    ║
╚════════════════════════════════════════════════════════════════════════════╝

WHAT CHANGED:
─────────────
Added RequestResponseLoggingMiddleware to the FastAPI backend.

This middleware now logs:
  ✓ All HTTP requests (GET, POST, PUT, DELETE)
  ✓ Request method, path, and query parameters
  ✓ Request headers (with auth tokens redacted)
  ✓ Request body for POST/PUT (with sensitive data redacted)
  ✓ Response status code and response time

HOW TO USE IT:
──────────────
1. Start the backend:
   
   cd c:\\Users\\chara\\semabridge_merged
   python run_backend.py

2. Watch the terminal output - you'll see logs like:

   [REQUEST] GET /api/discovery/fabric
     Headers: {'accept': 'application/json', 'authorization': '***REDACTED***'}
   [RESPONSE] GET /api/discovery/fabric -> 200 (1.23s)

3. Open the frontend at http://localhost:5174

4. Click "Source Browser" to trigger discovery

5. Watch the terminal where backend is running - you'll see all requests!

EXAMPLE LOG OUTPUT:
───────────────────

When you deploy a configuration:

   [REQUEST] POST /api/sync
     Headers: {'accept': 'application/json', 'content-type': 'application/json'}
     Body: {'content': 'version: 2\\nsources:\\n  - name: Fabric\\n...'}
   [RESPONSE] POST /api/sync -> 200 (45.32s)

   [REQUEST] POST /api/config/validate
     Headers: {'content-type': 'application/json'}
     Body: {'content': 'version: 2\\nsources:\\n  - name: Snowflake\\n...'}
   [RESPONSE] POST /api/config/validate -> 200 (2.15s)


WHAT YOU'LL SEE:
────────────────
✓ Every API call from the frontend
✓ Request/response times (helps identify where slowdowns occur)
✓ Response status codes (200, 400, 401, 500, etc.)
✓ Request bodies (so you can see what data is being sent)
✓ Response times in seconds (great for performance debugging)

EXCLUDING LOGS:
────────────────
Health checks to /api/health are skipped to avoid spam.
All sensitive data (passwords, tokens, secrets) are redacted as "***REDACTED***"

HOW TO CUSTOMIZE:
──────────────────
Edit: src/semabridge/api/main.py
Look for: class RequestResponseLoggingMiddleware

You can:
  • Add more endpoints to skip (line: if request.url.path == ...)
  • Increase log detail level
  • Change what gets redacted


TROUBLESHOOTING:
─────────────────
Q: I don't see the logs
A: Make sure:
   1. Backend is running with: python run_backend.py
   2. Not in a background terminal where output is hidden
   3. Look for "[REQUEST]" and "[RESPONSE]" lines in the log

Q: Logs show status 500
A: Check the full error response in the logs
   The response body will show what went wrong

Q: Too many logs
A: You can add more endpoints to skip list (like /docs, /redoc, etc.)


NEXT STEPS:
────────────
1. Restart backend: python run_backend.py
2. Watch logs while using the frontend
3. Check discovery, validation, and deployment operations
4. Share logs if you need help debugging

═════════════════════════════════════════════════════════════════════════════
""")
