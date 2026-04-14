# Spec: Connector-Aware Dry Run and Manual Mapping Override

## Objective
Build a reusable mapping workflow that starts with auto-detection and transitions to manual override through an explicit dry-run gate. The workflow must give users safe control over target-side naming constraints before persistence, especially for strict targets such as Snowflake.

Primary users:
1. Data engineers configuring source-to-target semantic mappings.
2. Analysts validating mapping quality before transformation and sync.

Success looks like:
1. Users can switch from auto mode to manual mode without losing clarity about state.
2. Dry run produces actionable validation output (valid rows, collisions, invalid rows, and suggestions).
3. Users can resolve issues inline with real-time feedback and persist only resolved mappings.

## Scope
In scope:
1. Mapping Options step in create-project flow.
2. Backend validator abstraction by connector type.
3. Dry-run API mode and response model.
4. Collision-focused editing UX with instant re-validation.
5. Persistence behavior for approved mappings only.

Out of scope:
1. Full redesign of non-mapping project steps.
2. Connector onboarding beyond validator interface and Snowflake implementation.
3. Bulk migration of legacy mapping records.

## Assumptions
1. Existing mapping endpoints and compatibility store behavior must stay backward compatible.
2. Backend remains source of truth for validation decisions.
3. Frontend can perform mirror validation for immediate UX feedback.
4. Snowflake identifier policy includes strict reserved keyword handling and sanitization.

## Constraints
1. No breaking changes to current API consumers of mappings endpoints.
2. Existing auto-map flow must continue to work when dry-run mode is not requested.
3. Mapping state transitions must be deterministic and restart-safe.
4. Validation metadata must be serializable and persisted only when user confirms.

## Tech Stack
Backend:
1. Python 3.11+
2. FastAPI
3. SQLAlchemy

Frontend:
1. React
2. Vite

## Commands
Backend setup and checks:
1. uv sync
2. uv run pytest -v --tb=short

Backend app run:
1. uv run uvicorn semabridge.api.main:app --host 127.0.0.1 --port 8001

Frontend run and checks:
1. npm run dev --prefix frontend
2. npm run build --prefix frontend
3. npm run lint --prefix frontend

## Project Structure
Relevant backend paths:
1. src/semabridge/api/services/project_runs_impl.py
2. src/semabridge/api/services/project_mapping_engine.py
3. src/semabridge/api/controllers/mappings_controller.py

Relevant frontend paths:
1. frontend/src/pages/CreateProjectPage.jsx
2. frontend/src/utils/api.js

Spec and docs:
1. Docs/Development/SPEC_Mapping_Dry_Run_Manual_Override.md

## Code Style
Conventions:
1. Keep request handlers thin and move business logic to service modules.
2. Use explicit response payload keys and stable shapes.
3. Prefer small, pure helper functions for validation and sanitization.
4. In frontend, keep mapping-phase state explicit and avoid implicit boolean coupling.

Example style:

```ts
const phase = useMemo(() => {
  if (autoMode) return 'auto-detect';
  if (dryRunLoading) return 'dry-run-executing';
  if (!hasDryRunResult) return 'manual-override-prompt';
  return 'resolution';
}, [autoMode, dryRunLoading, hasDryRunResult]);
```

## Functional Requirements

### FR-1: Mapping phase state machine
1. Mapping Options exposes four states:
   1. auto-detect
   2. manual-override-prompt
   3. dry-run-executing
   4. resolution
2. Transitioning auto toggle from on to off clears transient auto-detected state used only for display.
3. Manual editing is disabled before a successful dry run.

### FR-2: Dry run API mode
1. Existing auto-map endpoint accepts dry-run mode input.
2. Dry run returns grouped and entity-level mappings with validation metadata.
3. Response includes suggested target identifiers for invalid or colliding names.

