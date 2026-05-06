# Manual API Testing – cURL Commands for Rollback Verification

Use these cURL commands to manually test the API endpoints if you prefer not to use Postman.

---

## Prerequisites

```bash
# Install jq (for JSON formatting)
# macOS:
brew install jq

# Windows (with Chocolatey):
choco install jq

# Linux:
sudo apt-get install jq
```

---

## Setup Variables

```bash
# Set these values for your environment
export BASE_URL="http://localhost:8000/api"
export SNOWFLAKE_DB="SEMABRIDGE_DB"
export SNOWFLAKE_SCHEMA="PUBLIC"
export FABRIC_WORKSPACE_ID="your-workspace-id"
export MODEL_ID="your-model-id"
export SEMANTIC_VIEW_NAME="_6AA0A303_DC1F_4457_86B3_90888E995C0F_SEMANTIC"
export SOURCE_TABLE="ORDERS"
```

---

## Test 1: Health Check

```bash
# Verify API is running
curl -s -X GET "$BASE_URL/health" | jq .

# Expected response:
# { "status": "healthy" }
```

---

## Test 2: Start COPY Sync

```bash
# Start a COPY sync job
COPY_RESPONSE=$(curl -s -X POST "$BASE_URL/sync/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "direction": "fabric_to_snowflake",
    "conflict_resolution": "fail_and_approve",
    "snowflake_database": "'$SNOWFLAKE_DB'",
    "snowflake_schema": "'$SNOWFLAKE_SCHEMA'",
    "fabric_workspace_id": "'$FABRIC_WORKSPACE_ID'",
    "enable_parallel": true,
    "incremental": true,
    "include_data": false
  }')

echo "$COPY_RESPONSE" | jq .

# Extract job ID
COPY_JOB_ID=$(echo "$COPY_RESPONSE" | jq -r '.job_id')
echo "COPY Job ID: $COPY_JOB_ID"
```

---

## Test 3: Check Job Status (Poll)

```bash
# Poll job status until complete
check_job_status() {
  local job_id=$1
  local attempt=1
  local max_attempts=60

  while [ $attempt -le $max_attempts ]; do
    STATUS=$(curl -s -X GET "$BASE_URL/sync/jobs/$job_id" | jq -r '.status')
    echo "[$attempt/$max_attempts] Job $job_id Status: $STATUS"

    if [ "$STATUS" = "success" ] || [ "$STATUS" = "completed" ]; then
      echo "✓ Job completed successfully"
      return 0
    elif [ "$STATUS" = "failed" ]; then
      echo "✗ Job failed"
      return 1
    fi

    sleep 5  # Wait 5 seconds before next poll
    ((attempt++))
  done

  echo "✗ Timeout waiting for job completion"
  return 1
}

# Usage:
check_job_status $COPY_JOB_ID
```

---

## Test 4: Get Job Details

```bash
# Get full job details
curl -s -X GET "$BASE_URL/sync/jobs/$COPY_JOB_ID" | jq .

# Useful fields:
# - .status: Job status (success, failed, conflict_review, etc.)
# - .duration_ms: How long the sync took
# - .total_items: Number of items processed
# - .conflict_count: Number of conflicts (should be 0 after rollback)
```

---

## Test 5: List All Semantic Views (Snowflake)

```bash
# List all semantic views
curl -s -X GET "$BASE_URL/snowflake/semantic-views/list?database=$SNOWFLAKE_DB&schema=$SNOWFLAKE_SCHEMA" | jq .views

# Should output GUID-based names like:
# "_6AA0A303_DC1F_4457_86B3_90888E995C0F_SEMANTIC"
# NOT:
# "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"
```

---

## Test 6: Describe Semantic View

```bash
# Get details of a specific semantic view
curl -s -X GET "$BASE_URL/snowflake/semantic-view/describe?view_name=$SEMANTIC_VIEW_NAME" | jq .

# Check the structure to verify dimensions and measures
# After rollback, custom dimensions/metrics should be gone
```

---

## Test 7: Start UPSERT Sync

```bash
# Start an UPSERT sync
UPSERT_RESPONSE=$(curl -s -X POST "$BASE_URL/sync/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "direction": "fabric_to_snowflake",
    "conflict_resolution": "fail_and_approve",
    "snowflake_database": "'$SNOWFLAKE_DB'",
    "snowflake_schema": "'$SNOWFLAKE_SCHEMA'",
    "fabric_workspace_id": "'$FABRIC_WORKSPACE_ID'",
    "enable_parallel": true,
    "incremental": true,
    "include_data": false,
    "sync_mode": "upsert"
  }')

echo "$UPSERT_RESPONSE" | jq .

UPSERT_JOB_ID=$(echo "$UPSERT_RESPONSE" | jq -r '.job_id')
echo "UPSERT Job ID: $UPSERT_JOB_ID"
```

---

## Test 8: Check Job Logs

