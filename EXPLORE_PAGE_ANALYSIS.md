# Semabridge Frontend Explore Page Analysis

## Executive Summary

The **Explore** page is a lightweight React component that wraps a visual semantic model map (RepositoryMap). It visualizes project dependencies, tables, and measures using React Flow. The component loads projects and model snapshots through a multi-stage data fetching process with a **hard limit of 200 snapshots per model**.

---

## 1. Component Location & Structure

### Main Files
| File | Purpose |
|------|---------|
| `frontend/src/pages/ExplorePage.jsx` | Simple page wrapper |
| `frontend/src/components/RepositoryMap/RepositoryMap.jsx` | Main visualization container (440+ lines) |
| `frontend/src/components/RepositoryMap/DetailPanel.jsx` | Right sidebar detail view |
| `frontend/src/components/RepositoryMap/FileTreePanel.jsx` | Left sidebar file tree |
| `frontend/src/utils/api.js` | API client with caching |

### ExplorePage (Simple Wrapper)
```jsx
// frontend/src/pages/ExplorePage.jsx
export default function ExplorePage() {
  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <RepositoryMap />  // Delegates to RepositoryMap component
    </div>
  );
}
```

---

## 2. Data Loading Flow

### Phase 1: Initial Data Load (RepositoryMap.jsx:160-200)

```javascript
const loadData = useCallback(async () => {
    setLoading(true);
    setTreeLoading(true);
    setGraphLoading(true);
    try {
        // Load two data sources in parallel
        const [snapshotResp, snapshotsResp] = await Promise.all([
            api.getSnapshotTree().catch(() => ({ root: null })),          // File tree
            api.getGraphSnapshots('__all__').catch(() => []),             // All snapshots
        ]);

        setSnapshotTreeData(snapshotResp?.root || null);
        
        // Find latest snapshot by timestamp
        const snapshots = Array.isArray(snapshotsResp) ? snapshotsResp : [];
        const sorted = [...snapshots].sort(
            (a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime()
        );
        const effectiveSnapshotId = sorted[0]?.snapshot_id || null;
        
        // Load graph for latest snapshot
        const graphResp = await api.getGraphSnapshot(modelScope, effectiveSnapshotId);
        const normalizedGraph = normalizeGraphPayload(graphResp);
        setGraphData(normalizedGraph);
        
    } finally {
        setLoading(false);
    }
}, [snapshotId, includeSystemTables]);

useEffect(() => { loadData(); }, [loadData]);
```

### Phase 2: Fetch Snapshots (frontend/src/utils/api.js:513-560)

```javascript
async getGraphSnapshots(modelId = '__all__') {
    // First attempt: direct request for __all__
    const res = await authFetch(`${API_BASE_URL}/graph/${encodeURIComponent(modelId)}/snapshots`);
    const directData = await handleResponse(res);
    const directList = extractSnapshotList(directData);

    // Return if direct call succeeded or if specific modelId
    if (String(modelId) !== '__all__' || directList.length > 0) {
        return directList;
    }

    // Fallback: If __all__ returned nothing, fetch per-project
    try {
        const projects = await this.listProjects();  // ← Fetch all projects
        const ids = [...new Set((projects || [])
            .map((p) => p?.id || p?.project_id)
            .filter(Boolean)
            .map(String))];
        
        // Query snapshots for EACH project in parallel
        const nestedResults = await Promise.all(
            ids.map(async (id) => {
                try {
                    const projectRes = await authFetch(
                        `${API_BASE_URL}/graph/${encodeURIComponent(id)}/snapshots`
                    );
                    const projectData = await handleResponse(projectRes);
                    return extractSnapshotList(projectData).map((row) => ({
                        ...row,
                        model_name: row?.model_name || id,
                    }));
                } catch {
                    return [];
                }
            })
        );

        // Deduplicate by snapshot_id, keeping newest
        const deduped = new Map();
        for (const list of nestedResults) {
            for (const item of list) {
                const sid = String(item?.snapshot_id || '');
                if (!sid) continue;
                deduped.set(sid, item);
            }
        }

        return [...deduped.values()].sort(
            (a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime()
        );
    } catch {
        return [];
    }
}
```

---

## 3. Backend Snapshots Endpoint (CRITICAL LIMIT)