Required validation metadata per mapping row:
1. validation_status: valid | collision | invalid
2. validation_code: stable code for UI messaging
3. validation_message: human-readable explanation
4. suggested_target_name: non-empty suggestion when status is collision or invalid

### FR-3: Connector validator abstraction
1. Introduce a backend validator interface per target connector.
2. Snowflake validator implementation enforces:
   1. reserved keyword restrictions
   2. unsupported character normalization
   3. leading digit handling
   4. deterministic collision suffix behavior
3. Validator registry resolves active target validator from connector configuration.

### FR-4: Collision-first resolution UX
1. Resolution table shows segmentation and filters:
   1. only collisions
   2. only edited
2. Collisions and invalid entries are visually emphasized and sorted near top by default.
3. Editable target fields are prefilled with suggested_target_name.

### FR-5: Real-time re-validation
1. As user edits target name, frontend performs local re-validation to provide immediate feedback.
2. Backend validation re-runs before persistence to prevent drift.
3. Continue action is blocked when blocking validation errors remain.

### FR-6: Persistence and compatibility
1. Persist only after explicit user action.
2. Existing mapping update and list endpoints remain backward compatible.
3. Saved manual edits round-trip correctly when reloading project data.

## Non-Goals
1. Implementing all target connectors immediately.
2. Replacing current mapping storage mechanism.
3. Adding fuzzy semantic reconciliation across unrelated models.
4. Introducing AI-generated mapping rules.

## Risks and Mitigations
1. Risk: Rule drift between frontend and backend validation.
   Mitigation: Backend is final authority, frontend displays optimistic feedback and reconciles on save.
2. Risk: Performance degradation for large mapping sets.
   Mitigation: Debounce local validation and batch backend validation.
3. Risk: Backward compatibility break in API response shape.
   Mitigation: Additive response keys only, no removal of existing keys.
4. Risk: User confusion around auto-clear behavior when toggling modes.
   Mitigation: Prompt with explicit explanation and recoverable rerun action.

## Testing Strategy
Backend:
1. Unit tests for validator interface and Snowflake validator edge cases.
2. Service tests for dry-run mode payload/response.
3. Compatibility tests confirming legacy auto-map calls still function.

Frontend:
1. Component tests for phase transitions and disabled/enabled controls.
2. Tests for filter behavior (only collisions and only edited).
3. Tests for inline edit real-time validation and save blocking.

Manual verification:
1. Toggle auto on and off and verify required dry-run gate appears.
2. Execute dry run and verify categorized output and suggestions.
3. Resolve collisions and confirm continue/save availability only when valid.

## Boundaries
Always do:
1. Keep validation logic connector-driven and reusable.
2. Keep response contracts backward compatible.
3. Validate before persist.

Ask first:
1. Schema changes to mapping persistence tables.
2. Any dependency additions in frontend or backend.
3. Any change to existing endpoint paths.

Never do:
1. Persist mappings automatically at dry-run time.
2. Hardcode Snowflake rules directly in UI components.
3. Store secrets or credentials in mapping payloads.

## Acceptance Criteria
1. User cannot perform manual mapping edits until a dry run has executed after auto mode is disabled.
2. Dry run output categorizes rows into valid, collision, and invalid with clear reasons.
3. Every collision or invalid row has a deterministic suggested target name.
4. Inline edits update validation feedback in near real-time.
5. Save/continue is blocked if unresolved blocking validation issues remain.
6. Existing non-dry-run mapping workflows continue to work unchanged.
7. Reloading project data preserves saved manual mapping updates.

## Handoff to Planning
This spec is implementation-ready for the planning workflow and should be decomposed into:
1. Backend validator and API tasks.
2. Frontend state machine and UX tasks.
3. Testing and regression tasks.

## Open Questions
1. Should unresolved collisions block all project continuation, or allow warning-based progression for non-critical targets?
2. Should dry run be mandatory for every connector or only strict connectors by policy?
3. Should suggested names auto-apply immediately or wait for user acceptance per row?