```bash
# Get job execution logs
curl -s -X GET "$BASE_URL/sync/jobs/$UPSERT_JOB_ID/logs" | jq .

# After rollback, logs should NOT contain:
# - "Live target fetched"
# - "Merged source with target"
# - "Conflict detected"
# - "Using descriptive view name"
```

---

## Test 9: Get Conflicts (Should Be Empty)

```bash
# Get conflicts for a job (should be empty after rollback)
curl -s -X GET "$BASE_URL/sync/jobs/$UPSERT_JOB_ID/conflicts" | jq .

# Expected: Empty array []
# If has conflicts: Conflict detection not disabled
```

---

## Test 10: Query Conflict Reports Table

```bash
# Execute SQL to check conflict_reports table
curl -s -X POST "$BASE_URL/snowflake/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "SELECT COUNT(*) as conflict_count FROM '$SNOWFLAKE_DB'.'$SNOWFLAKE_SCHEMA'.conflict_reports;",
    "database": "'$SNOWFLAKE_DB'",
    "schema": "'$SNOWFLAKE_SCHEMA'"
  }' | jq .

# Expected result: { "conflict_count": 0 }
```

---

## Test 11: List Recent Jobs

```bash
# Get list of recent sync jobs
curl -s -X GET "$BASE_URL/sync/jobs?limit=10&status=success" | jq .

# Use different status values:
# - success / completed
# - failed
# - conflict_review
# - in_progress
```

---

## Test 12: Get Execution Context (Advanced)

```bash
# Get execution context for a job
curl -s -X GET "$BASE_URL/sync/jobs/$UPSERT_JOB_ID/execution-context" | jq .

# Check these fields:
# - semantic_view_name_override: Should be null/None (not set)
# - project_id: Should be used as view name (GUID)
# - sync_mode: Should be "upsert"
```

---

## Complete Test Sequence Script

Save this as `rollback_test.sh`:

```bash
#!/bin/bash

set -e  # Exit on error

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "  Semabridge Rollback Verification"
echo "=========================================="

# Set variables
BASE_URL="http://localhost:8000/api"
SNOWFLAKE_DB="SEMABRIDGE_DB"
SNOWFLAKE_SCHEMA="PUBLIC"
FABRIC_WORKSPACE_ID="your-workspace-id"

echo -e "${YELLOW}Starting tests...${NC}\n"

# Test 1: Health check
echo -e "${YELLOW}[1/6] Health Check${NC}"
HEALTH=$(curl -s -X GET "$BASE_URL/health")
if echo "$HEALTH" | jq -e '.status == "healthy"' > /dev/null; then
    echo -e "${GREEN}✓ API is healthy${NC}\n"
else
    echo -e "${RED}✗ API health check failed${NC}\n"
    exit 1
fi

# Test 2: Start COPY sync
echo -e "${YELLOW}[2/6] Start COPY Sync${NC}"
COPY_RESPONSE=$(curl -s -X POST "$BASE_URL/sync/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "direction": "fabric_to_snowflake",
    "snowflake_database": "'$SNOWFLAKE_DB'",
    "snowflake_schema": "'$SNOWFLAKE_SCHEMA'",
    "fabric_workspace_id": "'$FABRIC_WORKSPACE_ID'",
    "enable_parallel": true
  }')

COPY_JOB_ID=$(echo "$COPY_RESPONSE" | jq -r '.job_id')
echo "COPY Job ID: $COPY_JOB_ID"

# Test 3: Poll COPY job
echo -e "${YELLOW}[3/6] Polling COPY Job${NC}"
for i in {1..60}; do
    STATUS=$(curl -s -X GET "$BASE_URL/sync/jobs/$COPY_JOB_ID" | jq -r '.status')
    if [ "$STATUS" = "success" ] || [ "$STATUS" = "completed" ]; then
        echo -e "${GREEN}✓ COPY job completed${NC}\n"
        break
    fi
    echo "  Waiting... (attempt $i/60)"
    sleep 5
done

# Test 4: Start UPSERT sync
echo -e "${YELLOW}[4/6] Start UPSERT Sync${NC}"
UPSERT_RESPONSE=$(curl -s -X POST "$BASE_URL/sync/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "direction": "fabric_to_snowflake",
    "snowflake_database": "'$SNOWFLAKE_DB'",
    "snowflake_schema": "'$SNOWFLAKE_SCHEMA'",
    "fabric_workspace_id": "'$FABRIC_WORKSPACE_ID'",
    "enable_parallel": true,
    "sync_mode": "upsert"
  }')

UPSERT_JOB_ID=$(echo "$UPSERT_RESPONSE" | jq -r '.job_id')
echo "UPSERT Job ID: $UPSERT_JOB_ID"

# Test 5: Poll UPSERT job
echo -e "${YELLOW}[5/6] Polling UPSERT Job${NC}"
for i in {1..60}; do
    UPSERT_STATUS=$(curl -s -X GET "$BASE_URL/sync/jobs/$UPSERT_JOB_ID" | jq -r '.status')
    if [ "$UPSERT_STATUS" = "success" ] || [ "$UPSERT_STATUS" = "completed" ]; then
        echo -e "${GREEN}✓ UPSERT job completed${NC}\n"
        break
    fi
    echo "  Waiting... (attempt $i/60)"
    sleep 5
done

# Test 6: Verify no conflicts
echo -e "${YELLOW}[6/6] Verify No Conflict Detection${NC}"
CONFLICTS=$(curl -s -X GET "$BASE_URL/sync/jobs/$UPSERT_JOB_ID/conflicts" | jq '. | length')
if [ "$CONFLICTS" -eq 0 ]; then
    echo -e "${GREEN}✓ No conflicts detected (as expected after rollback)${NC}\n"
else
    echo -e "${RED}✗ Conflicts detected: $CONFLICTS (conflict detection not disabled!)${NC}\n"
fi

echo "=========================================="
echo -e "${GREEN}✓ All tests completed successfully!${NC}"
echo "=========================================="
```

