# CI/CD Workflow

Semabridge uses GitHub Actions for CI/CD automation.

## Pipeline Stages
1. Preflight: path-based change detection
2. Backend quality: ruff + pytest
3. Frontend quality: eslint + vite build
4. Security checks: pip-audit + npm audit
5. Database migrations: post-merge workflow

## Required Gates
- Backend lint
- Backend fast tests
- Frontend lint
- Frontend build
- Security checks

## Database Migration Workflow
- Triggered on push to main or develop
- Runs Alembic upgrade
- Requires STAGING_DATABASE_URL and PRODUCTION_DATABASE_URL secrets

Workflow file: .github/workflows/apply-migrations.yml
