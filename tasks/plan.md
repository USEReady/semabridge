# Plan: Align README with project

Goal: Update the repository README to accurately reflect current repository tooling, installation options, and developer workflow, then save the plan and task list for review.

1. Enter plan mode — read-only
   - Acceptance: Gathered README and spec files; no code changes.
   - Verification: `README.md` content captured and noted.

2. Identify component dependency graph
   - Acceptance: Short dependency map of core components (backend, frontend, repo tooling, migrations).
   - Verification: Map saved in this plan file.

3. Slice work vertically (one complete path per task)
   - Acceptance: Tasks cover discovery, draft, implementation, and verification.
   - Verification: Tasks enumerated in `tasks/todo.md`.

4. Draft README alignment changes
   - Acceptance: Draft includes clear install options, dev commands, and troubleshooting notes.
   - Verification: Draft saved as updated `README.md` in the repo.

5. Implement README updates
   - Acceptance: `README.md` updated in-place; minimal, backward-compatible wording.
   - Verification: Repo `README.md` changed and reviewed.

6. Save plan and todo files
   - Acceptance: `tasks/plan.md` and `tasks/todo.md` exist in the repo.
   - Verification: Files present and linkable from repo root.

7. Review & finalize
   - Acceptance: Tasks marked complete and a final note added to the plan.
   - Verification: `tasks/todo.md` statuses updated and plan annotated.

Checkpoints:
- After step 2: pause for human review of dependency map.
- After step 4: present README draft for review before committing.

Notes:
- Keep changes minimal and non-breaking; prefer clarifying wording over restructuring.
- If you want the README to be more prescriptive (e.g., recommend Poetry or uv), review and approve that preference before edit.
