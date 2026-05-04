# Semabridge CI/CD Workflow

## Assumptions

1. This repository uses GitHub Actions for CI/CD automation.
2. Production deployment infrastructure is managed outside this repository.
3. Current frontend lint issues and script-style integration tests are known blockers; pipeline must still provide clear fast feedback.

## Pipeline Stages

1. Preflight
- Detect changed backend and frontend paths.
- Skip irrelevant jobs for fast feedback.

2. Backend Quality Gates
- Install backend dependencies in editable mode.
- Run static lint: ruff check src/semabridge.
- Run fast backend verification: pytest Tests/test_*.py Tests/Core.

3. Frontend Quality Gates
- Install dependencies with npm ci from the committed lockfile.
- Run frontend lint and production build.

4. Security Checks
- Run Python dependency audit with pip-audit.
- Run npm audit for frontend at high severity threshold.

5. Database Migrations (Post-Merge)
- Automatically runs on push to `main` or `develop` branches
- Validates database connection to staging and production
- Applies Alembic migrations to keep schema in sync with code
- Requires `STAGING_DATABASE_URL` and `PRODUCTION_DATABASE_URL` secrets in GitHub
- Can be manually triggered via `workflow_dispatch` input for environment selection
- Workflow: `.github/workflows/apply-migrations.yml`

6. Extended Integration (manual)
- Optional workflow_dispatch input enables an extended integration check.
- Keeps PR feedback fast while allowing deeper verification before release.

7. Summary and Recovery Guidance
- Publish pass/fail status for each stage.
- Provide direct recovery instructions in workflow summary.

## Quality Gates

Required gates on PR and push to main:
- Backend lint
- Backend fast tests
- Frontend lint
- Frontend build
- Security checks

Optional gate:
- Extended integration checks via manual dispatch

## Automation Steps

1. Trigger on pull_request to main, push to main, and workflow_dispatch.
2. Use concurrency cancellation to avoid stale runs.
3. Use dependency caching for pip and npm.
4. Upload diagnostics artifacts on failures.
5. Emit actionable workflow summary for engineers and agents.

## Repeatability and Fast Feedback

Repeatability controls:
- Pinned runtime versions in CI: Python 3.11 and Node 20.
- Cache keyed by dependency manifests.
- Enforce lockfile-based installs (npm ci with frontend/package-lock.json).

Fast feedback controls:
- Path-filtered job execution.
- Lightweight test scope on PR critical path.
- Optional extended checks only when requested.

## Failure Handling and Recovery Strategy

Failure handling in pipeline:
- Job failures immediately fail the pipeline stage.
- Failure artifacts are uploaded for local reproduction.
- Summary step always runs and shows failed stage names.

Operational recovery steps:
1. Identify failing stage from CI summary.
2. Download artifacts and reproduce with the same command locally.
3. Fix and push; old runs are auto-canceled by concurrency.
4. If a bad change reaches main, revert offending commit first, then follow up with a forward fix.
5. If security audit fails due ecosystem advisory noise, triage and document temporary allow-list entries with expiry date.

## Known Gaps and Next Actions

1. Refactor script-style integration tests in Tests/Integration to avoid import-time sys.exit behavior.
2. Add protected environments for staging and production deployment approvals when deployment scripts are available.
3. Add branch protection rules so CI status checks are required before merge.
