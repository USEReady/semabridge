# Semabridge Codebase Guide (`src/semabridge`)

A deep, navigable walkthrough of the entire backend package — what each layer does,
the key files, the data models, and how a conversion flows end-to-end. This is the
companion to the short [System Overview](./overview.md); read that first for the
30-second version, then use this guide to actually find and understand the code.

> Scope: `src/semabridge/` (~119k LOC, 21 sub-packages). The `frontend/`, `Tests/`,
> and `Scripts/` directories are mentioned only in passing.

---

## 1. Orientation

**Semabridge** is a **bidirectional semantic-model pipeline** between **Snowflake**
(semantic views / Cortex Analyst) and **Microsoft Fabric Power BI** (TMSL / `model.bim`),
with `.pbix` and Databricks as additional endpoints.

The one architectural rule that explains the whole design:

> **Every conversion flows `Source → OSI → Target`.** Direct point-to-point
> converters (e.g. a `Snowflake → Fabric` transformer) are forbidden.

**OSI** (Open Semantic Intermediate) is a vendor-neutral in-memory model. Because
everything passes through it, adding a new platform costs **N+M** converters
(one extract + one emit), not **N×M**. OSI is the "Rosetta Stone" of the system.

**Stack:** Python 3.11+, FastAPI/Uvicorn (API), Typer/Rich (CLI), Pydantic v2
(models), SQLAlchemy + Alembic + DuckDB (state/version control), optional Temporal
(durable workflows) and Ray (parallel diffing), React/Vite frontend.

**How to read this guide:** §2–§3 give the mental model and folder map. §4–§8 walk
the pipeline from the inside out (data models → orchestration → connectors →
persistence). §9–§12 cover the interfaces (API, CLI, auth) and support code. §13 is
a single worked example that ties everything together. §14 is a glossary + a
"where do I look for X" table.

---

## 2. The big picture (layered architecture)

```
┌──────────────────────────────────────────────────────────────────────┐
│  INTERFACES                                                            │
│  api/ (FastAPI: routers → controllers → services)   cli/ (Typer)      │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │
┌───────────────────────────────▼──────────────────────────────────────┐
│  CORE ORCHESTRATION (system-agnostic)                                  │
│  core/conversion_router  ·  core/engine (10-step lifecycle)            │
│  core/sync_orchestrator  ·  core/container (DI)  ·  core/settings      │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │
┌───────────────────────────────▼──────────────────────────────────────┐
│  TRANSFORMATION                                                        │
│  converter/  (TMSL ↔ OSI ↔ SML, DAX↔SQL)                              │
│  intermediate/ (OSI models)   sml/ (SML models)   formats/ (schemas)  │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │
┌───────────────────────────────▼──────────────────────────────────────┐
│  CONNECTORS (plugin-based; core never imports a vendor directly)       │
│  connectors/ (Snowflake, Fabric, Databricks)   plugins/ (registry)    │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │
┌───────────────────────────────▼──────────────────────────────────────┐
│  PERSISTENCE                                                           │
│  repository/ (ORM, snapshots, versions, diffs)   migrations/ (Alembic)│
└────────────────────────────────────────────────────────────────────────┘

Cross-cutting:  auth/ · sync/ · orchestration/ + distributed/ · utils/ · domain/
```

The golden rule from `CLAUDE.md`: **`core/`, `converter/`, and `intermediate/` must
not import from `connectors/<vendor>/`.** Vendor specifics live behind the connector
factory and the plugin registry.

---

## 3. Folder map

Every top-level directory under `src/semabridge/`:

