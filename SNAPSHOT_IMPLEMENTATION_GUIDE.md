# Snapshot Schema/Table Handling - Implementation Guide

**Document Version**: 1.0  
**Created**: March 19, 2026  
**Purpose**: Step-by-step implementation to fix snapshot schema preservation issues

---

## Problem Statement (Quick Reference)

Snapshots currently store raw OSI model data without:

- Schema hierarchy information (which tables belong to which schema)
- Entity origin tracking (original vs system-generated vs derived)
- Change classification metadata (what type of change occurred)

This leads to:

- ❌ Unexpected tables appearing without explanation
- ❌ No way to distinguish system-generated from user-created tables
- ❌ Schema reorganizations appearing as no changes
- ❌ Confusing diff outputs that don't help users understand changes

---

## Solution Overview

Add three layers of metadata to snapshots:

```
Layer 1: Schema Hierarchy
  └─ Groups datasets by original schema context
    └─ Tracks which schema each table belongs to

Layer 2: Entity Origin Tracking
  └─ Marks each entity with its source/type
    └─ original | derived | system | user_created

Layer 3: Change Classification
  └─ Adds context about what type of change
    └─ auto_discovery | manual_edit | schema_update | transformation
```

---

## Implementation Phase 1: Model Extensions

### 1.1 Extend SemanticSnapshot Model

**File**: `src/semabridge/repository/semantic_snapshot_manager.py`

**Changes**: Add three new fields to `SemanticSnapshot` dataclass

```python
class SemanticSnapshot(BaseModel):
    """An immutable snapshot of semantic state."""

    # ===== EXISTING FIELDS =====
    snapshot_id: str = Field(...)
    adapter: str = Field(...)
    timestamp: str = Field(...)
    semantic_entities: Dict[str, Any] = Field(...)
    schema_hash: str = Field(...)
    parent_snapshot_id: Optional[str] = Field(None)
    change_description: str = Field("", description="Description of what changed")

    # ===== NEW FIELDS (PHASE 1) =====

    schema_hierarchy: Dict[str, Any] = Field(
        default_factory=dict,
        description="Schema grouping and hierarchy information"
    )
    # Structure:
    # {
    #   "schemas": [
    #     {
    #       "name": "dbo",
    #       "type": "original",  // original | derived | system
    #       "tables": ["Table1", "Table2"],
    #       "source_platform": "fabric",
    #       "discovered_at": "2026-03-19T10:00:00Z",
    #       "description": "Original schema from Fabric"
    #     },
    #     {
    #       "name": "staging",
    #       "type": "derived",
    #       "tables": ["StagingTable1"],
    #       "created_by": "user",
    #       "description": "User-created staging schema"
    #     }
    #   ],
    #   "derived_entities": [  // System-generated tables
    #     {
    #       "name": "SalesCustomerJoin",
    #       "type": "generated",
    #       "created_by": "system_transformation",  // Who created it
    #       "source_tables": ["Sales", "Customer"],
    #       "purpose": "join",  // join | aggregation | bridge | temp
    #       "created_at": "2026-03-19T11:00:00Z"
    #     }
    #   ]
    # }

    entity_metadata: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Metadata for each entity (dataset/metric/dimension/relationship)"
    )
    # Structure:
    # {
    #   "SalesTable": {
    #     "entity_type": "dataset",
    #     "origin": "original",  // original | derived | system | user_created
    #     "source_type": "table | view | join | aggregation",
    #     "original_schema": "dbo",
    #     "source_platform": "fabric",
    #     "discovered_at": "2026-03-19T10:00:00Z",
    #     "metadata": {}
    #   },
    #   "SalesCustomerJoin": {
    #     "entity_type": "dataset",
    #     "origin": "system",
    #     "source_type": "join",
    #     "created_by": "system_transformation",
    #     "source_tables": ["SalesTable", "CustomerTable"],
    #     "created_at": "2026-03-19T11:00:00Z"
    #   },
    #   "TotalSales": {
    #     "entity_type": "measure",
    #     "origin": "original",
    #     "formula": "SUM(SalesAmount)",
    #     "discovered_at": "2026-03-19T10:00:00Z"
    #   }
    # }

    change_classification: Dict[str, Any] = Field(
        default_factory=dict,
        description="Classification of what type of change this snapshot represents"
    )
    # Structure:
    # {
    #   "change_type": "auto_discovery",  // auto_discovery | manual_edit | schema_update | transformation | refresh
    #   "triggered_by": "user",  // user | system | scheduled_sync | webhook
    #   "change_reason": "Initial discovery of Fabric semantic model",
    #   "triggered_at": "2026-03-19T10:00:00Z",
    #   "source_changes": [  // What changed in the source
    #     {
    #       "type": "schema_discovered",
    #       "details": "Found new schema 'dbo' with 3 tables"
    #     }
    #   ],
    #   "applied_transformations": []  // What was done by semabridge
    # }
```

