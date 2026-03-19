# Snapshot Schema/Table Handling Investigation

**Last Updated**: March 19, 2026  
**Investigation Status**: Complete Analysis with Proposed Solutions

---

## Executive Summary

After comprehensive investigation of the snapshot infrastructure, we've identified the root cause of schema/table inconsistency issues: **The snapshot system stores the complete OSI model but lacks semantic metadata to distinguish original schema structure from system-generated or intermediate processing artifacts.**

### Key Findings

1. **Snapshots preserve raw OSI model data** - All datasets, metrics, dimensions, and relationships are stored as-is without schema hierarchy information
2. **No system-table filtering exists** - Generated tables (e.g., those created during transformations, joins, or aggregations) are indistinguishably stored alongside original schema tables
3. **Missing schema metadata** - Snapshots don't preserve information about which tables belong to which original schema source
4. **Diff engine works at entity level only** - Comparisons detect "added/removed/modified" but not schema-level structural changes
5. **No point-of-origin tracking** - Cannot trace whether a table came from original source discovery or was generated during processing

---

## Data Architecture

### Snapshot Storage Structure

```
SemanticSnapshot (stored in .semantic_metadata/snapshots/{adapter}/{snapshot_id}.json)
├── snapshot_id: "v20260319_145612_fabric_001"
├── adapter: "fabric"
├── timestamp: "2026-03-19T14:56:12Z"
├── semantic_entities: <-- MAIN DATA CONTAINER
│   ├── unique_name: "model-name"
│   ├── label: "Model Display Name"
│   ├── description: ""
│   ├── version: "1.0.0"
│   ├── datasets: [
│   │   {
│   │       "unique_name": "SalesTable",
│   │       "label": "Sales",
│   │       "source_platform": "fabric",
│   │       "columns": [{...}, {...}],
│   │       "metadata": {}  <-- Usually empty, no origin info
│   │   },
│   │   {...more datasets...}
│   ├── metrics: [...list of measures...]
│   ├── dimensions: [...list of dimensions...]
│   ├── relationships: [...list of relationships...]
│   └── metadata: {}
├── schema_hash: "a3f8d2e9c1b4..."
├── parent_snapshot_id: "v20260319_140000_fabric_000" (optional)
├── change_description: ""
├── measure_count: 42
├── dimension_count: 15
├── hierarchy_count: 8
└── relationship_count: 6
```

### Critical Gap: Missing Schema Metadata

The `semantic_entities` contains the OSI model (`OSIModel.model_dump()`) with no additional schema-level metadata:

```python
# What's stored:
snapshot_mgr.create_snapshot(
    adapter="fabric",
    semantic_state=osi.model_dump()  # ← Raw OSI data, no origin info
)

# What's missing:
# - Original schema name the dataset came from
# - Whether dataset is user-created or system-generated
# - Original column names vs transformed names
# - Source table references
```

---

## Data Flow Analysis

### 1. SNAPSHOT CREATION PHASE

**File**: `src/semabridge/api/main.py` (lines 1033, 1104)

```
Fabric/Snowflake Discovery
    ↓
Extract DDL → Convert to OSI Model (OSIModel)
    ↓
OSIModel.model_dump() ← Creates plain dict with:
    • datasets (tables without origin info)
    • metrics (measures)
    • dimensions (dimensions)
    • relationships (relationships)
    ↓
snapshot_mgr.create_snapshot(adapter, osi_dict)
    ↓
Store in DuckDB model_versions table
    ├── Column: snapshot (JSON blob) ← Contains the OSI dict
    ├── Column: schema_hash ← SHA256 of entire dict
    └── Column: created_at
```

**Problem**: All datasets are equally weighted; no distinction between:

- System-generated tables (joins, aggregations)
- Tables from original schema
- Derived/intermediate tables

### 2. SNAPSHOT STORAGE PHASE

**File**: `src/semabridge/repository/semantic_snapshot_manager.py` (lines 150-200)

```
create_snapshot() called with semantic_state (OSI dict)
    ↓
Counts entities:
    • measure_count: len(semantic_state.get("metrics", []))
    • dimension_count: len(semantic_state.get("dimensions", []))
    • relationship_count: len(semantic_state.get("relationships", []))
    ↓
Hash entire state:
    • schema_hash = SHA256(json.dumps(semantic_state, sort_keys=True))
    ↓
Create SemanticSnapshot with:
    • snapshot_id (timestamped)
    • semantic_entities = {raw OSI model}
    • parent_snapshot_id (auto-detected)
    ↓
Save as JSON file & update index
```