| Directory | LOC | Responsibility |
|---|---:|---|
| `connectors/` | ~28.9k | External integrations: Snowflake, Fabric/Power BI, Databricks extractors & emitters, plus the connector `factory`. |
| `api/` | ~20.8k | FastAPI app: routers → controllers → services (4-tier), bootstrap, middleware, websockets. |
| `core/` | ~20.0k | Execution lifecycle, conversion routing, DI container, config/settings, validation, concurrency. **System-agnostic.** |
| `converter/` | ~15.9k | All transforms (TMSL↔OSI↔SML, DAX↔SQL, semantic-view↔OSI). Everything routes through OSI. |
| `repository/` | ~9.3k | DuckDB/SQLAlchemy persistence: ORM models, snapshots, versions, diffs, command audit. |
| `utils/` | ~5.7k | Logging and shared utilities. |
| `cli/` | ~4.6k | Typer commands (`semantic`, `sync`, `diff`, `version`, `logs`, deploy/pipeline). |
| `sync/` | ~3.2k | In-process batch sync orchestration: jobs, conflicts, schema evolution. |
| `auth/` | ~2.3k | JWT/bcrypt app auth + Fabric/MSAL token validation & refresh. |
| `formats/` | ~2.2k | `semabridge.yaml` schema DSL, YAML validation, identifier sanitizer. |
| `migrations/` | ~1.5k | Alembic migrations for the DuckDB/SQL schema. |
| `sml/` | ~1.4k | SML (Semantic Modeling Language) models, assembler, YAML serializer. |
| `orchestration/` | ~1.2k | Local-vs-Temporal adapter + Temporal workflow/activities/worker. |
| `intermediate/` | ~0.8k | **The OSI models** — the canonical in-memory format. |
| `plugins/` | ~0.5k | Plugin registry + `SemaBridgePlugin` base (extensibility seam). |
| `distributed/` | ~0.4k | Ray actors for parallel SML diffing (with local fallback). |
| `config/` | ~0.1k | Runtime configuration loading. |
| `storage/` | ~0.1k | Blob/artifact storage helpers. |
| `domain/` | ~37 | Exception hierarchy (`SemaBridgeError` and friends). |

`__main__.py` makes the package runnable; `__init__.py` exposes the public surface.

---

## 4. The pipeline heart — OSI, SML, formats, converters

This is the most important part of the system. Everything else exists to feed it.

### 4.1 OSI — `intermediate/models.py`

OSI is a tree of Pydantic v2 models. Top-level container is **`OSIModel`**:

```
OSIModel
├── unique_name, label, description, version, source_platform, created_at
├── metadata, platform_metadata        # platform hints e.g. {"fabric": {...}, "snowflake": {...}}
├── datasets:      List[OSIDataset]     # tables / entities
│     └── columns: List[OSIColumn]
├── metrics:       List[OSIMetric]      # measures / calculations
├── dimensions:    List[OSIDimension]   # → OSIAttribute, OSIHierarchy → OSILevel
└── relationships: List[OSIRelationship]
```

Key model classes (all in `intermediate/models.py`):

| Class | Holds |
|---|---|
| `OSIColumn` | `data_type` (`OSIDataType` enum), `is_key`, `is_measure_candidate`, `default_aggregation` (`OSIAggregationType`), `format_string`, `folder`, `synonyms`, `sample_values`, Cortex search hints. |
| `OSIDataset` | `source_table`/`source_schema`/`source_database`, `is_fact`, `is_hidden`, `row_count`, `columns`. |
| `OSIMetric` | `dataset`, `source_column` **or** `expression` **or** `sql_expression`, `aggregation`, `complexity_tier` (0–5 DAX difficulty), `depends_on_measures`, Cortex `access_modifier`/`synonyms`. |
| `OSIRelationship` | `from_dataset`/`from_columns`, `to_dataset`/`to_columns` (composite keys OK), `cardinality` (`OSICardinality`), `cross_filter_direction`, `is_active`. |
| `OSIDimension` / `OSIAttribute` / `OSIHierarchy` / `OSILevel` | Logical groupings and drill-down paths. |
| `OSIExpressionDialect` | Same expression stored in multiple dialects (DAX, T-SQL, Snowflake SQL, …). |

Built-in validation (this is why OSI is trustworthy downstream):

- **`validate_prd_boundaries()`** (a Pydantic `@model_validator`) — hard-fails if a
  model has **> 75 datasets** or **> 75 relationships**; warns if total columns exceed
  `SEMABRIDGE_RECOMMENDED_MODEL_TOTAL_COLUMN_LIMIT` (default **100**, an LLM
  context-window concern). Per-dataset column count warns at `..._DATASET_COLUMN_LIMIT`
  (default 75).
- **`validate_integrity()`** — returns a list of dangling references (metrics/
  relationships/dimensions pointing at non-existent datasets).
- Helpers: `get_dataset()`, `get_metric()`, `get_relationship()`,
  `find_relationships_for_dataset()`.

### 4.2 SML — `sml/`

SML mirrors OSI but is the **YAML-serializable, deployment-oriented** representation.
`SMLMetric` adds deployment fields like `sync_enabled` and `sync_failure_reason`.

