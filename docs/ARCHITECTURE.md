# SemaBridge Architecture

**Last Updated: January 2026**

SemaBridge is a CLI tool for **bi-directional semantic model synchronization** between **Snowflake** and **Microsoft Fabric (Power BI)** with built-in Git-like version control.

---

## High-Level Architecture

```
┌─────────────────┐                    ┌─────────────────┐
│   Snowflake     │◄──── Reverse ─────►│  Microsoft      │
│   (Data)        │      Sync          │  Fabric (BI)    │
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         │  Extract                    Extract  │
         ▼                                      ▼
┌─────────────────────────────────────────────────────────┐
│                    SemaBridge CLI                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │
│  │  Extractor  │  │ Transformer │  │   Emitter   │      │
│  │  (Snowflake │  │ (TMSL↔SML)  │  │ (Snowflake/ │      │
│  │   /Fabric)  │  │             │  │  Fabric)    │      │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘      │
│         │                │                │              │
│         └────────►┌──────┴──────┐◄────────┘              │
│                   │  SML Model  │                        │
│                   │   (YAML)    │                        │
│                   └──────┬──────┘                        │
│                          │                               │
│         ┌────────────────┼────────────────┐              │
│         ▼                ▼                ▼              │
│   ┌───────────┐   ┌───────────┐   ┌───────────┐         │
│   │  DuckDB   │   │ Semantic  │   │ Snapshot  │         │
│   │ Snapshots │   │ Diff Eng  │   │ Manager   │         │
│   └───────────┘   └───────────┘   └───────────┘         │
└─────────────────────────────────────────────────────────┘
```

---

## Core Layers

### 1. CLI Layer (`main.py` + `cli/`)

| Command | Description |
|---------|-------------|
| `validate` | Test Snowflake and Fabric connections |
| `sync` | Full forward pipeline: Snowflake → Fabric |
| `reverse-sync` | Reverse pipeline: Fabric → Snowflake |
| `history` | View version history for a dataset |
| `rollback` | Rollback to a previous version |
| `list-projects` | List tracked projects in DuckDB |

### 2. Extract Layer (`extract/`)

| Module | Purpose |
|--------|---------|
| `snowflake_extractor.py` | Batch extraction from `INFORMATION_SCHEMA` |
| `fabric_extractor.py` | Azure AD OAuth + Fabric REST API |
| `relationship_detector.py` | Infer FK relationships |
| `hierarchy_detector.py` | Detect date/geo hierarchies |
| `measure_detector.py` | Identify fact tables and measures |

### 3. Transform Layer (`transform/`)

| Module | Purpose |
|--------|---------|
| `tmsl_to_sml.py` | Fabric TMSL JSON → SML |
| `dax_translator.py` | DAX → SQL translation (3-tier) |

**DAX Translation Tiers:**
- **Tier 1**: Direct aggregations (SUM, AVG, COUNT) → Full SQL
- **Tier 2**: CALCULATE with simple filters → Partial support
- **Tier 3**: Complex/Time-Intelligence → Metadata only

### 4. SML Layer (`sml/`)

Intermediate representation using YAML-based Semantic Modeling Language.

| Class | Purpose |
|-------|---------|
| `SMLModel` | Root container |
| `SMLDataset` | Table/query definition |
| `SMLColumn` | Column with normalized data types |
| `SMLMetric` | Measure with DAX/SQL expression |
| `SMLRelationship` | FK relationship with cardinality |
| `SMLDimension` | Logical dimension with hierarchies |

### 5. Emit Layer (`emit/`)

| Module | Purpose |
|--------|---------|
| `snowflake_emitter.py` | Generate DDL: CREATE VIEW + Cortex YAML |
| `fabric_publisher.py` | Upload model.bim to Fabric REST API |
| `tmsl_generator.py` | Generate Fabric-compatible model.bim |

### 6. State Layer (`state/`)

Git-like version control using **DuckDB**.

**Schema:**
```sql
semantic_projects (project_id, name, workspace_id, head_snapshot_id)
semantic_snapshots (snapshot_id, project_id, timestamp, version_tag, sml_blob)
semantic_changes (change_id, snapshot_id, object_type, diff_type, old/new_value)
```

