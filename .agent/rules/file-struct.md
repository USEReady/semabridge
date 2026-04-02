---
trigger: always_on
---

# Semabridge File Structure Guide

## Canonical Hierarchy

semabridge/
- src/semabridge/
	- core/
	- connectors/
	- formats/
	- converter/
	- intermediate/
	- repository/
	- cli/
	- api/
	- plugins/
	- utils/
- tests/
- docs/
- examples/
- scripts/
- config/

## Placement Rules
- Keep root minimal.
- Do not add one-off source or utility files in root.
- Put reusable scripts in scripts/.
- Put durable documentation in docs/.
- Put runtime/app configuration in config/ unless it is a standard root-level project file.
- Keep tests only in tests/.
- Avoid generating files outside approved directories unless explicitly requested.

## Docs Structure Rules
- docs/README.md should act as documentation index.
- Prefer docs/architecture, docs/development, docs/usage, docs/features categories.
- Consolidate roadmap and milestone tracking in docs/RoadMap.md.
- Internal agent scratch notes should not be stored in docs/.

## Examples and Scripts Rules
- examples/ is for configuration examples, not executable runtime code.
- scripts/ is for reusable build/run/developer utilities.
- Remove one-off cleanup/debug scripts rather than retaining them long-term.

## Safety Rules for Refactors
- Do not modify, move, or delete source, UI, tests, or configuration folders unless explicitly requested.
- Restrict instruction consolidation tasks to instruction/policy paths only.