- `sml/models.py` — `SMLModel`, `SMLDataset`, `SMLColumn`, `SMLMetric`, … with a
  `DataType` enum that does the platform mapping: `DataType.from_snowflake("NUMBER")
  → DECIMAL`, `DataType.to_powerbi() → "string"/"dateTime"/…`.
- `sml/assembler.py` — builds an `SMLModel` from extracted Snowflake metadata.
- `sml/serializer.py` — serializes SML to/from YAML.

### 4.3 Formats — `formats/`

Defines and validates the **`semabridge.yaml`** project file (not the OSI/SML models).

- `formats/schema.py` — a small schema DSL (`FieldSchema`) describing the legal shape
  of `semabridge.yaml`: `source`, `target`, `sync.direction`, `auth`, `logging`.
  The `auth` section is **env-var-reference-only** — passwords/tokens may never be
  inlined (enforces the "secrets via env only" rule).
- `formats/yaml_validator.py` — validates config against the schema, with "did you
  mean?" suggestions for typos and deprecation warnings.
- `formats/sanitizer.py` — identifier sanitization shared by emitters.

### 4.4 Converters — `converter/`

All transforms route through OSI. The important ones:

| File | Direction | Notes |
|---|---|---|
| `converter/tmsl_to_osi.py` | Fabric/PBIX TMSL → OSI | Parses `model.bim`; filters auto date tables (`LocalDateTable_*`); guards reserved model names. |
| `converter/semantic_view_to_osi.py` | Snowflake DDL → OSI | Regex/state-machine parser of `SHOW CREATE SEMANTIC VIEW` (TABLES/RELATIONSHIPS/DIMENSIONS/MEASURES). |
| `converter/osi_to_sml.py` | OSI → SML | Enriches metrics with translated SQL; topologically sorts inter-measure dependencies; batches the hard DAX. |
| `converter/sml_to_osi.py` | SML → OSI | Reverse path. |
| `converter/dax_translator.py` | DAX → SQL | **5-tier strategy** (below). |

**The 5-tier DAX→SQL translator** (`dax_translator.py`) is a highlight:

1. **Tier 1** — direct aggregations (`SUM`, `COUNT`, `AVG`) → trivial SQL.
2. **Tier 2** — filter/context shifts → SQL `WHERE`/subqueries.
3. **Tier 3** — `CALCULATE`, `SUMX`, calculated columns → dynamic SQL.
4. **Tier 4** — time-intelligence (`TOTALYTD`, …) → window functions.
5. **Tier 5** — everything left over → **LLM fallback** (Gemini/Ollama),
   `batch_translate_tier5()` does it in one batched call (≈90% fewer API calls).

Each metric ends up with a `sql_expression` + `complexity_tier` + `sync_enabled` flag.

---

## 5. Core orchestration — `core/`

`core/` is large (~87 files). The brains you'll touch most:

### 5.1 Conversion router — `core/conversion_router.py`

A pure routing table: `(source_type, target_type) → ordered list of step names`.
`get_router()` returns the singleton. Registered paths (symbolic steps consumed by
the engine):

| Source → Target | Steps |
|---|---|
| `fabric → snowflake` | `fabric_extract → tmsl_to_osi → osi_to_sml → sml_to_snowflake` |
| `snowflake → fabric` | `snowflake_extract → osi_to_sml → sml_to_tmsl → tmsl_to_fabric` |
| `pbix → snowflake` | `pbix_extract → tmsl_to_osi → osi_to_sml → sml_to_snowflake` |
| `pbix → fabric` | `pbix_extract → … → sml_to_tmsl → tmsl_to_fabric` |
| `snowflake → pbix`, `fabric → pbix` | via Fabric publish (download `.pbix` from Fabric) |
| `fabric → fabric`, `snowflake → snowflake` | re-publish / re-deploy to a different workspace/schema |

`route()` raises if a pair isn't registered — there is no silent fallback.

### 5.2 Execution engine — `core/engine/engine.py`

The `ExecutionEngine` enforces a **mandatory 10-step lifecycle**, executed in order,
each step recorded in a `RunSummary`. Authoritative names (`core/run_summary.py::STEP_NAMES`):