**Why This Structure?**

- `schema_hierarchy`: Preserves the schema context that users understand
- `entity_metadata`: Allows filtering and distinguishing entity origins
- `change_classification`: Provides context for why changes occurred

### 1.2 Extend OSIModel with Metadata

**File**: `src/semabridge/intermediate/models.py`

**Current State**:

```python
class OSIModel(OSIBaseModel):
    unique_name: str = Field(...)
    datasets: List[OSIDataset] = Field(default_factory=list)
    metrics: List[OSIMetric] = Field(default_factory=list)
    dimensions: List[OSIDimension] = Field(default_factory=list)
    relationships: List[OSIRelationship] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
```

**Add**:

```python
class OSIModel(OSIBaseModel):
    # ... existing fields ...

    # NEW: Schema discovery metadata
    schema_discovery_info: Dict[str, Any] = Field(
        default_factory=dict,
        description="Information about where this model came from"
    )
    # {
    #   "source_platform": "fabric",
    #   "discovery_timestamp": "2026-03-19T10:00:00Z",
    #   "discovered_schemas": ["dbo", "staging"],
    #   "total_source_tables": 3,
    #   "transformation_applied": false
    # }

    # NEW: Preserve which tables came from where
    table_origin_map: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Mapping of dataset unique_name to its origin"
    )
    # {
    #   "SalesTable": {
    #     "source_schema": "dbo",
    #     "is_system_generated": false,
    #     "source_type": "table"
    #   },
    #   "SalesCustomerJoin": {
    #     "source_schema": None,
    #     "is_system_generated": true,
    #     "source_type": "join",
    #     "created_by": "system_transformation"
    #   }
    # }
```

**Add to OSIDataset**:

```python
class OSIDataset(OSIBaseModel):
    # ... existing fields ...

    # NEW: Origin metadata
    origin_schema: Optional[str] = Field(
        None,
        description="Original schema this table came from"
    )
    is_system_generated: bool = Field(
        False,
        description="Whether this table was generated by system"
    )
    source_type: str = Field(
        default="table",
        description="table|view|join|aggregation|derived|system"
    )
    discovery_timestamp: Optional[str] = Field(
        None,
        description="When this table was discovered"
    )
```

---

## Implementation Phase 2: Snapshot Creation

### 2.1 Capture Schema Information During Discovery

**File**: `src/semabridge/converter/semantic_view_to_osi.py`

**Current**: Just converts DDL to OSIModel without preserving schema context

**New Logic**:

```python
class SemanticViewToOSIConverter:

    def to_osi(self, data: Dict[str, Any]) -> OSIModel:
        """Convert semantic view DDL to OSI with schema metadata."""

        # 1. Parse DDL and extract schema info
        ddl = data.get("ddl")
        view_name = data.get("view_name")

        # 2. Build OSI as currently done
        osi = self._parse_ddl_to_osi(ddl, view_name)

        # 3. NEW: Enrich with schema discovery metadata
        osi.schema_discovery_info = {
            "source_platform": "fabric",  # or "snowflake"
            "discovery_timestamp": datetime.utcnow().isoformat(),
            "discovered_schemas": self._extract_schema_names(ddl),
            "total_source_tables": len(osi.datasets),
            "transformation_applied": False
        }

        # 4. NEW: Mark origin of each table
        osi.table_origin_map = {}
        for dataset in osi.datasets:
            schema_name = self._extract_schema_for_table(ddl, dataset.unique_name)
            osi.table_origin_map[dataset.unique_name] = {
                "source_schema": schema_name,
                "is_system_generated": False,
                "source_type": "table"
            }
            # Also update the dataset itself
            dataset.origin_schema = schema_name
            dataset.is_system_generated = False
            dataset.source_type = "table"
            dataset.discovery_timestamp = datetime.utcnow().isoformat()

        return osi

    def _extract_schema_names(self, ddl: str) -> List[str]:
        """Extract unique schema names from DDL."""
        # Parse DDL to find schema references
        # e.g., from "SELECT * FROM dbo.Sales" -> ["dbo"]
        pass

    def _extract_schema_for_table(self, ddl: str, table_name: str) -> str:
        """Extract which schema a specific table came from."""
        # Parse DDL to find "schema.table_name" references
        pass
```

### 2.2 Create Snapshots with Full Metadata

**File**: `src/semabridge/api/main.py`

**Current** (lines 1033, 1104):

```python
snapshot_mgr.create_snapshot(osi.unique_name, osi.model_dump())
```

**New**:

```python
from datetime import datetime, timezone

# After OSI is created and before snapshot...

# 1. Build schema hierarchy
schema_hierarchy = {
    "schemas": [],
    "derived_entities": []
}

# Collect unique schemas
schemas_seen = {}
for dataset in osi.datasets:
    if not dataset.is_system_generated:
        schema_name = dataset.origin_schema or "unknown"
        if schema_name not in schemas_seen:
            schemas_seen[schema_name] = {
                "name": schema_name,
                "type": "original",
                "tables": [],
                "source_platform": osi.schema_discovery_info.get("source_platform"),
                "discovered_at": osi.schema_discovery_info.get("discovery_timestamp")
            }
        schemas_seen[schema_name]["tables"].append(dataset.unique_name)

schema_hierarchy["schemas"] = list(schemas_seen.values())

# Add any system-generated tables as derived entities
for dataset in osi.datasets:
    if dataset.is_system_generated:
        schema_hierarchy["derived_entities"].append({
            "name": dataset.unique_name,
            "type": "generated",
            "created_by": "system_transformation",
            "source_type": dataset.source_type,
            "created_at": dataset.discovery_timestamp or datetime.now(timezone.utc).isoformat()
        })

# 2. Build entity metadata
entity_metadata = {}
for dataset in osi.datasets:
    entity_metadata[dataset.unique_name] = {
        "entity_type": "dataset",
        "origin": "system" if dataset.is_system_generated else "original",
        "source_type": dataset.source_type,
        "original_schema": dataset.origin_schema,
        "source_platform": osi.schema_discovery_info.get("source_platform"),
        "discovered_at": dataset.discovery_timestamp
    }

for metric in osi.metrics:
    entity_metadata[metric.unique_name] = {
        "entity_type": "measure",
        "origin": "original",
        "discovered_at": datetime.now(timezone.utc).isoformat()
    }

for dimension in osi.dimensions:
    entity_metadata[dimension.unique_name] = {
        "entity_type": "dimension",
        "origin": "original",
        "discovered_at": datetime.now(timezone.utc).isoformat()
    }

for relationship in osi.relationships:
    entity_metadata[relationship.unique_name] = {
        "entity_type": "relationship",
        "origin": "original",
        "discovered_at": datetime.now(timezone.utc).isoformat()
    }

# 3. Classify the change
change_classification = {
    "change_type": "auto_discovery",
    "triggered_by": "system",
    "change_reason": f"Discovery refresh for {osi.unique_name}",
    "triggered_at": datetime.now(timezone.utc).isoformat(),
    "source_changes": [
        {
            "type": "schema_discovered",
            "details": f"Discovered {len(osi.datasets)} tables in {len(schemas_seen)} schemas"
        }
    ],
    "applied_transformations": []
}

# 4. Create snapshot with all metadata
snapshot_mgr.create_snapshot(
    adapter=osi.schema_discovery_info.get("source_platform"),
    semantic_state=osi.model_dump(),
    change_description=f"Discovered {len(osi.datasets)} datasets from {osi.schema_discovery_info.get('source_platform')}",
    schema_hierarchy=schema_hierarchy,
    entity_metadata=entity_metadata,
    change_classification=change_classification
)
```

