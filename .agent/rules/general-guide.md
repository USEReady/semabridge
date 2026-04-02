---
trigger: always_on
---

# Semabridge General Guide

## Project Goals
- Build production-grade, extensible, and type-safe semantic model tooling.
- Keep maintainability and modularity above short-term convenience.
- Preserve deterministic behavior and reproducible outputs.

## Non-Negotiable Architecture
- Intermediate model first: all transformations must follow Source -> OSI -> Target.
- Never implement direct Source -> Target conversion.
- Keep core system-agnostic. Connector-specific behavior belongs in connectors/formats/mapping plugins.
- Enforce plugin-first extensibility. New connectors/formats/rule packs should be addable at registration boundaries without changing orchestrator behavior.
- Maintain strict separation of concerns:
  - CLI orchestrates and does not transform data.
  - Connectors connect and do not implement business transformation logic.
  - Converter transforms and does not persist.

## Runtime and Platform Baseline
- Runtime: Python 3.10+.
- API: FastAPI + Uvicorn.
- CLI: Typer + Rich.
- Validation/config: Pydantic v2 + pydantic-settings + YAML.
- Auth: MSAL.
- Database: DuckDB only for repository persistence.

## Data, State, and Persistence Rules
- Use DuckDB APIs and abstractions for database operations.
- Never use SQLite APIs for Semabridge persistence.
- Use repository abstractions under src/semabridge/repository for DB access patterns.
- Run artifacts are immutable once a run_id is closed.
- Run status must be explicit: SUCCESS, FAILED, or PARTIAL.

## Security and Secrets
- No inline secrets in code, config artifacts, or logs.
- Secret-bearing settings should reference environment variable names.
- Resolve secret values from environment at runtime.
- Apply log redaction for credential-like values.

## Reliability and Failure Behavior
- Fail fast on invalid config, missing env vars, unsupported connector types, and interface violations.
- Use structured errors and include actionable context.
- Handle external API throttling and retries in connector-specific code.

## Quality and Testing Goals
- Keep tests under tests/ and mock external systems in unit/integration tests where appropriate.
- Target >= 80% coverage for new functionality.
- Do not consider work complete if it introduces test regressions.

## Agent Behavior
- For policy/instruction-only requests, modify only instruction/policy files.
- Prefer minimal, targeted changes and avoid unrelated edits.