1. **Load & Validate Configuration**
2. **Initialize Identifiers** (project_id / run_id / source & target types → `RunContext`)
3. **Resolve Authentication**
4. **Extract from Source** → produces a `SourceFormat` (`core/source_format.py`)
5. **Validate & Parse Source Format**
6. **Convert to Canonical SML** (Source → OSI → SML, incl. DAX↔SQL); a conditional
   **Step 6a** first extracts the existing target model when `sync_mode = UPSERT`
7. **Persist Artifacts** (source artifact + SML snapshot to the repository)
8. **Convert to Target Format** (optional; SML → TMSL or Snowflake DDL)
9. **Deploy to Target** (optional; skipped on dry runs)
10. **Finalize Run** (build `RunSummary`, optionally push to a Snowflake observability table)

Supporting files: `core/engine/context.py` (`RunContext` accumulates `source_format`,
`osi_model`, `sml_model`, artifact IDs, `sync_mode`), and the step sub-packages
`core/engine/extraction/`, `core/engine/conversion/`, `core/engine/deployment/`,
`core/engine/targets/`. `core/execution_engine.py` is a backward-compat shim that
re-exports from `core/engine/`.

### 5.3 DI container — `core/container.py`

A lightweight lazy container (`@cached_property` singletons). `get_container()` exposes
`settings`, `db_manager` (`ModelRepository`), `engine` (`ExecutionEngine`),
`scheduler_service`, `version_control_service`. The CLI and background tasks use this;
the API uses FastAPI `Depends` instead (see §9).

### 5.4 Multi-model sync & config

- `core/sync_orchestrator.py` — deploys **many** models with resilience: Phase 0
  validate (classify VALID/WARN/BLOCKED) → Phase 1 dependency ordering (dimensions →
  facts → derived) → Phase 2 resilient deploy (retry transient, skip blocked) →
  Phase 3 structured `SyncReport`.
- `core/sync_modes.py` — **COPY** (source replaces target) vs **UPSERT** (union;
  source wins on conflicts; target-only entities preserved). `apply_sync_mode()`
  merges at dataset/column, metric, and dimension/attribute granularity.
- `core/settings.py` — Pydantic settings (`SnowflakeConfig`, `FabricConfig`,
  `DatabricksConfig`, `TelemetryConfig`) loaded from `.env`/environment.
- `core/config_loader.py` — locates and loads the YAML project file.

### 5.5 The rest of `core/` (by theme)

- `core/concurrency/` — parallel model processing (orchestrator, delta detector,
  retry manager, batch persistence, resource manager).
- `core/validation/` — multi-tier validation (primary-key resolver, identifier &
  relationship checks, Cortex-specific validator).
- `core/sentinel/` — observability/governance (monitor, lineage, governance).
- `core/interfaces.py` — the abstract contracts: `BaseConnector`, `BaseExtractor`
  (`extract_to_osi`), `BaseEmitter` (`emit(osi_model)`). **Constructors must not take
  secrets** — credentials are read from the environment inside `authenticate()`.
- `core/behavior.py` (`ConnectorBehavior`), `core/run_summary.py`,
  `core/source_format.py`, `core/exceptions.py`.

---

## 6. Connectors & the plugin seam

### 6.1 Two extension mechanisms

- **Plugin registry** — `plugins/base.py` (`SemaBridgePlugin` ABC: `name`, `version`,
  `plugin_type` ∈ CONNECTOR/TRANSFORMER/VALIDATOR/EMITTER/HOOK, plus
  `initialize/execute/cleanup`) and `plugins/registry.py` (singleton `PluginRegistry`
  with `register/get/get_by_type/initialize_all/list_mcp_tools`). This is the
  forward-looking seam for **new** vendors — no core edits required.
- **Connector factory** — `connectors/factory.py`: `make_source_extractor(type, cfg)`
  and `make_target_emitter(type, cfg)` dispatch a type string to the concrete class
  (`snowflake` → `SnowflakeExtractor`, `fabric`/`powerbi` → `FabricExtractor`,
  `databricks` → `DatabricksPublisher`). This is the eager path used by the engine today.

Both honor `core/interfaces.py`, so callers stay vendor-agnostic.

### 6.2 Snowflake

- `connectors/snowflake_extractor.py` — batch reads `INFORMATION_SCHEMA` (tables,
  one batched column query, PK/FK via `SHOW`, metric discovery), optional parallelism.