### 2.3 Update SemanticSnapshotManager.create_snapshot()

**File**: `src/semabridge/repository/semantic_snapshot_manager.py`

**Current Signature**:

```python
def create_snapshot(
    self,
    adapter: str,
    semantic_state: Dict[str, Any],
    change_description: str = "",
    parent_snapshot_id: Optional[str] = None,
) -> SemanticSnapshot:
```

**New Signature**:

```python
def create_snapshot(
    self,
    adapter: str,
    semantic_state: Dict[str, Any],
    change_description: str = "",
    parent_snapshot_id: Optional[str] = None,
    schema_hierarchy: Optional[Dict[str, Any]] = None,
    entity_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
    change_classification: Optional[Dict[str, Any]] = None,
) -> SemanticSnapshot:
    """
    Create an immutable snapshot with full metadata.

    Args:
        adapter: Adapter name (snowflake, fabric).
        semantic_state: Full SML JSON state.
        change_description: Description of what changed.
        parent_snapshot_id: Parent snapshot (auto-detected if None).
        schema_hierarchy: NEW - Schema grouping and hierarchy info.
        entity_metadata: NEW - Origin tracking for each entity.
        change_classification: NEW - Classification of change type.

    Returns:
        The created SemanticSnapshot.
    """
    snapshot_id = self._generate_snapshot_id(adapter)

    # Compute hash (unchanged)
    schema_hash = self.compute_schema_hash(semantic_state)

    # Auto-detect parent (unchanged)
    if parent_snapshot_id is None:
        existing = self.list_snapshots(adapter, limit=1)
        if existing:
            parent_snapshot_id = existing[0].snapshot_id

    # Count entities (unchanged)
    counts = self._count_entities(semantic_state)

    # NEW: Apply defaults for metadata
    if schema_hierarchy is None:
        schema_hierarchy = self._infer_schema_hierarchy(semantic_state)

    if entity_metadata is None:
        entity_metadata = self._infer_entity_metadata(semantic_state)

    if change_classification is None:
        change_classification = {
            "change_type": "auto_discovery",
            "triggered_by": "system",
            "change_reason": "Automatic snapshot",
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }

    # Create snapshot with new fields
    snapshot = SemanticSnapshot(
        snapshot_id=snapshot_id,
        adapter=adapter,
        timestamp=datetime.now(timezone.utc).isoformat(),
        semantic_entities=semantic_state,
        schema_hash=schema_hash,
        parent_snapshot_id=parent_snapshot_id,
        change_description=change_description,
        schema_hierarchy=schema_hierarchy,  # NEW
        entity_metadata=entity_metadata,     # NEW
        change_classification=change_classification,  # NEW
        **counts,
    )

    # Save to disk (unchanged)
    self._save_snapshot(snapshot)

    # Update index (unchanged)
    if adapter not in self._index:
        self._index[adapter] = []
    self._index[adapter].append(snapshot_id)
    self._save_index()

    logger.info(
        f"Created snapshot {snapshot_id} for {adapter}: "
        f"{snapshot.measure_count} measures, "
        f"{snapshot.dimension_count} dimensions, "
        f"{len(schema_hierarchy.get('schemas', []))} schemas"
    )
    return snapshot

def _infer_schema_hierarchy(self, semantic_state: Dict[str, Any]) -> Dict[str, Any]:
    """Infer schema hierarchy from semantic state (fallback for old code)."""
    # When explicit schema_hierarchy not provided, build from state
    # Assumes all datasets are "original" origin
    return {
        "schemas": [
            {
                "name": "default",
                "type": "original",
                "tables": [d.get("unique_name") for d in semantic_state.get("datasets", [])],
                "source_platform": semantic_state.get("source_platform")
            }
        ],
        "derived_entities": []
    }

def _infer_entity_metadata(self, semantic_state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Infer entity metadata from semantic state (fallback for old code)."""
    metadata = {}

    for dataset in semantic_state.get("datasets", []):
        metadata[dataset.get("unique_name")] = {
            "entity_type": "dataset",
            "origin": "original",
            "source_type": "table"
        }

    for metric in semantic_state.get("metrics", []):
        metadata[metric.get("unique_name")] = {
            "entity_type": "measure",
            "origin": "original"
        }

    for dimension in semantic_state.get("dimensions", []):
        metadata[dimension.get("unique_name")] = {
            "entity_type": "dimension",
            "origin": "original"
        }

    for rel in semantic_state.get("relationships", []):
        metadata[rel.get("unique_name")] = {
            "entity_type": "relationship",
            "origin": "original"
        }

    return metadata
```

