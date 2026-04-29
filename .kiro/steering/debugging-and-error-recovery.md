---
inclusion: manual
---

# Debugging and Error Recovery

Systematic root-cause debugging. When something breaks, stop adding features and follow this process.

## Stop-the-Line Rule

```
1. STOP adding features
2. PRESERVE evidence (error output, logs, repro steps)
3. DIAGNOSE using the triage checklist
4. FIX the root cause (not the symptom)
5. GUARD with a regression test
6. RESUME after verification passes
```

## Triage Checklist

1. **Reproduce** — Make the failure happen reliably. Can't reproduce = can't fix with confidence.
2. **Localize** — Which layer? UI / API / DB / Build / External service / The test itself?
3. **Reduce** — Create the minimal failing case.
4. **Fix root cause** — Ask "why does this happen?" until you reach the actual cause.
5. **Guard** — Write a test that catches this specific failure.
6. **Verify end-to-end** — Run the specific test, full suite, and build.

## Fix Root Cause, Not Symptoms

```
Symptom: duplicate entries in list
Bad fix: deduplicate in UI
Good fix: fix the JOIN query producing duplicates
```

## Regression Test Pattern

```python
# The bug: [description]
def test_bug_reproduction():
    # This test FAILS without the fix, PASSES with it
    ...
```

## Verification After Fix

- [ ] Root cause identified and documented
- [ ] Fix addresses root cause, not symptoms
- [ ] Regression test exists that fails without the fix
- [ ] All existing tests pass
- [ ] Build succeeds