- `connectors/snowflake_emitter.py` — façade that deploys SML/OSI as **semantic views
  + Cortex Analyst YAML**, delegating to managers: connection, schema (TTL cache +
  table verification), `MetricExpressionTranslator` (DAX→SQL), `MeasureSynchronizer`,
  `SemanticViewBuilder` (DDL), duplicate-name handling, `IdentifierSanitizer`.
- `connectors/snowflake_connection.py` — auth: password / key-pair / SSO
  (externalbrowser), all from env.

### 6.3 Fabric / Power BI

- `connectors/fabric_extractor.py` — REST reads (`/workspaces`, `/semanticModels`),
  workspace name→GUID resolution, 401-refresh-and-retry.
- `connectors/fabric_publisher.py` — publishes **base64-encoded TMSL** via REST
  (`updateDefinition`), polls long-running ops. No XMLA needed.
- `connectors/tmsl_generator.py` — SML → `model.bim` (compatibility level 1567;
  tables/relationships/measures-as-DAX/hierarchies; skips auto date tables).
- Auth precedence: request token → `FABRIC_ACCESS_TOKEN` env → service principal
  (MSAL confidential client) → device-code/delegated (refresh-token) flow.

### 6.4 Databricks

- `connectors/databricks_publisher.py` — dual role (source extract + target emit).
  Deploys via the Statements API in two modes: native **`WITH METRICS LANGUAGE YAML`**
  (Unity Catalog) or a plain `CREATE VIEW` SQL fallback; includes a metadata catalog
  table and tiered measure translation.

### 6.5 Adding a connector

Implement extractor/emitter against `core/interfaces.py` (or a `SemaBridgePlugin`),
register it (factory mapping or `registry.register(...)`), and add a route in
`conversion_router.py`. **No edits to the engine or orchestrator** — if you find
yourself editing those, the design intends a plugin instead.

---

## 7. Sync, orchestration & distributed execution

Three layers, increasing in scale:

- **In-process batch** — `sync/orchestrator.py` (`SyncOrchestrator`) coordinates a
  `SyncJob` of many `SyncJobItem`s (`sync/models.py`): extract → convert → detect
  conflicts → deploy, sequential or parallel, **resumable** via `SyncCheckpoint`.
  `sync/conflict_resolver.py` and `sync/schema_evolution.py` handle drift; unresolved
  conflicts pause the job (`resolve_and_resume()`). `sync/repository.py` persists job
  state. Enums cover `SyncDirection`, `SyncJobStatus`, `ConflictResolution`, etc.
- **Durable workflows** — `orchestration/temporal/` runs the per-model
  Extract → Diff → Emit → Snapshot as a Temporal workflow (`workflows.py`,
  `activities.py`, `worker.py`) with per-phase retry policies; survives restarts,
  handles Fabric 429 back-off durably.
- **Parallel diffing** — `distributed/ray/` (`coordinator.py`, `actor_diff.py`) farms
  SML diffs to stateful Ray actors, with a **local fallback** if Ray is unavailable.

`orchestration/orchestrator_adapter.py` is the single entry point: `run_sync(...)`
picks **local** (default) or **temporal** mode via `SEMABRIDGE_ORCHESTRATOR` (or a
`mode=` argument). All modes call the **same** connector classes — Temporal/Ray just
wrap them as durable/parallel units. Use local for interactive/CLI/tests; Temporal
for large, fault-tolerant, multi-tenant batches.

---

## 8. Persistence & version control — `repository/` + `migrations/`

DuckDB by default (PostgreSQL/SQLite also supported) via `SEMABRIDGE_DATABASE_URL`.

### 8.1 Access layer

- `repository/model_repository.py` — the primary repository: session-scoped CRUD over
  snapshots, runs, versions, mappings, credentials; (de)serializes SML blobs.
- `repository/orm/session_factory.py` — `DatabaseManager` (engine + `sessionmaker`
  singleton), dialect-aware.
- `repository/orm/base.py` — declarative `Base`; `repository/orm/models.py` is a
  compat shim re-exporting the per-domain model modules.

### 8.2 ORM model groups (`repository/orm/*.py`)

