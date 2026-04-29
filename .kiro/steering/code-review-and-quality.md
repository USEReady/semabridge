---
inclusion: manual
---

# Code Review and Quality

Multi-dimensional review before merging any change. Review covers five axes.

## The Five Axes

1. **Correctness** — Does it match the spec? Edge cases handled? Error paths handled?
2. **Readability** — Clear names? Straightforward control flow? Could it be fewer lines?
3. **Architecture** — Follows existing patterns? Clean module boundaries? No circular deps?
4. **Security** — Input validated? No secrets in code? Auth checks in place? External data treated as untrusted?
5. **Performance** — No N+1 queries? No unbounded loops? Pagination on list endpoints?

## Comment Severity Labels

| Prefix | Meaning |
|--------|---------|
| *(none)* | Required — must fix before merge |
| **Critical:** | Blocks merge — security, data loss, broken functionality |
| **Nit:** | Optional — style preference |
| **Consider:** | Suggestion — worth thinking about, not required |
| **FYI** | Informational — no action needed |

## Change Size Targets

- ~100 lines: ideal
- ~300 lines: acceptable for a single logical change
- ~1000 lines: too large, split it

Separate refactoring from feature work — submit them as separate changes.

## Review Checklist

- [ ] Tests cover the change adequately
- [ ] Names are clear and consistent
- [ ] No secrets in code
- [ ] Input validated at boundaries
- [ ] No N+1 patterns
- [ ] Tests pass, build succeeds