### 7. Semantic API Layer (`api/`) — NEW

Provides enterprise version management.

| Module | Purpose |
|--------|---------|
| `semantic_version_manager.py` | Version registry with parent-child lineage |
| `semantic_snapshot_manager.py` | Immutable snapshot storage (JSON files) |
| `semantic_diff_engine.py` | Entity-level change comparison |
| `rollback_orchestrator.py` | Cross-adapter rollback coordination |

**Adapter Interface** (`api/interfaces/adapter_rollback_interface.py`):
```python
class AdapterRollbackInterface(ABC):
    def adapter_name(self) -> str: ...
    def supports_rollback(self) -> bool: ...
    def execute_rollback(self, version_id, ...) -> RollbackResult: ...
    def get_current_state(self) -> Dict: ...
```

---

## Data Flows

### Forward Sync (Snowflake → Fabric)

```
Snowflake DB
       │
       ▼ (INFORMATION_SCHEMA queries)
SnowflakeExtractor
       │
       ▼ (Auto-detection)
Detectors (Relationship, Hierarchy, Measure)
       │
       ▼ (Assembler)
SMLModel (YAML)
       │
       ├──► DuckDBManager.commit_model()
       │
       ▼ (Generator)
TMSLGenerator → model.bim
       │
       ▼ (Publisher)
FabricPublisher → Fabric REST API
```

### Reverse Sync (Fabric → Snowflake)

```
Fabric Semantic Model
       │
       ▼ (REST API: getDefinition)
FabricExtractor → TMSL JSON
       │
       ▼ (Transformer)
TMSLTransformer + DAXTranslator
       │
       ▼
SMLModel
       │
       ├──► DuckDBManager.commit_model()
       │
       ▼ (Emitter)
SnowflakeEmitter → DDL + Cortex YAML
       │
       ▼ (Execute DDL)
Snowflake Views
```

### Rollback Flow

```
CLI: python main.py rollback -d <id> --tag v1.0
       │
       ▼
RollbackOrchestrator
       ├── Validate feasibility
       ├── Create pre-rollback snapshot
       ├── Execute adapter rollback
       ├── Create post-rollback snapshot
       └── Link rollback lineage
```

---

## Configuration

Settings loaded via Pydantic from environment variables:

```
SNOWFLAKE_* → SnowflakeConfig (account, user, password, warehouse, database, schema)
FABRIC_*    → FabricConfig (tenant_id, client_id, client_secret, workspace_id)
MODEL_*     → ModelConfig (name, description, exclude_tables)
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| SML as Intermediate | Decouples source/target formats for extensibility |
| DuckDB for State | Embedded, zero-config, native JSON support |
| Non-destructive Rollback | Rollback creates new snapshot; history preserved |
| Tiered DAX Translation | Graceful degradation for complex expressions |
| File-based Snapshots | Simple auditing, no additional DB required |
| Adapter Interface | Standardized rollback contract for all platforms |

---

## File Structure

```
semabridge/
├── main.py                 # CLI entry point
├── config/
│   └── settings.py         # Pydantic settings
├── extract/
│   ├── snowflake_extractor.py
│   ├── fabric_extractor.py
│   ├── relationship_detector.py
│   ├── hierarchy_detector.py
│   └── measure_detector.py
├── transform/
│   ├── tmsl_to_sml.py
│   └── dax_translator.py
├── sml/
│   ├── models.py
│   ├── assembler.py
│   └── serializer.py
├── emit/
│   ├── snowflake_emitter.py
│   ├── fabric_publisher.py
│   └── tmsl_generator.py
├── state/
│   └── duckdb_manager.py
├── api/                    # Semantic API Layer
│   ├── semantic_version_manager.py
│   ├── semantic_snapshot_manager.py
│   ├── semantic_diff_engine.py
│   ├── rollback_orchestrator.py
│   └── interfaces/
│       └── adapter_rollback_interface.py
├── cli/
│   └── semantic_commands.py
├── utils/
│   ├── logger.py
│   └── cache.py
└── tests/
```