| Module | Tables (highlights) |
|---|---|
| `project_models.py` | `Project`, `LocalFolder`, `RetentionPolicy`. |
| `snapshot_models.py` | `SnapshotRow` (SML blob + status/ts, indexed), `Change`, `ModelVersion`, `ModelVersionHistory`, `SchemaVersionRow`. |
| `run_models.py` | `Run` (lifecycle, before/after snapshot ids), `SourceArtifact`, `SyncConflictRow`, `SyncCheckpointRow`. |
| `auth_models.py` | `User`, `UserCredential`, `RefreshToken`, `PasswordResetToken`. |
| `account_models.py` | `Account` (multi-session connector identity: encrypted token, owner, default), `Post`. |
| `infra_models.py` | `Credential` (vault; `owner_id=0` = global/CLI), `CommandLog`. |
| `mapping_models.py` | `ModelMappingRow` (source↔target registry + last OSI hash), `SynonymOverride`. |
| `schedule_models.py` | `SyncJob`, `SyncJobItem`. |

### 8.3 Semantic version control

- `repository/semantic_snapshot_manager.py` — immutable `SemanticSnapshot`s
  (`v{timestamp}_{adapter}_{seq}`), stored under `.semantic_metadata/snapshots/`.
- `repository/semantic_version_manager.py` — `VersionRecord` registry with parent/
  child lineage and SHA-256 schema hashes.
- `repository/semantic_diff_engine.py` — entity-level diff (`ChangeType`,
  `EntityType`, `EntityChange`, `DiffSummary`, `SemanticDiff`), flags breaking changes.
- `repository/command_logger.py` — CLI audit (`CommandType`/`ActionType`/`CommandStatus`).

### 8.4 Migrations — `migrations/`

Alembic, configured in `migrations/env.py` (resolves `SEMABRIDGE_DATABASE_URL`,
imports all ORM models for autogenerate). ~20 versions in `migrations/versions/` from
`ff10eddde5b9_initial_schema` through sync-mode columns, account ownership, synonym
overrides, conflict-approval fields, and password-reset tokens. **Run `make migrate`
after every pull.**

---

## 9. The API layer — `api/`

### 9.1 Wiring

`api/main.py` builds the FastAPI app (and sets the Windows selector event-loop policy).
`api/bootstrap/app_factory.py` owns `configure_app()` (`.env`, logging, Sentry,
middleware, CORS) and the `lifespan` (create ORM tables / run migrations on startup,
assign orphaned accounts, install the websocket alert handler, poll MSAL refresh,
launch the scheduler). `api/app_setup.py` is a backward-compat shim re-exporting from
`bootstrap/`.

Middleware (`api/bootstrap/middleware.py`): request-ID, content-size limit, security
headers, CSRF, request/response logging, and `AuthMiddleware` (from `auth/`).

> Note: routers are currently registered directly in `main.py`. A
> `bootstrap/router_registry.py` exists but is **dead code** (never called); it also
> references an unregistered `export_router`. See the restructure discussion below.

### 9.2 The request path & the 4-tier service pattern

```
HTTP → router → controller → service → repository / connector → response
                                  ↑ domain exceptions → error_handlers.py → HTTP status
```

`api/services/` (50 files) follows a deliberate but verbose **4-tier** pattern:

```
controller            controllers/projects_controller.py
   → X_service         services/projects_service.py     (5–11 line stub façade)
   → X_domain_service  services/project_domain_service.py (aggregator hub)
   → X_*_impl / feature services/project_projects_impl.py … (the real logic)
   → X_shared          services/project_shared.py        (singletons/bootstrap)
```

So apparent "duplicate pairs" (`folders_service`/`folder_service`,
`versioning_service`/`version_service`, `connections_service`/`connection_domain_service`)
are **intentional façades**, not bugs — each controller imports exactly one stub.

*Worked trace (get a project):* `projects_controller.get_project` checks ownership
(`project_ownership_service.is_project_owned_by_user`), calls
`projects_service.get_project_compat` → `project_domain_service` → `project_projects_impl`,
which reads via `model_repository`, and returns; any `domain/` exception is mapped to an
HTTP status by `api/error_handlers.py`.

### 9.3 Router / controller catalog

Domain routers compose per-feature controllers:

- **`routers/core_router.py`** → health, discovery, semantic, model, config, history,
  comparator controllers.
- **`routers/project_router.py`** → projects, graph, project-runs, folders, jobs,
  mappings, versioning, pbix, composite controllers.
- **`routers/connection_router.py`** → connections, fabric-connection,
  databricks-connection controllers.
- Standalone: `routers/diagnostics_router.py`, `routers/export_router.py`,
  `routers/synonyms_router.py`, `routers/comparator_router.py`, `routers/mapping_router.py`.
