# Semabridge TMSL Removal Spec

## Objective
Remove TMSL from the Semabridge product surface and runtime paths so TMDL is the only supported semantic model format.

Assumptions:
- Primary users are developers and internal maintainers.
- Backward compatibility with existing TMSL artifacts can break.
- The goal is a hard cutover, not a staged migration.

Success criteria:
- No user-visible TMSL support remains.
- No active runtime path accepts or emits TMSL for the Semabridge semantic-model flow.
- Docs, tests, config defaults, and package exports reflect TMDL-only behavior.

## Commands
Use the workspace Python venv for validation.

Required validation commands after each slice:
- `& ".venv/Scripts/python.exe" -m pytest Tests/test_metadata_connectors.py Tests/test_tmdl_connector_edgecases.py -q`
- `& ".venv/Scripts/python.exe" -m pytest <relevant touched tests> -q`
- `get_errors` on every edited Python/JS file before and after changes

If frontend files are changed:
- Run the narrowest frontend build/lint check available in the repo

## Project Structure
Touch the smallest set of files needed for the cutover:
- `src/semabridge/connectors/` for connector exports and model generation paths
- `src/semabridge/converter/` for model conversion paths
- `src/semabridge/core/` for execution, persistence, and source-format defaults
- `src/semabridge/api/routers/` for comparator format detection and docs-facing behavior
- `frontend/src/pages/` and `Docs/features/` for user-facing TSML references
- `Tests/` for removing obsolete TSML/TMSL compatibility coverage and replacing it with TMDL-only coverage

Keep changes slice-by-slice:
1. Remove public TSML/TMSL support surfaces and exports.
2. Remove runtime TSML ingestion paths.
3. Remove TMSL emission/publishing paths.
4. Update persistence defaults and migration values to TMDL.
5. Remove obsolete tests and docs references.

## Code Style
- Preserve existing naming, formatting, and module boundaries unless a rename is required to remove legacy concepts.
- Prefer small, reversible edits over broad refactors.
- Do not introduce new dependencies unless a slice cannot be completed without one.
- Keep compatibility-breaking changes explicit in names, defaults, and tests.
- Avoid touching unrelated code while removing TMSL surfaces.

## Testing Strategy
- Validate each slice immediately after implementation.
- Start with focused unit tests around the touched surface.
- Use `get_errors` for static validation on edited files.
- Prefer narrow pytest runs over the full suite until the final cutover slice.
- After the final slice, run the broadest relevant tests for the changed area.

Minimum test expectations:
- Connector tests pass after TMDL-only behavior is enforced.
- Comparator parsing tests no longer expect TSML.
- No edited file reports syntax or import errors.

## Boundaries
Always do:
- Keep the TMDL cutover internally consistent across code, docs, tests, and defaults.
- Validate every slice before moving on.
- Treat any TMSL reference as suspect unless it is clearly a separate legacy module outside the active semantic-model path.

Ask first about:
- Deleting modules that still serve unrelated Fabric/TMSL publishing paths.
- Any change that affects database migrations or persisted snapshot data.
- Renaming public APIs or package exports used outside the current workspace.

Never do:
- Reintroduce backward compatibility for TMSL.
- Leave runtime code that silently accepts both TMSL and TMDL.
- Keep TSML/TMSL labels in user-facing docs or UI after the cutover.
- Mix unrelated cleanup into the same slice.

Open question:
- Should legacy TMSL helper modules remain as isolated internal implementation details for Fabric publishing, or should they be removed in the next spec slice as well?