### Run the script:

```bash
chmod +x rollback_test.sh
./rollback_test.sh
```

---

## Debugging Individual Endpoints

### If sync is slow:

```bash
# Check job progress in detail
curl -s -X GET "$BASE_URL/sync/jobs/$JOB_ID/progress" | jq .

# Shows: completed_items, total_items, estimated_time_remaining
```

### If conflict detected (shouldn't happen):

```bash
# Get detailed conflict information
curl -s -X GET "$BASE_URL/sync/jobs/$JOB_ID/conflicts" | jq .

# Should return empty array: []
```

### If view name is wrong:

```bash
# Check what view names exist
curl -s -X GET "$BASE_URL/snowflake/semantic-views/list?database=$SNOWFLAKE_DB" | jq '.views[]'

# Should show GUID-based names, not descriptive names
```

---

## Performance Comparison

```bash
# Script to compare COPY vs UPSERT duration

echo "=== Performance Comparison ==="

# Time COPY sync
echo "Starting COPY sync..."
COPY_START=$(date +%s%N)
COPY_RESPONSE=$(curl -s -X POST "$BASE_URL/sync/jobs" -H "Content-Type: application/json" -d '{"direction":"fabric_to_snowflake"...}')
COPY_JOB_ID=$(echo "$COPY_RESPONSE" | jq -r '.job_id')
# Wait for completion...
COPY_END=$(date +%s%N)
COPY_DURATION=$(( ($COPY_END - $COPY_START) / 1000000 ))

# Time UPSERT sync
echo "Starting UPSERT sync..."
UPSERT_START=$(date +%s%N)
UPSERT_RESPONSE=$(curl -s -X POST "$BASE_URL/sync/jobs" -H "Content-Type: application/json" -d '{"direction":"fabric_to_snowflake","sync_mode":"upsert"...}')
UPSERT_JOB_ID=$(echo "$UPSERT_RESPONSE" | jq -r '.job_id')
# Wait for completion...
UPSERT_END=$(date +%s%N)
UPSERT_DURATION=$(( ($UPSERT_END - $UPSERT_START) / 1000000 ))

# Compare
OVERHEAD=$(( (($UPSERT_DURATION - $COPY_DURATION) * 100) / $COPY_DURATION ))
echo "COPY duration:   ${COPY_DURATION}ms"
echo "UPSERT duration: ${UPSERT_DURATION}ms"
echo "Overhead:        ${OVERHEAD}%"

if [ $OVERHEAD -lt 20 ]; then
    echo "✓ Performance acceptable (overhead < 20%)"
else
    echo "✗ Performance issue (overhead >= 20%)"
fi
```

---

## Logging Best Practices

Keep a log of your test runs:

```bash
# Run tests and save output
./rollback_test.sh | tee test-run-$(date +%Y%m%d-%H%M%S).log

# View results later
cat test-run-20260505-143022.log
```

---

## Export Results for Documentation

```bash
# Get all job details and save to file
curl -s -X GET "$BASE_URL/sync/jobs/$COPY_JOB_ID" | jq . > copy-job-details.json
curl -s -X GET "$BASE_URL/sync/jobs/$UPSERT_JOB_ID" | jq . > upsert-job-details.json

# Compare results
diff <(jq '.status' copy-job-details.json) <(jq '.status' upsert-job-details.json)
```

---

## Troubleshooting cURL Commands

### Check connection:

```bash
curl -v http://localhost:8000/api/health
# Look for "Connected to localhost" message
```

### Check authentication (if required):

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/api/sync/jobs
```

### Pretty print JSON:

```bash
curl -s ... | jq .
# Or:
curl -s ... | python -m json.tool
```

### Debug request details:

```bash
curl -v -X POST http://localhost:8000/api/sync/jobs \
  -H "Content-Type: application/json" \
  -d '{"direction":"fabric_to_snowflake"}'
# -v shows headers and request/response details
```

---

**Version:** 1.0  
**Date:** 2026-05-05

For interactive testing with a UI, use Postman collection instead:  
→ See `Semabridge_Rollback_Tests.postman_collection.json`