### Location
`src/semabridge/api/services/project_projects_impl.py:650-668`

### Endpoint
```
GET /api/graph/{model_name}/snapshots
```

### Implementation
```python
async def graph_snapshots_compat(model_name: str):
    """Snapshot history for Explore time-machine (newest first)."""
    try:
        logger.info("[Explore] Snapshot list requested model=%s", model_name)
        
        # ⚠️ HARD LIMIT: Only 200 snapshots per model!
        snapshots = db_manager.list_snapshots(model_name, limit=200)
        
        logger.info("[Explore] Snapshot list resolved model=%s count=%s", 
                   model_name, len(snapshots or []))
        
        return [
            {
                "snapshot_id": s.snapshot_id,
                "timestamp": s.timestamp,
                "version_tag": s.version_tag or f"v{s.snapshot_id[:8]}",
                "status": s.status or "success",
                "duration_ms": s.duration_ms or 0,
                "model_name": model_name,
                "run_id": s.run_id,
                "initiated_by": s.initiated_by,
                "connectors": _extract_snapshot_connectors(s),
            }
            for s in snapshots
        ]
    except Exception as exc:
        logger.debug("Failed to list snapshots for %s: %s", model_name, exc)
        return []
```

---

## 4. How Projects are Loaded

### Projects Listing Endpoint
```python
# backend/projects_controller.py
@router.get('/api/projects')
async def list_projects_compat():
    """Compatibility: newfrontend expects a projects collection."""
    _compat_ensure_loaded()
    
    # 1. Load YAML files from Config/projects directory
    # 2. Merge with in-memory project registry
    # 3. Filter out test projects: "test", "teste", "test project", "fabricmodel"
    # 4. Deduplicate by project_id (keep newest by timestamp)
    
    return list(deduped.values())
```

### Frontend Usage
```javascript
const {
    data: projects = [],
    isLoading: projectsLoading,
    refetch: refetchProjects,
} = useQuery({
    queryKey: ['projects'],
    queryFn: api.listProjects,  // ← Calls /api/projects
});
```

---

## 5. State Management Architecture

### RepositoryMap Component State
```javascript
// Data
const [snapshotTreeData, setSnapshotTreeData] = useState(null);
const [graphData, setGraphData] = useState({ nodes: [], edges: [], meta: {} });

// UI Selections (cached per session via usePageCache hook)
const [selectedModelId, setSelectedModelId] = usePageCache('explore:selectedModelId', '__all__');
const [selectedConnector, setSelectedConnector] = usePageCache('explore:selectedConnector', '__all__');
const [selectedTableId, setSelectedTableId] = usePageCache('explore:selectedTableId', '__all__');
const [erMode, setErMode] = usePageCache('explore:erMode', true);  // Power BI-like view
const [showExplorer, setShowExplorer] = usePageCache('explore:showExplorer', true);
const [includeSystemTables, setIncludeSystemTables] = usePageCache('explore:includeSystemTables', false);

// Search & Filter
const [searchQuery, setSearchQuery] = useState('');
const [filterType, setFilterType] = useState('all');  // all | models | tables | broken
```

### Filtering & Search
- **searchQuery**: Free-text search across node labels
- **filterType**: Filter by node type
- **erMode**: Toggle between hierarchical and entity-relationship layouts
- **includeSystemTables**: Include/exclude system tables (pg_, sqlite_, duckdb_, sys., __)

---

## 6. Data Flow Diagram

```
┌─────────────────────────────────────────────────┐
│ ExplorePage.jsx                                 │
│ ├─ Renders RepositoryMap                       │
└─────────────────────┬───────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────┐
│ RepositoryMap.jsx (useEffect → loadData)        │
│ Calls:                                          │
│ 1. api.getSnapshotTree()                       │
│ 2. api.getGraphSnapshots('__all__')            │
└─────────────────────┬───────────────────────────┘
                      │
        ┌─────────────┴──────────────┐
        │                            │
        ▼                            ▼
    getSnapshotTree()        getGraphSnapshots()
    ─────────────────        ───────────────────
    GET /repo/snapshots/tree GET /graph/__all__/snapshots
                             │
                             ├─ If empty response:
                             │  • Fetch: api.listProjects()
                             │  • For each project ID:
                             │    GET /graph/{projectId}/snapshots
                             │  • Merge & deduplicate results
                             │
                             ▼ Returns: [{snapshot_id, timestamp, ...}]
                             
    Backend Limit ⚠️: 
    db_manager.list_snapshots(model_name, limit=200)
    
    ┌─────────────────────────────────────────┐
    │ Graph Data Loaded                       │
    │ • Nodes (models, tables, measures)      │
    │ • Edges (relationships)                 │
    │ • Sorted by timestamp (newest first)    │
    │ • ≤ 200 per model_name                 │
    └─────────────────────────────────────────┘
```

