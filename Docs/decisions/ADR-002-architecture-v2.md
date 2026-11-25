# ADR-002: Architecture v2 (shared state, durable jobs, plan once, deployment profiles)

- **Status:** Accepted, implemented on `feature/architecture-v2`
- **Date:** 2026-11-25

## Context

A review of the backend against the six Well-Architected pillars found that
the app was built for one process on one machine:

- Project state lived in three places: PostgreSQL, a JSON file
  (`.semabridge_compat_store.json`) and per-process dicts. A second server
  process or a restart lost or diverged mappings, schedules and run details.
- A sync ran in a daemon thread started by the request. A crash lost it.
- Every single-model sync extracted and converted the source twice (once for
  the readiness dry run, once for the sync), and the readiness gate ignored a
  failed dry run.
- SQL guard allowlists were process-global sets that only grew, so concurrent
  runs widened each other's allowlists.
- Several authorization gaps (synonym routes, link-account) and a
  password-reset endpoint that returned a working reset link to anyone when
  SMTP was not configured.
- No container image or infrastructure template; the app could not be
  handed to a client.

## Decisions

### 1. PostgreSQL is the single source of truth (`state_documents`)
`repository/state_store.py` + `repository/orm/state_models.py`, migration `sb0006`.

- One row per entity `(collection, key)` with a content digest and a tombstone flag.
- `project_shared._compat_save_store` writes only rows whose digest changed and
  tombstones entities this process deleted. `_compat_ensure_loaded` pulls rows
  other processes changed (throttled, `SEMABRIDGE_STATE_REFRESH_SECONDS`, default 2).
- Run detail blobs (summary, results, logs) get their own `run_details` rows and
  are filled in when runs are listed, so any process shows them.
- The in-memory dicts remain as a cache: callers did not change.
- An existing JSON store is imported once into an empty table. The JSON file is
  a local mirror only (off on hosted profiles). `SEMABRIDGE_STATE_BACKEND=file`
  restores the old behaviour.
- Conflict rule: last writer wins **per entity** (was: per whole file).

### 2. Durable job queue (`core/job_queue.py`, `run_jobs`, migration `sb0007`)

- `SEMABRIDGE_EXECUTION_MODE=queue` (default on hosted profiles): a sync is a
  `run_jobs` row. Workers claim rows atomically (`FOR UPDATE SKIP LOCKED` on
  PostgreSQL, compare-and-swap elsewhere), heartbeat while executing, and a
  job whose worker stopped is re-queued (max 2 attempts) by any process's
  maintenance loop. A resumed run re-takes its project lease or is skipped.
- Workers run embedded in the API (`SEMABRIDGE_EMBEDDED_WORKERS`) or as their
  own process (`semabridge-worker`).
- `inline` mode keeps the old thread-per-run behaviour for local development.

### 3. Plan once, honest readiness gate (`run_service._assert_sync_ready`)

- Auto-map reports the dry run's engine status (`dry_run_status`,
  `dry_run_errors`); the gate blocks with `DRY_RUN_FAILED` when it failed.
- In gate mode auto-map never falls back to stale stored mappings.
- A clean readiness result is stored against the fingerprint of source content
  + project config + `behavior.yaml` and reused for identical content
  (`SEMABRIDGE_READINESS_CACHE_HOURS`, default 24). No fingerprint, no reuse.
- Scheduled runs with `skip_unchanged_sources` are skipped before the gate.

### 4. Deployment profiles and platform adapters

`core/deployment_profile.py`: `local | azure | snowflake_native | databricks_app`.
Hosted profiles turn on:

- strict startup: the app refuses to start if a schema step failed or auth is
  disabled (`api/bootstrap/startup_state.py`; readiness reports failed steps);
- queue execution, no JSON state mirror, no password-reset links in responses or logs.

`platform/identity.py`: the hosting platform's front door authenticates the
user (Entra ID headers on Azure, `Sf-Context-Current-User` on Snowpark
Container Services, `X-Forwarded-Email` on Databricks Apps).
`POST /auth/platform-login` exchanges that identity for a normal SemaBridge
session. Headers are trusted only with `SEMABRIDGE_PLATFORM_AUTH=true`.

Azure packaging: `Dockerfile` (web app + API + worker in one image),
`deploy/azure/main.bicep`, `deploy/azure/README.md`.

### 5. Targets behind one port (`core/targets/adapter.py`)
Step 9 dispatches through a `TargetAdapter` registry. An unknown target now
fails; before, it fell through and recorded "Deployment complete".

### 6. Security fixes
- Run-scoped SQL allowlists (`ContextVar`).
- Ownership checks on synonym routes and link-account.
- `/health/*` diagnostics require sign-in.
- Reset links are never exposed on hosted profiles.

### 7. Databricks
- Metric view comments carry business meaning: the
  `Semabridge: <model> - <view>` marker (ownership checks read it) plus the
  ontology/BI description.
- Rejected measures and stub state are run warnings, not comment text.
- Structure-only (stubbed) views and partially failed deploys are run
  warnings with counts.
- No silent binding to a same-named table in another schema.
- Source-table resolution moved to `connectors/databricks/source_resolution.py`
  (first step of splitting the publisher).
- Golden snapshots: `Tests/characterization/test_databricks_snapshots.py`.

## Consequences

- Multi-process and multi-replica deployments are supported (Azure runs 2 API
  processes per replica plus a worker app).
- Every state save costs one small DB transaction of changed rows; reads stay
  in memory.
- A Databricks change to metric view output must update the golden snapshots
  deliberately (`SEMABRIDGE_UPDATE_GOLDEN=1`).

### 8. Follow-up work (same branch)
- **Run-scoped LLM credentials** (`core/run_secrets.py`): every LLM key lookup
  prefers the run's key; different users' syncs no longer wait on a
  process-wide `os.environ` gate. Saving / testing a connection with sign-in
  on no longer leaves a user's credentials in the shared environment.
- **Cancel** a queued or running sync (`POST /api/projects/{id}/runs/{run_id}/cancel`,
  `core/run_cancel.py`): checked at every engine step; the run ends "cancelled".
- **PostgreSQL CI lane** (`.github/workflows/tests.yml` job `postgres`,
  `Scripts/check_migrations.py`).
- **Splits:** `dax_ast_parser.py` 8.7k → 2.1k lines (`converter/dax/sql_renderer.py`,
  `converter/dax/feasibility.py`, re-exported); `databricks_publisher.py`
  9.6k → 8.8k (`connectors/databricks/source_resolution.py`, `view_repair.py`).
- **Snowflake Native App / Databricks App packaging** (`deploy/snowflake-native`,
  `deploy/databricks-app`) as templates.

## Not done yet

| Item | Why it is open |
|---|---|
| Unity Catalog tags as the ownership marker | Changes which views SemaBridge may replace; must be tested against a live workspace. |
| Validate the Snowflake Native App and Databricks App templates | Needs a Snowflake provider/consumer account and a Databricks workspace. |
| Further publisher split (YAML generation, deploy/staging) | Mechanical, same mixin pattern; golden snapshots guard it. |
| Snowflake session-token target credentials inside the Native App | So consumers do not enter warehouse credentials. |