**Problem**: No metadata enrichment; snapshot is just a copy of OSI model with basic counts.

### 3. SNAPSHOT RETRIEVAL PHASE

**Files**:

- `src/semabridge/api/repo_router.py` (lines 525-650)
- `src/semabridge/api/main.py` (lines 2346-2400)

```
Frontend requests snapshot:
    GET /api/repo/snapshots/tree → get_snapshot_tree()
    GET /api/repo/snapshots/file → get_snapshot_file()
    ↓
Query DuckDB:
    SELECT snapshot FROM model_versions WHERE model_id = ? ORDER BY created_at DESC LIMIT 1
    ↓
Return snapshot JSON (OSI dict) as YAML
    ↓
Frontend displays all datasets equally
    (no visual distinction between source/generated tables)
```

**Problem**: No filtering; all tables displayed regardless of origin.

### 4. SNAPSHOT DIFF PHASE

**File**: `src/semabridge/repository/semantic_diff_engine.py` (lines 200-300)

```
compare_states(state_1, state_2)
    ↓
Compare by entity type:
    1. Compare metrics (measures)
       - Index by unique_name
       - Detect: added, modified, removed
    2. Compare dimensions
       - Index by unique_name
       - Detect: added, modified, removed
    3. Compare relationships
       - Index by unique_name
       - Detect: added, modified, removed
    4. Compare datasets
       - Index by unique_name
       - Detect: added, modified, removed
    ↓
Return SemanticDiff with:
    • summary: counts of changes per entity type
    • changes: list of EntityChange objects
    • breaking_changes: only removals
```

**Problem**: No schema-level comparison; cannot distinguish:

- "Sales table structure changed" vs "Sales table removed, new Sales table added"
- Schema reorganization vs actual data model changes

---

## Root Causes

### 1. **No Schema Origin Metadata**

Datasets lack information about their source:

```python
# Current OSIDataset structure:
{
    "unique_name": "Sales",
    "label": "Sales",
    "columns": [...],
    "source_platform": "fabric",
    "metadata": {}  # ← Empty, could contain origin info
}

# Missing fields:
# - "original_schema_name": "dbo"
# - "is_system_generated": false
# - "source_type": "original" | "derived" | "system"
# - "created_by": "discovery" | "user" | "transformation"
```

### 2. **Single-Level Entity Storage**

All datasets treated as equals in arrays; no hierarchical structure:

```python
# Current:
"datasets": [
    {"unique_name": "Table1", ...},
    {"unique_name": "Table2", ...},
    {"unique_name": "Table3", ...}  # ← Can't tell if this was added or reorganized
]

# Better would be:
"datasets": {
    "original_schema": {
        "dbo": {
            "tables": [...],
            "source": "fabric_semantic_view"
        }
    },
    "derived_tables": {...},
    "system_tables": {...}
}
```

### 3. **No Snapshot Versioning Metadata**

`parent_snapshot_id` exists but no change context:

```python
# Current:
parent_snapshot_id: "v20260319_140000_fabric_000"
change_description: ""  # ← Often empty

# Better would include:
change_metadata: {
    "change_type": "schema_update|refresh|manual_edit",
    "triggered_by": "auto_discovery|user_action|scheduled_sync",
    "source_changes": [...],  # What changed in source
    "applied_transformations": [...]  # What was done to model
}
```

### 4. **No Filtering During Extract**

System-generated tables included without distinction:

```python
# In OSIModel creation:
# No logic to mark or filter:
# - Generated join tables
# - Temporary aggregation tables
# - System views
# - Internal bridge tables
```

### 5. **Diff Engine at Wrong Granularity**

Entity-level comparison misses schema-level patterns:

```python
# Current logic:
if entity in snapshot1 and entity not in snapshot2:
    REMOVED
elif entity not in snapshot1 and entity in snapshot2:
    ADDED
elif entity in both and entity != entity:
    MODIFIED

# Missing:
# - "3 tables moved to different schema"
# - "Schema renamed from 'old' to 'new'"
# - "Tables consolidated from 2 schemas into 1"
```

---

## Current System Behavior (Problematic)

### Scenario 1: Original Schema Has 3 Tables

