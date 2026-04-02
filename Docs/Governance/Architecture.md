# SemaBridge Architecture

**Last Updated: January 2026**

SemaBridge is a CLI tool for **bi-directional semantic model synchronization** between **Snowflake** and **Microsoft Fabric (Power BI)** with built-in Git-like version control.

---

## High-Level Architecture

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚   Snowflake     â”‚â—„â”€â”€â”€â”€ Reverse â”€â”€â”€â”€â”€â–ºâ”‚  Microsoft      â”‚
â”‚   (Data)        â”‚      Sync          â”‚  Fabric (BI)    â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”˜                    â””â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”˜
         â”‚                                      â”‚
         â”‚  Extract                    Extract  â”‚
         â–¼                                      â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                    SemaBridge CLI                        â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”      â”‚
â”‚  â”‚  Extractor  â”‚  â”‚ Transformer â”‚  â”‚   Emitter   â”‚      â”‚
â”‚  â”‚  (Snowflake â”‚  â”‚ (TMSLâ†”SML)  â”‚  â”‚ (Snowflake/ â”‚      â”‚
â”‚  â”‚   /Fabric)  â”‚  â”‚             â”‚  â”‚  Fabric)    â”‚      â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”˜      â”‚
â”‚         â”‚                â”‚                â”‚              â”‚
â”‚         â””â”€â”€â”€â”€â”€â”€â”€â”€â–ºâ”Œâ”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”â—„â”€â”€â”€â”€â”€â”€â”€â”€â”˜              â”‚
â”‚                   â”‚  SML Model  â”‚                        â”‚
â”‚                   â”‚   (YAML)    â”‚                        â”‚
â”‚                   â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”˜                        â”‚
â”‚                          â”‚                               â”‚
â”‚         â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”              â”‚
â”‚         â–¼                â–¼                â–¼              â”‚
â”‚   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”         â”‚
â”‚   â”‚  DuckDB   â”‚   â”‚ Semantic  â”‚   â”‚ Snapshot  â”‚         â”‚
â”‚   â”‚ Snapshots â”‚   â”‚ Diff Eng  â”‚   â”‚ Manager   â”‚         â”‚
â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜         â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

---

## Core Layers

### 1. CLI Layer (`main.py` + `cli/`)

| Command | Description |
|---------|-------------|
| `validate` | Test Snowflake and Fabric connections |
| `sync` | Full forward pipeline: Snowflake â†’ Fabric |
| `reverse-sync` | Reverse pipeline: Fabric â†’ Snowflake |
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
| `tmsl_to_sml.py` | Fabric TMSL JSON â†’ SML |
| `dax_translator.py` | DAX â†’ SQL translation (3-tier) |

**DAX Translation Tiers:**
- **Tier 1**: Direct aggregations (SUM, AVG, COUNT) â†’ Full SQL
- **Tier 2**: CALCULATE with simple filters â†’ Partial support
- **Tier 3**: Complex/Time-Intelligence â†’ Metadata only

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

### 7. Semantic API Layer (`api/`) â€” NEW

Provides enterprise version management.

| Module | Purpose |
|--------|---------|
| `semantic_version_manager.py` | Version registry with parent-child lineage |
| `semantic_snapshot_manager.py` | Immutable snapshot storage (JSON files) |
| `semantic_diff_engine.py` | Entity-level change comparison |
| `rollback_orchestrator.py` | Cross-adapter rollback coordination |

**Adapter Interface** (`api/interfaces/adapter_rollback_interface.py`):
```uv run class AdapterRollbackInterface(ABC):
    def adapter_name(self) -> str: ...
    def supports_rollback(self) -> bool: ...
    def execute_rollback(self, version_id, ...) -> RollbackResult: ...
    def get_current_state(self) -> Dict: ...
```

---

## Data Flows

### Forward Sync (Snowflake â†’ Fabric)

