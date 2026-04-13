# Semabridge Launch Workflow

## Assumptions

1. Release target is the backend API in [src/semabridge/api/main.py](src/semabridge/api/main.py) and the React frontend in [frontend](frontend).
2. Deployment is controlled by the platform team (no fully automated CI workflow is currently present in the repository).
3. Feature flags are not yet standardized in code, so rollout will use environment-gated access and staged traffic exposure at the deployment layer.
4. Health validation will use the existing endpoint in [src/semabridge/api/controllers/health_controller.py](src/semabridge/api/controllers/health_controller.py).

## 1) Pre-Launch Release Checklist

### Code Quality Gates

- [ ] Backend tests pass: run pytest from repo root.
- [ ] Frontend build passes: npm run build in [frontend](frontend).
- [ ] Frontend lint passes: npm run lint in [frontend](frontend).
- [ ] No blocking debug code in API and frontend routes.
- [ ] Release notes prepared with user-visible behavior changes.

### Security Gates

- [ ] Confirm no credentials committed in tracked files.
- [ ] Validate auth middleware bypass paths are restricted to intended endpoints only (health/docs/openapi).
- [ ] Verify production CORS allow-list is explicit.
- [ ] Confirm runtime secrets are provided from environment only.

### Performance and Reliability Gates

- [ ] API startup succeeds with production config.
- [ ] Health endpoint returns status ok or accepted degraded policy.
- [ ] Critical API flows complete within baseline latency budget.
- [ ] Database connectivity is stable under expected concurrent requests.

### Infrastructure and Configuration Gates

- [ ] Production environment variables validated.
- [ ] Target database path/URL is environment-local and lock-safe.
- [ ] Logging sink is configured and queryable.
- [ ] On-call owner assigned for launch window.

### Documentation and Operations Gates

- [ ] Deployment command sequence written and peer-reviewed.
- [ ] Rollback runbook copied to ticket and release channel.
- [ ] Stakeholders notified of rollout windows and success criteria.

## 2) Rollout Strategy (Staged)

### Stage A: Pre-Prod Validation

- Deploy release candidate to staging.
- Run smoke checks:
  - GET /api/health
  - Authentication flow
  - One end-to-end semantic sync flow (representative connector pair)
- Hold for at least 30 minutes and review logs/errors.

### Stage B: Production Deploy With Guardrails

- Deploy to production with user-facing access constrained (internal users/admin allow-list).
- Observe for 60 minutes.
- Exit criteria:
  - Error rate within 10% of pre-launch baseline.
  - P95 latency within 20% of baseline.
  - No new Sev1 or Sev2 incidents.

### Stage C: Canary Rollout

- Expand exposure to 5% tenant/project slice.
- Observe for 24 hours.
- Advance criteria:
  - Error rate <= 1.5x baseline
  - P95 latency <= 1.3x baseline
  - No data integrity regressions

### Stage D: Gradual Expansion

- Increase in steps: 25% -> 50% -> 100%.
- Minimum 24 hours at each step.
- Pause at any step if validation criteria fail.

## 3) Rollback Strategy (Runbook)

### Rollback Triggers

- Error rate > 2x baseline for 10 minutes.
- P95 latency > 50% above baseline for 15 minutes.
- Reproducible data corruption or incorrect semantic output.
- Authentication or authorization regression.

### Fast Rollback Path

