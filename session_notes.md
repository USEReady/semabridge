# Session Notes

## Date
- 2026-04-02

## Summary
- Added workspace-level AI agent policy file to enforce Semabridge repository practices.
- Avoided product code modifications per user request.

## Changes Made
- Created `.github/copilot-instructions.md` with mandatory rules for:
  - naming conventions
  - root/docs/examples/scripts/src/tests hygiene
  - merge hygiene
  - security/secret handling
  - Semabridge architecture principles
  - instruction-only behavior (no code changes for policy requests)
- Created `session_notes.md` to satisfy ongoing session documentation requirement.

## Impact
- Future agent actions in this workspace are guided by shared policy.
- Structural consistency expectations are now codified for subsequent tasks.

## Follow-ups
- Keep this file updated after each significant task in current chat.
- Optionally migrate session notes into long-term docs if requested.
