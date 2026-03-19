# Why Backend Terminal Isn't Showing Deployment Info

## The Problem

You're running:
```powershell
python src\semabridge\api\main.py
```

And then from the frontend or another terminal:
```powershell
python main.py semantic-sync
```

But the **backend FastAPI server shows nothing** while the sync runs.

This is **expected behavior** because:

1. **Separate Processes**: The CLI (`main.py semantic-sync`) runs in its own local process
2. **No API Communication**: The CLI doesn't call the backend API - it does everything locally
3. **No Shared Logs**: Logs from the CLI don't automatically appear on the backend terminal

### What's Actually Happening:

```
CLI Process (python main.py semantic-sync)
├── Extracts from Fabric
├── Translates DAX → SQL
├── Creates Snowflake DDL
└── Deploys to Snowflake
    [All logs stay in CLI process]

FastAPI Backend (port 8000)
├── Running
├── Waiting for /api/sync requests
└── Not involved in CLI sync
```

---

## Solution: View Deployment Info

You have **3 options**:

### Option 1: Stream Sync Output (RECOMMENDED)

Use the new script to capture and display ALL output in real-time:

```powershell
python run_sync_with_output.py
```

**Shows:**
- Progress updates (processing, translating, deploying)
- Measure/metric being synced
- DAX expressions being translated
- Snowflake view names
- Any errors

**Output:** Formatted + saved to `output/sync_stream_*.log`

---

### Option 2: Watch CLI Logs (In Progress)

While sync is running, open a new terminal and run:

```powershell
python watch_sync_logs.py
```

**Shows:**
- Recent CLI command logs
- Current sync status
- What stage it's on
- Tails latest operation logs

---

### Option 3: Verbose/Debug Mode

Enable DEBUG logging to see absolutely everything:

```powershell
python run_sync_verbose.py
```

**Shows:**
- Every single operation
- Internal state transitions
- DAX translation steps
- SQL being executed
- All metrics processed

---

## How to Use: Complete Workflow

### During Sync:

**Terminal 1:** Run the sync with output
```powershell
python run_sync_with_output.py
```

This shows deployment progress in real-time.

### Get Full Report After Sync:

```powershell
# See what was deployed
python quick_sync_status.py

# Compare Fabric vs Snowflake
python compare_fabric_vs_snowflake_measures.py

# Validate queries work
python validate_synced_metrics.py
```

---

## Understanding the Output

When you run `python run_sync_with_output.py`, you'll see:

```
[INFO] ============= EXTRACTING FROM FABRIC =============
[METRIC] Processing measure: TOTAL_REVENUE
[EXPR] DAX Expression: SUM([Amount])
[PROC] Translating to SQL...
[OK] Translation successful
[TABLE] Creating semantic view: COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC
[PROC] Deploying metrics to Snowflake...
[METRIC] Metric TOTAL_REVENUE: SUM(fact.amount)
[METRIC] Metric CAMPAIGN_ROI: (revenue - cost) / cost * 100
[OK] Deployment successful
```

---

## Why Backend Terminal Shows Nothing

**Before Fix:**
- Backend: Waiting idle
- CLI: Syncing silently
- You: Staring at backend terminal with no output

**After Fix:**
- Backend: Still waiting for API requests (normal)
- CLI: Streaming output to console (NEW)
- You: Can see everything happening

---

## Key Takeaway

The **backend terminal is NOT supposed to show deployment info** because the CLI runs separately. 

Use one of the new scripts above to see real-time deployment progress!