```
Discovery runs on Fabric workspace
    ↓
Creates OSIModel with datasets: [SalesTable, CustomerTable, ProductTable]
    ↓
Snapshot created:
    {
        "datasets": [
            {"unique_name": "SalesTable"},
            {"unique_name": "CustomerTable"},
            {"unique_name": "ProductTable"}
        ]
    }
    ↓
System applies transformation → adds join table: "SalesCustomerJoin"
    ↓
New snapshot:
    {
        "datasets": [
            {"unique_name": "SalesTable"},
            {"unique_name": "CustomerTable"},
            {"unique_name": "ProductTable"},
            {"unique_name": "SalesCustomerJoin"}  # ← Where did this come from?
        ]
    }
    ↓
Diff shows: "1 dataset added: SalesCustomerJoin"
    → User confused: "I didn't add that!"
```

### Scenario 2: Schema Reorganization

```
Original: tables in schema "dbo"
    ↓
User reorganizes: moves some to "staging", some to "analytics"
    ↓
New snapshot has same tables but different schema associations
    ↓
Diff shows: "0 changes"
    → User confused: "Nothing changed, but the structure is different!"
```

---

## Solution Architecture

### Phase 1: Enhance Snapshot Metadata (Immediate)

**Add schema-level metadata to OSIModel**:

```python
# Extend OSIModel to include:
class OSIModel(OSIBaseModel):
    # ... existing fields ...

    schema_metadata: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Schema origin and grouping info"
    )
    # Example:
    # {
    #     "dbo": {
    #         "source": "original_schema",
    #         "tables": ["SalesTable", "CustomerTable"],
    #         "discovery_timestamp": "2026-03-19T10:00:00Z"
    #     }
    # }

    entity_origin_tracking: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Origin info for each dataset"
    )
    # Example:
    # {
    #     "SalesTable": {
    #         "type": "original|derived|system",
    #         "source": "fabric_semantic_view",
    #         "original_schema": "dbo"
    #     }
    # }
```

### Phase 2: Enriched Snapshot Creation

**Before creating snapshot, capture origin metadata**:

```python
# In SemanticSnapshotManager.create_snapshot()

snapshot = SemanticSnapshot(
    ...existing fields...,
    semantic_entities=semantic_state,  # ← Unchanged
    schema_hierarchy={  # ← NEW
        "schemas": [
            {
                "name": "dbo",
                "type": "original",
                "tables": ["SalesTable", "CustomerTable"],
                "source_platform": "fabric",
                "discovered_at": timestamp
            }
        ],
        "derived_entities": [
            {
                "name": "SalesCustomerJoin",
                "type": "generated",
                "created_by": "system_transformation",
                "source_tables": ["SalesTable", "CustomerTable"]
            }
        ]
    },
    change_classification={  # ← NEW
        "change_type": "auto_discovery|manual_edit|schema_update",
        "triggered_by": "user|system|scheduled_sync",
        "related_snapshots": [parent_snapshot_id]
    }
)
```

### Phase 3: Enhanced Diff Engine

**Schema-aware comparison**:

```python
# In SemanticDiffEngine.compare_states()

def compare_schema_hierarchies(
    state_1_hierarchy: Dict,
    state_2_hierarchy: Dict
) -> List[SchemaChange]:
    """
    Compare schema structures, not just entity counts.
    Detects:
    - Schema additions/removals
    - Table migrations between schemas
    - System table additions
    - Structural reorganizations
    """
    changes = []

    # Detect schema-level changes
    schemas_1 = {s['name']: s for s in state_1_hierarchy.get('schemas', [])}
    schemas_2 = {s['name']: s for s in state_2_hierarchy.get('schemas', [])}

    # Schema added/removed
    for schema_name in set(schemas_1) | set(schemas_2):
        if schema_name not in schemas_1:
            changes.append(SchemaChange(
                type="schema_added",
                schema_name=schema_name,
                tables=schemas_2[schema_name]['tables']
            ))
        elif schema_name not in schemas_2:
            changes.append(SchemaChange(
                type="schema_removed",
                schema_name=schema_name,
                tables=schemas_1[schema_name]['tables']
            ))
        else:
            # Schema exists in both - check table changes
            tables_1 = set(schemas_1[schema_name]['tables'])
            tables_2 = set(schemas_2[schema_name]['tables'])

            added = tables_2 - tables_1
            removed = tables_1 - tables_2

            if added:
                changes.append(SchemaChange(
                    type="tables_added_to_schema",
                    schema_name=schema_name,
                    tables=list(added)
                ))
            if removed:
                changes.append(SchemaChange(
                    type="tables_removed_from_schema",
                    schema_name=schema_name,
                    tables=list(removed)
                ))

    # Detect system table additions (mark differently)
    derived_1 = {e['name']: e for e in state_1_hierarchy.get('derived_entities', [])}
    derived_2 = {e['name']: e for e in state_2_hierarchy.get('derived_entities', [])}

    for entity_name in set(derived_2) - set(derived_1):
        changes.append(SchemaChange(
            type="system_table_added",
            entity_name=entity_name,
            created_by=derived_2[entity_name].get('created_by'),
            is_system_generated=True
        ))

    return changes
```

