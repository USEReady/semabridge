---
inclusion: manual
---

# Planning and Task Breakdown

Decompose work into small, verifiable tasks with explicit acceptance criteria before writing any code.

## Task Sizing

| Size | Files | Action |
|------|-------|--------|
| XS | 1 | Single function/config change |
| S | 1-2 | One component or endpoint |
| M | 3-5 | One feature slice |
| L | 5-8 | Multi-component feature |
| XL | 8+ | Too large — break it down |

## Task Template

```markdown
## Task [N]: [Title]

**Description:** What this accomplishes.

**Acceptance criteria:**
- [ ] [Specific, testable condition]

**Verification:**
- [ ] Tests pass: `pytest Tests/...`
- [ ] Build succeeds

**Dependencies:** [Task numbers or "None"]
**Files likely touched:** [list]
```

## Rules

- Slice vertically (one full feature path at a time), not horizontally (all DB, then all API, then all UI)
- Add checkpoints every 2-3 tasks: all tests pass, build clean, human reviews before proceeding
- High-risk tasks go early — fail fast
- No task should touch more than ~5 files