```
Snowflake DB
       â”‚
       â–¼ (INFORMATION_SCHEMA queries)
SnowflakeExtractor
       â”‚
       â–¼ (Auto-detection)
Detectors (Relationship, Hierarchy, Measure)
       â”‚
       â–¼ (Assembler)
SMLModel (YAML)
       â”‚
       â”œâ”€â”€â–º DuckDBManager.commit_model()
       â”‚
       â–¼ (Generator)
TMSLGenerator â†’ model.bim
       â”‚
       â–¼ (Publisher)
FabricPublisher â†’ Fabric REST API
```

### Reverse Sync (Fabric â†’ Snowflake)

```
Fabric Semantic Model
       â”‚
       â–¼ (REST API: getDefinition)
FabricExtractor â†’ TMSL JSON
       â”‚
       â–¼ (Transformer)
TMSLTransformer + DAXTranslator
       â”‚
       â–¼
SMLModel
       â”‚
       â”œâ”€â”€â–º DuckDBManager.commit_model()
       â”‚
       â–¼ (Emitter)
SnowflakeEmitter â†’ DDL + Cortex YAML
       â”‚
       â–¼ (Execute DDL)
Snowflake Views
```

### Rollback Flow

```
CLI: uv run main.py rollback -d <id> --tag v1.0
       â”‚
       â–¼
RollbackOrchestrator
       â”œâ”€â”€ Validate feasibility
       â”œâ”€â”€ Create pre-rollback snapshot
       â”œâ”€â”€ Execute adapter rollback
       â”œâ”€â”€ Create post-rollback snapshot
       â””â”€â”€ Link rollback lineage
```

---

## Configuration

Settings loaded via Pydantic from environment variables:

```
SNOWFLAKE_* â†’ SnowflakeConfig (account, user, password, warehouse, database, schema)
FABRIC_*    â†’ FabricConfig (tenant_id, client_id, client_secret, workspace_id)
MODEL_*     â†’ ModelConfig (name, description, exclude_tables)
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
â”œâ”€â”€ main.py                 # CLI entry point
â”œâ”€â”€ config/
â”‚   â””â”€â”€ settings.py         # Pydantic settings
â”œâ”€â”€ extract/
â”‚   â”œâ”€â”€ snowflake_extractor.py
â”‚   â”œâ”€â”€ fabric_extractor.py
â”‚   â”œâ”€â”€ relationship_detector.py
â”‚   â”œâ”€â”€ hierarchy_detector.py
â”‚   â””â”€â”€ measure_detector.py
â”œâ”€â”€ transform/
â”‚   â”œâ”€â”€ tmsl_to_sml.py
â”‚   â””â”€â”€ dax_translator.py
â”œâ”€â”€ sml/
â”‚   â”œâ”€â”€ models.py
â”‚   â”œâ”€â”€ assembler.py
â”‚   â””â”€â”€ serializer.py
â”œâ”€â”€ emit/
â”‚   â”œâ”€â”€ snowflake_emitter.py
â”‚   â”œâ”€â”€ fabric_publisher.py
â”‚   â””â”€â”€ tmsl_generator.py
â”œâ”€â”€ state/
â”‚   â””â”€â”€ duckdb_manager.py
â”œâ”€â”€ api/                    # Semantic API Layer
â”‚   â”œâ”€â”€ semantic_version_manager.py
â”‚   â”œâ”€â”€ semantic_snapshot_manager.py
â”‚   â”œâ”€â”€ semantic_diff_engine.py
â”‚   â”œâ”€â”€ rollback_orchestrator.py
â”‚   â””â”€â”€ interfaces/
â”‚       â””â”€â”€ adapter_rollback_interface.py
â”œâ”€â”€ cli/
â”‚   â””â”€â”€ semantic_commands.py
â”œâ”€â”€ utils/
â”‚   â”œâ”€â”€ logger.py
â”‚   â””â”€â”€ cache.py
â””â”€â”€ tests/
```