### Phase 4: Frontend Display Improvements

**Show schema hierarchy with visual distinction**:

```jsx
// In VersionControlPanel - snapshot diff display

function SnapshotDiffViewer({ diff }) {
  return (
    <div className="space-y-4">
      {/* Schema-level changes */}
      <section>
        <h3>Schema Changes</h3>
        {diff.schema_changes.map((change) => (
          <div
            key={change.id}
            className={`
            p-3 rounded border-l-4
            ${
              change.type.startsWith("schema_")
                ? "bg-blue-50 border-blue-500"
                : "bg-yellow-50 border-yellow-500"
            }
          `}
          >
            <span className="font-semibold">{change.schema_name}</span>
            <span
              className={`badge ${change.type === "schema_added" ? "badge-success" : "badge-danger"}`}
            >
              {change.type}
            </span>
          </div>
        ))}
      </section>

      {/* Table-level changes, grouped by origin */}
      <section>
        <h3>Table Changes</h3>

        <div className="space-y-3">
          {/* Original schema tables */}
          <div className="bg-green-50 p-3 rounded">
            <h4 className="font-semibold text-green-900">
              Original Schema Tables
            </h4>
            {diff.changes
              .filter(
                (c) => c.entity_type === "dataset" && !c.is_system_generated,
              )
              .map((change) => (
                <TableChangeRow key={change.entity_id} change={change} />
              ))}
          </div>

          {/* System-generated tables - with clear warning */}
          {diff.changes.some((c) => c.is_system_generated) && (
            <div className="bg-orange-50 p-3 rounded border-l-4 border-orange-500">
              <h4 className="font-semibold text-orange-900">
                ⚠️ System-Generated Tables
              </h4>
              <p className="text-sm text-orange-800 mb-2">
                These tables were automatically created by the system and may
                not represent original schema structure.
              </p>
              {diff.changes
                .filter((c) => c.is_system_generated)
                .map((change) => (
                  <SystemTableRow key={change.entity_id} change={change} />
                ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
```

---

## Implementation Roadmap

### Step 1: Extend SemanticSnapshot Model (High Priority)

**File**: `src/semabridge/repository/semantic_snapshot_manager.py`

Add fields to `SemanticSnapshot`:

- `schema_hierarchy`: Dict with schema grouping info
- `entity_metadata`: Dict mapping entity_id → origin/type info
- `change_classification`: Dict with change context

### Step 2: Update Snapshot Creation (High Priority)

**Files**:

- `src/semabridge/converter/semantic_view_to_osi.py`
- `src/semabridge/api/main.py`

Before snapshot creation:

- Extract schema names from original discovery
- Mark entity origins (original vs generated)
- Capture change context

### Step 3: Enhance Diff Engine (Medium Priority)

**File**: `src/semabridge/repository/semantic_diff_engine.py`

Add methods:

- `compare_schema_hierarchies()`
- `classify_system_tables()`
- `detect_structural_changes()`

### Step 4: Update Frontend Display (Medium Priority)

**File**: `newfrontend/src/components/RepositoryMap/VersionControlPanel.jsx`

- Group tables by schema
- Visual distinction for system-generated
- Schema-level change summary
- Clear warnings for unexpected changes

### Step 5: Migration & Backward Compatibility (Medium Priority)

Existing snapshots:

- Infer schema hierarchy from dataset groupings
- Mark all as "original" if no origin info
- Graceful degradation if metadata missing

---

## Risk Analysis

### What Could Go Wrong

1. **Performance Impact**
   - Larger snapshot JSON (20-30% increase estimated)
   - More metadata to process during comparisons
   - **Mitigation**: Store in separate index table, lazy-load metadata

