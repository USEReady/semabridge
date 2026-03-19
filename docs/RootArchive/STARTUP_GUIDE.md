#!/usr/bin/env python3
"""
Quick Start Guide - Direct Backend Launch

This shows how to start the backend and frontend for development.
"""

print("""
╔════════════════════════════════════════════════════════════════════════════╗
║              SEMABRIDGE - DIRECT BACKEND LAUNCH GUIDE                      ║
╚════════════════════════════════════════════════════════════════════════════╝

SETUP:
======

The backend is now directly runnable from main.py

Frontend is configured to connect to: http://127.0.0.1:8000
Backend is configured to run on: 127.0.0.1:8000

QUICK START:
============

Terminal 1 - Start Backend:
---------------------------
cd c:\\Users\\chara\\semabridge_merged
python -u src/semabridge/api/main.py

Expected output:
  INFO:     Waiting for application startup.
  INFO:     Application startup complete.
  [REQUEST] GET /api/discovery/fabric
  [RESPONSE] GET /api/discovery/fabric -> 200 (1.23s)


Terminal 2 - Start Frontend:
----------------------------
cd c:\\Users\\chara\\semabridge_merged\\frontend
npm run dev

Expected output:
  VITE v7.3.1  ready in XXX ms
  ➜  Local:   http://localhost:5174


Terminal 3 - Open Browser:
-------------------------
Open: http://localhost:5174

Then:
1. Click "Source Browser"
2. Select "Fabric" as source type
3. Click the Discover button
4. Watch backend logs show requests/responses
5. Models should appear in the tree


WHAT YOU'LL SEE:
================

Backend Terminal Logs:
  INFO:     Waiting for application startup.
  INFO:     Application startup complete.
  [REQUEST] GET /api/discovery/fabric
  [RESPONSE] GET /api/discovery/fabric -> 200 (1.23s)

Frontend UI:
  ✓ Fabric models appear in Source Browser
  ✓ Can select models
  ✓ Can generate YAML config
  ✓ Can deploy to Snowflake


IMPORTANT NOTES:
================

✓ Backend runs on port 8000 (configurable in main.py line 3026)
✓ Frontend runs on port 5174 (default Vite)
✓ CORS is enabled for all origins (safe for dev)
✓ Logs are clean - shows only essential info
✓ Use -u flag for unbuffered Python:
  
  python -u src/semabridge/api/main.py
  
  This ensures logs appear in real-time during deployment


FILE STRUCTURE:
================

Backend entry point:
  src/semabridge/api/main.py

Frontend entry point:
  frontend/src/App.jsx

Frontend API client:
  frontend/src/utils/api.js (connects to http://127.0.0.1:8000/api)


TROUBLESHOOTING:
================

Q: "Failed to fetch" on frontend
A: Backend is not running on port 8000
   Start it: python -u src/semabridge/api/main.py

Q: Backend won't start
A: Check for port conflicts:
   netstat -ano | findstr :8000
   
   If port 8000 is in use, change in main.py line 3026:
   port=8001  (or another free port)

Q: Connection refused
A: Firewall might be blocking localhost
   Or Python is not running the main.py

Q: Models not showing
A: Check browser console (F12) for JavaScript errors
   Check backend terminal for [RESPONSE] errors
   Verify .env has FABRIC_WORKSPACE_ID set


FRONTEND WIRING:
================

All endpoints are pre-configured and working:

GET  /api/health              → Health check
GET  /api/discovery/fabric    → List Fabric models
POST /api/config/generate     → Generate YAML config
POST /api/config/validate     → Validate YAML
POST /api/sync                → Deploy to Snowflake
GET  /api/history             → Get deployment history


MONITORING DEPLOYMENTS:
=======================

Watch backend logs while deploying:

  [REQUEST] POST /api/sync
  [RESPONSE] POST /api/sync -> 200 (45.67s)
  
Status codes:
  ✓ 200 = Success
  ✗ 400 = Bad request (invalid YAML)
  ✗ 401 = Auth failed (credentials)
  ✗ 500 = Server error (check logs)


NEXT STEPS:
===========

1. Start backend: python -u src/semabridge/api/main.py
2. Start frontend: npm run dev (in frontend folder)
3. Open http://localhost:5174
4. Try discovering Fabric models
5. Try generating a config
6. Try deploying to Snowflake
7. Watch the logs!

═════════════════════════════════════════════════════════════════════════════
""")
