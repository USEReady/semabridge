---
inclusion: manual
---

# Test-Driven Development

Write a failing test before writing the code that makes it pass. Tests are proof — "seems right" is not done.

## The TDD Cycle

```
RED (write failing test) → GREEN (minimal code to pass) → REFACTOR (clean up)
```

## The Prove-It Pattern (Bug Fixes)

Never start by fixing a bug. Start by writing a test that reproduces it:

1. Write a test that demonstrates the bug → it FAILS (bug confirmed)
2. Implement the fix → test PASSES (fix proven)
3. Run full suite → no regressions

## Test Pyramid

```
     E2E (~5%)         — Full user flows
  Integration (~15%)   — API boundaries, component interactions
   Unit (~80%)         — Pure logic, isolated, milliseconds each
```

## Writing Good Tests

- Test state (outcomes), not interactions (method calls)
- DAMP over DRY — each test should be self-contained and readable
- Prefer real implementations over mocks; mock only at slow/non-deterministic boundaries
- Arrange-Act-Assert pattern
- One assertion per concept
- Descriptive names: `it('sets completedAt when task is completed')`

## Verification Checklist

- [ ] Every new behavior has a corresponding test
- [ ] All tests pass
- [ ] Bug fixes include a reproduction test that failed before the fix
- [ ] No tests were skipped or disabled
- [ ] Coverage hasn't decreased