---

## Implementation Phase 3: Enhanced Diff Engine

### 3.1 Add Schema-Aware Comparison

**File**: `src/semabridge/repository/semantic_diff_engine.py`

**Add new dataclass**:

```python
@dataclass
class SchemaChange:
    """A schema-level change (vs entity-level)."""
    change_type: str  # "schema_added", "schema_removed", "tables_moved", etc.
    schema_name: Optional[str] = None
    tables: List[str] = field(default_factory=list)
    from_schema: Optional[str] = None
    to_schema: Optional[str] = None
    details: str = ""
    is_structural_change: bool = True  # Affects schema layout, not data

@dataclass
class EnrichedSemanticDiff(SemanticDiff):
    """Enhanced diff with schema-level changes."""
    schema_changes: List[SchemaChange] = field(default_factory=list)
    system_table_changes: List[EntityChange] = field(default_factory=list)
    original_table_changes: List[EntityChange] = field(default_factory=list)
```

**Add method to SemanticDiffEngine**:

```python
def compare_states_enriched(
    self,
    state_1: Dict[str, Any],
    state_2: Dict[str, Any],
    hierarchy_1: Optional[Dict[str, Any]] = None,
    hierarchy_2: Optional[Dict[str, Any]] = None,
    metadata_1: Optional[Dict[str, Dict[str, Any]]] = None,
    metadata_2: Optional[Dict[str, Dict[str, Any]]] = None,
    from_snapshot_id: str = "current",
    to_snapshot_id: str = "proposed",
    from_adapter: str = "unknown",
    to_adapter: str = "unknown",
) -> EnrichedSemanticDiff:
    """
    Compare two semantic states WITH schema hierarchy awareness.

    This enhances the standard compare_states() with:
    - Schema-level changes
    - System table identification
    - Origin-aware change classification
    """

    # First, do normal entity-level comparison
    base_diff = self.compare_states(
        state_1, state_2,
        from_snapshot_id, to_snapshot_id,
        from_adapter, to_adapter
    )

    # NEW: Compare schema hierarchies
    schema_changes = []
    if hierarchy_1 and hierarchy_2:
        schema_changes = self._compare_schema_hierarchies(hierarchy_1, hierarchy_2)

    # NEW: Classify changes by origin
    system_table_changes = []
    original_table_changes = []

    if metadata_1 and metadata_2:
        for change in base_diff.changes:
            if change.entity_type == EntityType.DATASET:
                # Check if this is a system table
                is_system = (
                    (metadata_2.get(change.entity_id, {}).get("origin") == "system") if change.change_type == ChangeType.ADDED
                    else (metadata_1.get(change.entity_id, {}).get("origin") == "system") if change.change_type == ChangeType.REMOVED
                    else False
                )

                if is_system:
                    system_table_changes.append(change)
                    change.is_breaking = False  # System tables aren't breaking
                else:
                    original_table_changes.append(change)
            else:
                original_table_changes.append(change)
    else:
        original_table_changes = base_diff.changes

    return EnrichedSemanticDiff(
        from_snapshot_id=base_diff.from_snapshot_id,
        to_snapshot_id=base_diff.to_snapshot_id,
        from_adapter=base_diff.from_adapter,
        to_adapter=base_diff.to_adapter,
        summary=base_diff.summary,
        changes=base_diff.changes,
        breaking_changes=base_diff.breaking_changes,
        generated_at=base_diff.generated_at,
        comparison_duration_ms=base_diff.comparison_duration_ms,
        schema_changes=schema_changes,
        system_table_changes=system_table_changes,
        original_table_changes=original_table_changes,
    )

def _compare_schema_hierarchies(
    self,
    hierarchy_1: Dict[str, Any],
    hierarchy_2: Dict[str, Any],
) -> List[SchemaChange]:
    """Compare two schema hierarchies and detect structural changes."""
    changes = []

    # Index schemas by name
    schemas_1 = {s['name']: s for s in hierarchy_1.get('schemas', [])}
    schemas_2 = {s['name']: s for s in hierarchy_2.get('schemas', [])}

    all_schema_names = set(schemas_1.keys()) | set(schemas_2.keys())

    for schema_name in all_schema_names:
        if schema_name not in schemas_1:
            # Schema added
            changes.append(SchemaChange(
                change_type="schema_added",
                schema_name=schema_name,
                tables=schemas_2[schema_name].get('tables', []),
                details=f"New schema '{schema_name}' added"
            ))
        elif schema_name not in schemas_2:
            # Schema removed
            changes.append(SchemaChange(
                change_type="schema_removed",
                schema_name=schema_name,
                tables=schemas_1[schema_name].get('tables', []),
                details=f"Schema '{schema_name}' removed"
            ))
        else:
            # Schema exists in both - check table membership
            tables_1 = set(schemas_1[schema_name].get('tables', []))
            tables_2 = set(schemas_2[schema_name].get('tables', []))

            added = tables_2 - tables_1
            removed = tables_1 - tables_2

            if added:
                changes.append(SchemaChange(
                    change_type="tables_added_to_schema",
                    schema_name=schema_name,
                    tables=list(added),
                    details=f"{len(added)} table(s) added to schema '{schema_name}'"
                ))

            if removed:
                changes.append(SchemaChange(
                    change_type="tables_removed_from_schema",
                    schema_name=schema_name,
                    tables=list(removed),
                    details=f"{len(removed)} table(s) removed from schema '{schema_name}'"
                ))

    # Check for derived entities changes
    derived_1 = {e['name']: e for e in hierarchy_1.get('derived_entities', [])}
    derived_2 = {e['name']: e for e in hierarchy_2.get('derived_entities', [])}

    added_derived = set(derived_2.keys()) - set(derived_1.keys())
    removed_derived = set(derived_1.keys()) - set(derived_2.keys())

    for entity_name in added_derived:
        entity = derived_2[entity_name]
        changes.append(SchemaChange(
            change_type="system_table_added",
            schema_name=None,
            tables=[entity_name],
            details=f"System-generated {entity.get('purpose', 'entity')} '{entity_name}' added by {entity.get('created_by')}",
            is_structural_change=False  # Not a structural change
        ))

    for entity_name in removed_derived:
        entity = derived_1[entity_name]
        changes.append(SchemaChange(
            change_type="system_table_removed",
            schema_name=None,
            tables=[entity_name],
            details=f"System-generated {entity.get('purpose', 'entity')} '{entity_name}' removed",
            is_structural_change=False
        ))

    return changes
```

