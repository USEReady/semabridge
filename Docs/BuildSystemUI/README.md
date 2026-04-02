# BuildSystemUI

## Purpose
This section documents the UI build and runtime standards for the Semabridge frontend experience.

## Alignment With Engineering Standards
- Use plugin-first, maintainable architecture decisions.
- Keep UI behavior consistent with OSI-first data flow principles.
- Favor typed contracts and explicit interfaces across UI and API boundaries.

## Scope
- UI build tooling and bundling strategy.
- Local development workflows.
- Release packaging notes for the frontend.
- UI quality gates tied to repository standards.

## Current Frontend Structure
- Root folder: frontend/
- Toolchain: Vite + npm package scripts
- Primary artifacts: source code in src/, static assets in public/

## Developer Workflow
1. Install dependencies:
   - uv run npm --prefix frontend install
2. Start development server:
   - uv run npm --prefix frontend run dev
3. Run linting:
   - uv run npm --prefix frontend run lint
4. Build production bundle:
   - uv run npm --prefix frontend run build

## Quality and Safety Rules
- No secrets in frontend source or committed environment files.
- Keep commands and docs synchronized with actual scripts.
- Prefer reusable components and shared context providers.
- Avoid direct coupling to backend internals; use API contracts.

## Change Control Checklist
- Verify dev/build/lint scripts run successfully.
- Validate navigation and data-loading paths still work.
- Update Docs indexes when adding major UI subsystems.
