---
inclusion: manual
---

# Spec-Driven Development

Write a structured specification before writing any code. The spec is the shared source of truth — it defines what we're building, why, and how we'll know it's done.

## When to Use
- Starting a new project or feature
- Requirements are ambiguous or incomplete
- The change touches multiple files or modules
- Architectural decisions need to be made

## The Gated Workflow

```
SPECIFY ──→ PLAN ──→ TASKS ──→ IMPLEMENT
```
Do not advance to the next phase until the current one is validated by the human.

## Phase 1: Specify

Surface assumptions immediately before writing any spec content:

```
ASSUMPTIONS I'M MAKING:
1. [assumption]
2. [assumption]
→ Correct me now or I'll proceed with these.
```

Write a spec covering:
1. **Objective** — What are we building and why?
2. **Commands** — Full executable commands (build, test, lint, dev)
3. **Project Structure** — Where source code, tests, and docs live
4. **Code Style** — One real code snippet beats three paragraphs
5. **Testing Strategy** — Framework, locations, coverage expectations
6. **Boundaries** — Always do / Ask first / Never do

## Phase 2: Plan
Generate a technical implementation plan with components, dependencies, and implementation order.

## Phase 3: Tasks
Break the plan into discrete tasks. Each task must have:
- Acceptance criteria (specific, testable)
- Verification step (test command, build, manual check)
- Files likely touched

## Phase 4: Implement
Execute tasks one at a time. Update the spec when decisions change.

## Spec Template

```markdown
# Spec: [Feature Name]

## Objective
[What we're building and why]

## Tech Stack
[Framework, language, key dependencies]

## Commands
Build: 
Test: 
Lint: 

## Project Structure
[Directory layout]

## Code Style
[Example snippet]

## Testing Strategy
[Framework, test locations, coverage]

## Boundaries
- Always: [...]
- Ask first: [...]
- Never: [...]

## Success Criteria
[Specific, testable conditions]

## Open Questions
[Anything needing human input]
```