- Legacy top-level routers still at the `api/` root: `account_router.py`,
  `repo_router.py`, `sync_router.py`, `discovery_api.py`, `browse.py`,
  `settings_api.py`, `ui.py`.

Support: `api/deps.py` (`get_db` session, `get_model_repository`, fabric context,
current user), `api/dependencies.py` (cached settings, engine, scheduler),
`api/error_handlers.py` (domain→HTTP), `api/websocket_alerts.py` (`/ws/alerts`
broadcasting + a logging handler that turns warn/error logs into live alerts).

### 9.4 Known rough edges (so the code matches what you'll see)

These are real and worth knowing — they're the subject of a separate (paused)
restructure plan; this guide only documents them:

- `services/connection_domain_service.py` (~1.5k LOC) and `services/project_shared.py`
  (~1k LOC) begin with `main.py`'s header and re-import FastAPI/CORS **and routers** —
  vestigial code carved out of the old monolith (a layering violation: services should
  not import routers).
- A lazy-import cycle chain (`mapping_service → snapshot_service → run_service →
  schedule_service`, plus `version_service`) is worked around with in-function imports.
- `connection_*` was never split into `_impl` modules the way `core_*`/`project_*` were.
- `services/__init__.py` is empty; naming mixes singular/plural and
  `_service`/`_impl`/`_domain_service`/`_api_service`.

---

## 10. CLI — `cli/`

`cli/main.py` aggregates Typer sub-apps and the `cli/commands/` groups
(deploy, pipeline, utility, version-history). Command families:

| Group | Commands (selection) |
|---|---|
| `semantic_commands.py` | `snapshot-create`, `snapshot-list`, `snapshot-compare`, `version-create`, `rollback`, `status` |
| `sync_commands.py` | `run` (`--direction`, `--pbix-folder`, `--sf-schema`, `--conflict`), `status`, `jobs`, `cancel`, `resolve`, `mappings`, `history` |
| `diff_commands.py` | `source` (live vs HEAD), `versions` (id↔id), `semantic` (measures/dimensions/hierarchies) |
| `version_commands.py` | `list`, `compare`, `rollback` (per model) |
| `logs_commands.py` | `list`, `show`, `export` |

Every command brackets its work with `CommandLogger` (`log_start` → `log_success` /
`log_failure`), persisting to the `CommandLog` table. Entry point:
`semabridge = "semabridge.cli.main:main"`.

---

## 11. Auth — `auth/`

App auth (users) and Fabric auth (Microsoft tokens) are distinct concerns.

- **App JWT** — `auth/tokens.py` (HS256; ~15-min access token, 7-day rotating
  refresh token hashed in DB, plain token in an HttpOnly cookie; secret from
  `JWT_SECRET_KEY`). `auth/middleware.py` enforces `Authorization: Bearer …`, exempts
  public paths (`/api/health*`, `/auth/*`, `/docs`, `/ws/*`, …), and — when
  `AUTH_ENABLED=false` — runs a single-user dev mode (`user_id=1`). `auth/passwords.py`
  is bcrypt (cost 12).
- **Fabric/MSAL** — `auth/fabric_validator.py` validates Microsoft tokens by
  **claims** (`exp`/`aud`/`iss`/`nbf`/`tid`) with a JWKS + payload cache; it does not
  verify the signature (Microsoft first-party tokens use opaque internal MACs).
  `auth/token_resolver.py` resolves a usable Fabric token by precedence: identity
  account token → request bearer → stored MSAL token (with **silent refresh** +
  one-time-use rotation) → `FABRIC_ACCESS_TOKEN`.
- Support: `auth/encryption.py` (Fernet-style token encryption),
  `account_credential_resolver.py`, `credential_builder.py`, `email_service.py`
  (password-reset mail), `schemas.py` (register/login/token/credential models).
- `/auth/*` endpoints live in `api/auth_router.py` (register, login, refresh, logout,
  me, credentials CRUD, forgot/reset password) with per-IP rate limiting.

Deeper dive: [authentication.md](./authentication.md) and
[ADR-001](../decisions/ADR-001-authentication-architecture.md).

---

## 12. Supporting modules

- `utils/` — `logger.py` / `enterprise_logger.py` and shared helpers (identifier
  sanitization, relationship naming `REL_<FROM>_<COL>__<TO>_<COL>`, etc.).