---

## Implementation Phase 4: Frontend Display Updates

### 4.1 Enhanced Snapshot Display

**File**: `newfrontend/src/components/RepositoryMap/VersionControlPanel.jsx`

**Update the snapshot diff viewer section**:

```jsx
function SnapshotDiffViewer({ diff, enrichedDiff }) {
  // enrichedDiff = result of compare_states_enriched()

  const hasSchemaChanges = enrichedDiff?.schema_changes?.length > 0;
  const hasSystemTableChanges = enrichedDiff?.system_table_changes?.length > 0;
  const hasOriginalTableChanges =
    enrichedDiff?.original_table_changes?.length > 0;

  return (
    <div className="space-y-4 p-4">
      {/* Section 1: Schema-Level Changes */}
      {hasSchemaChanges && (
        <section className="border-l-4 border-blue-500 bg-blue-50 p-4 rounded">
          <h3 className="font-semibold text-blue-900 mb-3">
            📊 Schema Changes ({enrichedDiff.schema_changes.length})
          </h3>
          <div className="space-y-2">
            {enrichedDiff.schema_changes.map((change, idx) => (
              <div
                key={idx}
                className="flex items-start gap-3 bg-white p-2 rounded border border-blue-200"
              >
                <div className="flex-1">
                  <div className="font-medium text-blue-900">
                    {change.change_type.replace(/_/g, " ").toUpperCase()}
                  </div>
                  {change.schema_name && (
                    <div className="text-sm text-blue-700">
                      Schema:{" "}
                      <code className="bg-blue-100 px-2 py-1 rounded">
                        {change.schema_name}
                      </code>
                    </div>
                  )}
                  {change.tables.length > 0 && (
                    <div className="text-sm text-blue-700 mt-1">
                      Tables: {change.tables.join(", ")}
                    </div>
                  )}
                  {change.details && (
                    <div className="text-xs text-gray-600 mt-1">
                      {change.details}
                    </div>
                  )}
                </div>
                <Badge
                  variant={
                    change.change_type.includes("added")
                      ? "success"
                      : change.change_type.includes("removed")
                        ? "danger"
                        : "info"
                  }
                  className="rounded-full flex-shrink-0"
                >
                  {change.change_type.includes("added")
                    ? "➕"
                    : change.change_type.includes("removed")
                      ? "➖"
                      : "🔄"}
                </Badge>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Section 2: Original Schema Table Changes */}
      {hasOriginalTableChanges && (
        <section className="border-l-4 border-green-500 bg-green-50 p-4 rounded">
          <h3 className="font-semibold text-green-900 mb-3">
            📋 Original Schema Table Changes (
            {enrichedDiff.original_table_changes.length})
          </h3>
          <div className="space-y-2">
            {enrichedDiff.original_table_changes.map((change, idx) => (
              <TableChangeRow key={idx} change={change} variant="original" />
            ))}
          </div>
        </section>
      )}

      {/* Section 3: System-Generated Table Changes - WITH WARNING */}
      {hasSystemTableChanges && (
        <section className="border-l-4 border-orange-500 bg-orange-50 p-4 rounded">
          <div className="flex items-start gap-3 mb-3">
            <span className="text-2xl">⚠️</span>
            <div>
              <h3 className="font-semibold text-orange-900">
                System-Generated Table Changes (
                {enrichedDiff.system_table_changes.length})
              </h3>
              <p className="text-sm text-orange-800 mt-1">
                These tables were automatically created or removed by the
                system. They may not reflect changes to your original semantic
                model.
              </p>
            </div>
          </div>
          <div className="space-y-2">
            {enrichedDiff.system_table_changes.map((change, idx) => (
              <TableChangeRow
                key={idx}
                change={change}
                variant="system"
                note={`System-generated (${change.entity_type})`}
              />
            ))}
          </div>
        </section>
      )}

      {/* Section 4: Summary */}
      {!hasSchemaChanges &&
        !hasSystemTableChanges &&
        !hasOriginalTableChanges && (
          <div className="bg-gray-50 p-4 rounded text-center text-gray-600">
            No changes between versions
          </div>
        )}
    </div>
  );
}

// Helper component for table changes
function TableChangeRow({ change, variant = "original", note }) {
  const variantStyles = {
    original: {
      bg: "bg-green-100 hover:bg-green-150",
      border: "border-green-300",
      text: "text-green-900",
    },
    system: {
      bg: "bg-orange-100 hover:bg-orange-150",
      border: "border-orange-300",
      text: "text-orange-900",
    },
  };

  const style = variantStyles[variant];

  return (
    <div
      className={`${style.bg} border ${style.border} p-3 rounded flex items-start justify-between`}
    >
      <div>
        <div className={`font-medium ${style.text}`}>{change.entity_name}</div>
        <div className={`text-sm ${style.text}`}>
          {change.change_type.toUpperCase()}
        </div>
        {note && <div className="text-xs text-gray-600 mt-1">{note}</div>}
        {change.change_reason && (
          <div className="text-xs text-gray-600 mt-1">
            {change.change_reason}
          </div>
        )}
      </div>
      <Badge
        variant={
          change.change_type === ChangeType.ADDED
            ? "success"
            : change.change_type === ChangeType.REMOVED
              ? "danger"
              : "warning"
        }
        className={`rounded-full flex-shrink-0 ${style.text}`}
      >
        {change.change_type === ChangeType.ADDED
          ? "✓ Added"
          : change.change_type === ChangeType.REMOVED
            ? "✗ Removed"
            : "◐ Modified"}
      </Badge>
    </div>
  );
}
```