1. Restrict traffic immediately (reapply internal-only gate or remove route exposure).
2. Re-deploy prior known-good backend and frontend artifact versions.
3. Restart service processes and verify [health check](src/semabridge/api/controllers/health_controller.py#L6).
4. Re-run smoke tests on critical flows.
5. Announce rollback and incident state in release channel.

### Data and State Considerations

- Preserve current state DB snapshot before rollback if migration/state-shape changed.
- If rollback includes schema changes, execute documented down migration path before reopening traffic.
- Keep generated output artifacts for forensic comparison.

### Rollback RTO Targets

- Traffic gate rollback: under 5 minutes.
- Full artifact rollback: under 15 minutes.
- Service stabilization plus validation: under 30 minutes.

## 4) Monitoring and Alerting

### Metrics to Monitor

### API and Service Health

- Request rate, error rate, status code mix (2xx/4xx/5xx)
- Latency: P50, P95, P99
- Health endpoint result and frequency of degraded status

### Infrastructure

- CPU and memory utilization
- DB connection pool pressure and timeout signals
- Process restarts and crash loops

### Domain/Business Signals

- Sync job success/failure counts
- Mean sync duration
- Connector validation success rate

### Alert Thresholds

- Critical alert:
  - 5xx rate > 2% for 10 minutes
  - Health endpoint unavailable for 5 minutes
  - Auth failures spike > 3x baseline for 15 minutes
- Warning alert:
  - P95 latency > 30% baseline for 15 minutes
  - Sync failure rate > 10% for 30 minutes
  - DB pool saturation warning detected

### Validation Criteria During Launch

- Functional:
  - Health endpoint returns expected payload fields: status, service, database.
  - Create/list/sync critical flows succeed end to end.
- Operational:
  - Logs are ingested and searchable in under 2 minutes.
  - Alerting pipeline is verified with at least one synthetic warning.
- Business:
  - No significant drop in successful sync completions versus baseline.

## 5) Launch Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Environment drift between staging and production | High | Medium | Freeze config delta, run preflight config diff, require sign-off before deploy |
| DuckDB/database lock contention on Windows hosts | High | Medium | Ensure single active writer process, pre-launch process sweep, workspace-local DB path |
| Auth dependency mismatch or optional auth wiring regression | High | Medium | Validate startup path with and without optional auth deps in staging before production |
| External connector API throttling/outage (Fabric/Snowflake) | Medium | High | Add retry budget, graceful error banners, rollback gate based on connector failure rate |
| Frontend build/runtime mismatch | Medium | Medium | Enforce frontend build + lint gate, smoke test core routes post-deploy |
| Undetected performance regression under real traffic | High | Medium | Canary rollout with strict latency/error thresholds, halt progression on breach |
| Incomplete observability leading to slow incident response | High | Medium | Verify dashboards and alert routes before rollout, run synthetic alert test |

## 6) Release-Day Command Checklist (Operator Ready)

1. Backend quality gate:
   - pytest
2. Frontend quality gate:
   - cd frontend; npm run lint; npm run build
3. Start API (example):
   - uvicorn semabridge.api.main:app --host 0.0.0.0 --port 8001
4. Validate health:
   - GET /api/health
5. Execute staged rollout and hold points exactly as above.
6. Keep rollback artifact/version identifiers pre-selected before Stage B.

## 7) Launch Decision Record

Record these fields in the release ticket:

- Release version:
- Date/time (UTC):
- Launch owner:
- Canary start/end:
- Alert incidents observed:
- Rollback invoked (yes/no):
- Final outcome:
- Follow-up actions:

## 8) Readiness Snapshot (2026-04-13)

### Executed Gates

- Backend tests (full): failed during collection because integration tests call sys.exit when Gemini key is missing.
  - Blocking files:
    - Tests/Integration/test_gemini_integration.py
    - Tests/Integration/test_gemini_models.py
    - Tests/Integration/test_markdown_fix.py
- Frontend lint: failed with parser, undefined symbol, and hook/compiler rule errors.
  - Primary blockers:
    - frontend/src/components/WorkspaceSelector.jsx (Parsing error: return outside of function)
    - frontend/src/components/common/SearchableSelect.jsx (react-hooks/preserve-manual-memoization + no-unused-vars)
    - frontend/src/context/LogsContext.jsx (variable used before declaration)
- Frontend build: passed.
  - Bundle warning observed: large chunk size (>750 kB).

### Launch Status

- Status: NOT READY for production launch.
- Required to proceed:
  - Remove test-suite collection exits and make env-gated tests skip instead of sys.exit.
  - Fix frontend lint/parser/runtime-safety errors and rerun lint.
  - Re-run full backend and frontend quality gates, then update this snapshot.