2. **Backward Compatibility**
   - Existing snapshots lack metadata
   - Frontend expecting old format
   - **Mitigation**: Add `schema_hierarchy_version` field, support both formats

3. **False Positives in Classification**
   - Difficult to distinguish "generated by system" vs "user-created"
   - **Mitigation**: Explicit origin tracking at creation time

4. **Migration Complexity**
   - Existing workflow assumptions
   - Database schema changes
   - **Mitigation**: Phased rollout, feature flags

---

## Testing Strategy

### Test Case 1: Basic Schema Preservation

```
Given: Discovery of 3-table schema
When: Snapshot created
Then: schema_hierarchy shows 1 schema with 3 tables
  And: All marked as "original" source
  And: No system tables
```

### Test Case 2: Detect System-Generated Tables

```
Given: Snapshot 1 with 3 original tables
  And: System transformation adds join table
When: Snapshot 2 created
Then: Diff shows "system_table_added"
  And: Join table marked with "created_by": "system_transformation"
  And: UI shows clear warning
```

### Test Case 3: Schema Reorganization

```
Given: Original schema "dbo" with tables in snapshot 1
  And: User reorganizes to "staging" and "analytics"
When: Snapshot 2 created
Then: Diff shows "schema_added" for new schemas
  And: Diff shows "tables_moved" or "tables_migrated"
  And: Original tables still tracked by unique_name
```

### Test Case 4: Backward Compatibility

```
Given: Old snapshots without schema_hierarchy metadata
When: Frontend displays diff with old snapshot
Then: Falls back to entity-level comparison
  And: No errors, graceful degradation
  And: Warnings shown to user
```

---

## Files to Modify

1. **Core Models** (High Priority)
   - [ ] `src/semabridge/repository/semantic_snapshot_manager.py` - Extend `SemanticSnapshot`
   - [ ] `src/semabridge/intermediate/models.py` - Extend `OSIModel` with schema metadata

2. **Snapshot Creation** (High Priority)
   - [ ] `src/semabridge/api/main.py` - Add metadata during snapshot creation
   - [ ] `src/semabridge/converter/semantic_view_to_osi.py` - Track schema origins

3. **Diff Engine** (Medium Priority)
   - [ ] `src/semabridge/repository/semantic_diff_engine.py` - Schema-aware comparison

4. **API Endpoints** (Medium Priority)
   - [ ] `src/semabridge/api/repo_router.py` - Return enriched snapshots
   - [ ] `src/semabridge/api/main.py` - Include hierarchy in responses

5. **Frontend** (Medium Priority)
   - [ ] `newfrontend/src/components/RepositoryMap/VersionControlPanel.jsx` - Display schema hierarchy
   - [ ] `newfrontend/src/hooks/useSnapshotComparison.js` - Handle schema-level diffs

6. **Database** (Optional)
   - [ ] `alembic/versions/*.py` - Add schema_hierarchy column to model_versions (if needed)

---

## Success Metrics

✅ **After Implementation**:

- [ ] Snapshots include schema hierarchy metadata
- [ ] System-generated tables clearly marked and distinguished
- [ ] Diff output shows schema-level changes (not just entity counts)
- [ ] UI displays clear visual distinction between original and system tables
- [ ] User can trace table origin and understand change context
- [ ] No performance degradation > 5%
- [ ] Backward compatible with existing snapshots
- [ ] Tests pass for all scenarios above

---

## Questions for Review

1. **Should we store schema hierarchy in separate JSON column or embed in snapshot?**
   - Trade-off: Storage efficiency vs query simplicity
   - Recommendation: Embed initially, optimize later if needed

2. **How to handle cross-schema relationships?**
   - Current: Relationships stored flat
   - Future: Could track schema context

3. **What constitutes a "system-generated" table?**
   - Need to define clear criteria and mark at creation time
   - Examples: joins, aggregations, transformations, bridge tables

4. **Should old snapshots be retroactively enriched?**
   - Recommendation: Not during initial phase, use inference fallback

---

## Next Steps

1. **Week 1**: Implement Phase 1 & 2 (Extend models + Snapshot creation)
2. **Week 2**: Implement Phase 3 (Enhanced diff engine)
3. **Week 3**: Implement Phase 4 (Frontend improvements)
4. **Week 4**: Testing, migration planning, and documentation