### 4.2 Update Snapshot Comparison Hook

**File**: `newfrontend/src/hooks/useSnapshotComparison.js`

```javascript
export function useSnapshotComparison() {
  const [diff, setDiff] = useState(null);
  const [enrichedDiff, setEnrichedDiff] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const compareSnapshots = async (snapshotId1, snapshotId2, modelName) => {
    setLoading(true);
    setError(null);
    try {
      // Get basic diff
      const diffResponse = await fetch(
        `/api/snapshots/compare?from=${snapshotId1}&to=${snapshotId2}`,
      );
      if (!diffResponse.ok) throw new Error("Failed to fetch diff");
      const diffData = await diffResponse.json();
      setDiff(diffData);

      // NEW: Get enriched diff with schema awareness
      const enrichedResponse = await fetch(
        `/api/snapshots/compare-enriched?from=${snapshotId1}&to=${snapshotId2}`,
      );
      if (enrichedResponse.ok) {
        const enrichedData = await enrichedResponse.json();
        setEnrichedDiff(enrichedData);
      } else {
        // Fallback to basic diff if enriched not available
        setEnrichedDiff(null);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return { diff, enrichedDiff, loading, error, compareSnapshots };
}
```

---

## Implementation Summary

### What Gets Changed

| Component       | File                         | Changes                                                      | Priority |
| --------------- | ---------------------------- | ------------------------------------------------------------ | -------- |
| **Models**      | semantic_snapshot_manager.py | Add schema_hierarchy, entity_metadata, change_classification | HIGH     |
| **Models**      | intermediate/models.py       | Add origin fields to OSIModel, OSIDataset                    | HIGH     |
| **Converter**   | semantic_view_to_osi.py      | Track schema origins during conversion                       | HIGH     |
| **Creation**    | api/main.py                  | Build metadata before snapshot creation                      | HIGH     |
| **Diff Engine** | semantic_diff_engine.py      | Add schema-aware comparison methods                          | MEDIUM   |
| **API**         | repo_router.py               | Return enriched snapshots with metadata                      | MEDIUM   |
| **Frontend**    | VersionControlPanel.jsx      | Display schema hierarchy and system tables                   | MEDIUM   |
| **Frontend**    | useSnapshotComparison.js     | Call enriched comparison endpoint                            | MEDIUM   |

### Backward Compatibility

- Old snapshots without metadata default to "all original, single schema"
- Diff engine falls back to entity-level comparison if metadata missing
- Frontend gracefully degrades if enriched data not available
- No breaking changes to existing APIs

### Performance Impact

- Additional JSON fields: ~20-30% larger snapshots
- Schema comparison: O(schemas × tables) vs O(entities) for entity comparison
- Mitigation: Lazy-load metadata, use indexes

---

## Testing Checklist

- [ ] SemanticSnapshot model accepts new fields
- [ ] OSIModel captures origin metadata during discovery
- [ ] Snapshots created with schema_hierarchy contain correct schema grouping
- [ ] System-generated tables marked with is_system_generated=True
- [ ] Diff engine detects schema-level changes
- [ ] Frontend displays schema hierarchy sections
- [ ] System table warnings show in diff viewer
- [ ] Backward compatibility with old snapshots works
- [ ] No breaking changes to existing endpoints
