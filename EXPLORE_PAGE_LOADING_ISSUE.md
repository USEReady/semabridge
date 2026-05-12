# Explore Page Issue: Only One Model Snapshot Loading

## Problem Statement
The Explore page shows only one model's snapshots instead of all projects' snapshots from version control.

---

## Root Cause Analysis

### Issue #1: Only Latest Snapshot is Visualized
**Where**: [RepositoryMap.jsx](frontend/src/components/RepositoryMap/RepositoryMap.jsx#L160-L200)

```javascript
// Current behavior: Pick only the NEWEST snapshot
const sorted = [...snapshots].sort(
    (a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime()
);
const effectiveSnapshotId = sorted[0]?.snapshot_id || null;  // ← ONLY LOADS ONE
```

**Impact**: Even though multiple snapshots are fetched, only 1 is loaded into the graph visualization.

---

### Issue #2: Hard 200-Snapshot Limit Per Model
**Where**: [src/semabridge/api/services/project_projects_impl.py:653](src/semabridge/api/services/project_projects_impl.py#L653)

```python
snapshots = db_manager.list_snapshots(model_name, limit=200)
```

**Impact**: If any project has >200 snapshots, older ones are never returned to frontend.

---

### Issue #3: Test Projects Auto-Filtered
**Projects filtered out**:
- "test"
- "teste" 
- "test project"
- "fabricmodel"

**Impact**: Development/test projects are invisible in Explore view, making it harder to debug.

---

### Issue #4: Deduplication Hides Multi-Project Snapshots
**Where**: [api.js:530-550](frontend/src/utils/api.js#L530-L550)

```javascript
// Deduplicate by snapshot_id, keeping newest
const deduped = new Map();
for (const list of nestedResults) {
    for (const item of list) {
        const sid = String(item?.snapshot_id || '');
        deduped.set(sid, item);  // ← If same snapshot_id in multiple projects, only newest kept
    }
}
```

**Impact**: If a snapshot appears in multiple projects (e.g., production snapshot used by multiple teams), only the newest occurrence is visible.

---

## Ideation: Solutions

### Solution A: Multi-Snapshot Visualization (High Value)
**Goal**: Show all projects' snapshots in Explore, not just the latest one.

**Implementation**:
1. Keep `getGraphSnapshots('__all__')` to fetch all snapshots from all projects
2. In RepositoryMap, add a **timeline selector** or **snapshot list panel**
3. User can switch between snapshots and see different project graphs
4. Add toggle: "Show all snapshots" vs "Show latest only"

**Effort**: Medium (adds UI component, modify data flow)
**Benefit**: Users see full history of all project snapshots

---

### Solution B: Increase Snapshot Limit (Low Effort, Quick Win)
**Goal**: Load more than 200 snapshots per model.

**Implementation**:
1. Make `limit=200` configurable via environment variable or config
2. Or remove the hard limit entirely and paginate on demand
3. Backend: `snapshots = db_manager.list_snapshots(model_name, limit=CONFIG.SNAPSHOT_LIMIT or 1000)`

**Effort**: Low (1 line change)
**Benefit**: Immediate fix for projects with >200 snapshots

---

### Solution C: Stop Auto-Filtering Test Projects (Low Effort)
**Goal**: Make test projects visible (user can manually filter if needed).

**Implementation**:
1. Add config flag: `includeTestProjects=true` (environment variable or settings UI)
2. Skip the test project filter when flag is true
3. Backend: Move filter logic to optional middleware

**Effort**: Low (add config flag, conditional logic)
**Benefit**: Easier debugging; users can decide what to hide

---

### Solution D: Fix Deduplication Logic (Low Effort)
**Goal**: Show all project instances of a snapshot, not just newest.

**Implementation**:
```javascript
// Instead of Map (which deduplicates):
// Return array with ALL instances, but sort/group by project
const withMetadata = nestedResults.flat().map(item => ({
    ...item,
    project_id: item.model_name,
    source: 'project'
}));
// Group by snapshot_id in UI, not in API
```

**Effort**: Low (change Map to Array, add UI grouping)
**Benefit**: Users see cross-project snapshot reuse

---

## Debugging Steps (To Investigate Further)

### Step 1: Check Backend Logs
```bash
# Check if backend is limiting snapshots
grep -r "limit=200" src/
```

### Step 2: Inspect Network Requests
1. Open DevTools (F12)
2. Go to Network tab
3. In Explore page, look for requests to `/graph/*/snapshots`
4. Check response JSON - how many snapshots returned per project?

### Step 3: Check Frontend State
```javascript
// In RepositoryMap.jsx, add debug logging:
console.log('All snapshots fetched:', snapshotsResp);
console.log('Using only this snapshot:', effectiveSnapshotId);
```

### Step 4: List Projects
```bash
curl http://localhost:8000/api/projects | jq '.[] | {id, name, created_at}'
```

### Step 5: Check Version Control
```bash
# How many snapshots per project?
find Config/projects -name "*.yaml" | while read f; do
  project=$(basename $f .yaml)
  count=$(grep -c "snapshot_id" "$f" 2>/dev/null || echo 0)
  echo "$project: $count snapshots"
done
```

---

## Recommended Fixes (Priority Order)

| Priority | Fix | Effort | Impact |
|----------|-----|--------|--------|
| 🔴 P1 | Solution B: Increase snapshot limit | 5 min | High - unblocks >200 snapshot projects |
| 🟠 P2 | Solution A: Multi-snapshot UI | 2-3 hrs | High - shows all project versions |
| 🟡 P3 | Solution C: Allow test projects | 30 min | Medium - aids debugging |
| 🟡 P3 | Solution D: Fix deduplication | 30 min | Low - edge case, low frequency |

---

## Next Steps

1. **Confirm root cause**: Run "Debugging Step 2" to see actual snapshot counts
2. **Quick win**: Implement Solution B (increase limit)
3. **Main feature**: Implement Solution A (multi-snapshot visualization)

Would you like me to implement any of these solutions?