---

## 7. Potential Issues & Limitations

### 🚨 Hard Limit: 200 Snapshots Per Model
- **Issue**: Only the 200 most recent snapshots are returned per model_name
- **Location**: `project_projects_impl.py:653`
- **Impact**: If a project has >200 snapshots, older ones won't appear in Explore
- **Fix Required**: Implement pagination or increase limit

### 🚨 Project Deduplication May Hide Data
- If two projects share the same semantic model/workspace
- Only the newest (by timestamp) is kept when snapshot_id collides
- Older project snapshots with same ID are dropped

### 🚨 Test Projects Auto-Filtered
Backend filters out:
- `"test"`, `"teste"`, `"test project"` (case-insensitive)
- `"fabricmodel"` (auto-generated default)

If a legitimate project is named one of these, it won't appear.

### ⚠️ API Response Caching
- **TTL**: 2 minutes (API_CACHE_TTL_MS)
- New snapshots may not appear immediately after sync

### ⚠️ System Tables Filtering
- Include/exclude in URL: `?include_system_tables=true|false`
- Default: false (hides system tables)

---

## 8. Key Configuration Points

### Cache Configuration (frontend/src/utils/api.js)
```javascript
const API_CACHE_TTL_MS = 2 * 60 * 1000;  // 2 minutes

function setCachedApiValue(cacheKey, value, ttlMs = API_CACHE_TTL_MS) {
    apiCache.set(cacheKey, {
        value,
        expiresAt: Date.now() + ttlMs,
    });
}
```

### Snapshot Limit (backend/project_projects_impl.py)
```python
snapshots = db_manager.list_snapshots(model_name, limit=200)
```

### UI State Persistence
```javascript
const usePageCache = (key, defaultValue) => {
    // Stores/retrieves from sessionStorage
    // Clears when user navigates away
}
```

---

## 9. Recommended Debugging Steps

If projects/snapshots aren't loading:

1. **Check DevTools Network Tab**
   - Monitor `/api/projects` response size
   - Check `/api/graph/__all__/snapshots` for returned count
   - Look for 200-snapshot cap per project

2. **Browser Console Logs**
   - RepositoryMap logs `[Explore]` prefixed messages from backend
   - Check for permission/auth errors

3. **Backend Logs**
   - Search for `[Explore]` prefix in logs
   - Look for `Failed to list snapshots` messages

4. **Check Project Configuration**
   - Verify projects aren't named "test", "teste", "test project", "fabricmodel"
   - Verify project_id is not null/empty

5. **Clear Cache**
   - SessionStorage stores UI state per session
   - Browser DevTools → Application → Clear Site Data
   - Also clears the 2-minute API response cache

---

## 10. Summary: Data Flow for All Projects

```
REQUEST SEQUENCE:
1. RepositoryMap mounts → loadData() triggered
2. Promise.all([getSnapshotTree(), getGraphSnapshots('__all__')])
3. getSnapshotTree() → GET /repo/snapshots/tree (file tree)
4. getGraphSnapshots('__all__') → GET /graph/__all__/snapshots
5. If empty: listProjects() → GET /api/projects
6. For each project: GET /api/graph/{projectId}/snapshots
   ├─ Backend limit: 200 snapshots per request
   ├─ Timestamps returned
   └─ Deduplicate & sort by timestamp
7. Select latest snapshot (highest timestamp)
8. Load graph: GET /graph/{modelId}/snapshot/{snapshotId}
9. Render React Flow visualization

LIMITS:
• 200 snapshots max per model_name (hard backend limit)
• 2 minute API cache TTL
• Deduplication may reduce unique snapshots shown
• Test projects auto-filtered
```