- `domain/` — the `SemaBridgeError` hierarchy (`NotFoundError`, `ValidationError`,
  `AuthenticationError`, `PermissionError`, `ConflictError`, `ExternalServiceError`,
  `RateLimitError`, …) that `api/error_handlers.py` maps to HTTP codes.
- `storage/` — blob/artifact storage helpers. `config/` — runtime config loading.

---

## 13. End-to-end worked example: Fabric → Snowflake

```
1. CLI/API calls run_sync(... source=fabric, target=snowflake)
        │
2. conversion_router.route("fabric","snowflake")
        → [fabric_extract, tmsl_to_osi, osi_to_sml, sml_to_snowflake]
        │
3. ExecutionEngine.execute() walks the 10 steps:
   ├ Step 3  Resolve auth        → auth/token_resolver → Fabric access token
   ├ Step 4  fabric_extract      → connectors/fabric_extractor → SourceFormat{ TMSL }
   ├ Step 5  validate source format
   ├ Step 6  tmsl_to_osi         → converter/tmsl_to_osi → OSIModel
   │         osi_to_sml          → converter/osi_to_sml  → SMLModel
   │                                 (DAX→SQL via dax_translator: tiers 1–4, then
   │                                  batched tier-5 LLM for the hard measures)
   ├ Step 7  persist artifacts   → repository: SourceArtifact + SnapshotRow(SML)
   ├ Step 8  sml_to_snowflake    → connectors/snowflake_emitter → semantic-view DDL
   │                                 + Cortex Analyst YAML
   ├ Step 9  deploy              → SnowflakeEmitter executes DDL in Snowflake
   └ Step 10 finalize            → RunSummary (per-step status, artifact ids, missing dims)
        │
4. repository records the Run + before/after snapshot ids; semantic_diff_engine can
   compare snapshots; CommandLogger logs the CLI invocation.
```

The reverse (`snowflake → fabric`) is symmetric: `semantic_view_to_osi` →
`osi_to_sml` → `tmsl_generator` → `fabric_publisher`. **Same OSI in the middle** —
that symmetry is the entire point of the architecture.

---

## 14. Glossary & quick map

| Term | Meaning |
|---|---|
| **OSI** | Open Semantic Intermediate — the canonical Pydantic model all conversions pass through (`intermediate/models.py`). |
| **SML** | Semantic Modeling Language — YAML-serializable, deployment-oriented mirror of OSI (`sml/`). |
| **TMSL** | Tabular Model Scripting Language — Fabric/Power BI `model.bim` JSON. |
| **Semantic view** | Snowflake's server-side semantic object; the Snowflake deploy target. |
| **Cortex Analyst YAML** | Snowflake NL-query metadata emitted alongside the semantic view. |
| **Device-code flow** | Interactive Microsoft OAuth used when no service principal secret is configured. |
| **Snapshot vs Version vs Run** | Snapshot = immutable SML state; Version = lineage record with parent/child + hash; Run = one execution of the engine. |
| **COPY vs UPSERT** | Sync modes — replace target vs merge (source wins, target-only kept). |

**Where do I look for X?**

| I want to… | Start here |
|---|---|
| Understand the canonical data model | `intermediate/models.py` |
| Add/inspect a conversion path | `core/conversion_router.py` |
| Trace one full run | `core/engine/engine.py` (+ `STEP_NAMES` in `core/run_summary.py`) |
| Add a new platform | `core/interfaces.py`, `connectors/factory.py`, `plugins/registry.py` |
| Translate DAX to SQL | `converter/dax_translator.py` |
| Deploy to Snowflake / Fabric | `connectors/snowflake_emitter.py` / `connectors/fabric_publisher.py` |
| Change what's stored | `repository/orm/*.py` + a migration in `migrations/versions/` |
| Add an HTTP endpoint | `api/controllers/*` + the matching `api/routers/*` + a `services/*` |
| Add a CLI command | `cli/*_commands.py` (+ `cli/main.py`) |
| Debug auth | `auth/middleware.py`, `auth/token_resolver.py`, `auth/fabric_validator.py` |

---

### Related docs

- [System Overview](./overview.md) · [OSI](./osi.md) · [Authentication](./authentication.md)
- [Configuration files](../development/configuration.md) · [Setup](../development/setup.md)
- [CLI usage](../usage/cli.md) · [REST API](../usage/rest-api.md) · [DAX translation](../features/dax-translation.md)
