# Drift monitoring test pack

Test cases for the drift package: normalisation, the comparison engine, the
platform parsers, and the contract for the baseline store, registry, cache and
event lifecycle.

Nothing here names a real project, view, metric, table, catalog or account. Every
fixture is generic and built inside the pack.

## Running

Against the reference implementation bundled here, which proves the suite is
self consistent:

    pytest drift_tests -q
    # 97 passed

Against the product:

    SB_DRIFT_IMPL=product pytest drift_tests -q

Optional overrides when module paths differ:

    SB_DRIFT_MODELS_MODULE      default semabridge.governance.drift.models
    SB_DRIFT_NORMALIZE_MODULE   default semabridge.governance.drift.normalize
    SB_DRIFT_COMPARISON_MODULE  default semabridge.governance.drift.comparison
    SB_DRIFT_PARSERS_MODULE     default semabridge.governance.drift.adapters
    SB_DRIFT_STORE_MODULE       default semabridge.governance.drift.baseline_store
    SB_DRIFT_REGISTRY_MODULE    default semabridge.governance.drift.adapters.registry

## What the product must expose

`models`: `ObjectDef(kind, table, name, expression, description)` with a `key`
property; `ViewDefinition(view_name, platform, source_path, objects,
capabilities, not_deployed)` with `by_key()`; `DriftFinding(category, kind,
table, name, baseline_value, live_value)`; `Capabilities(kinds, expressions,
descriptions)` with `reports(kind)`; the constants `METRIC`, `DIMENSION`,
`FACT`, `RELATIONSHIP`, `ADDITION`, `DELETION`, `EXPRESSION_DRIFT`,
`DESCRIPTION_DRIFT`; the exceptions `SourcePathMismatch`, `SidesDoNotMatch`.

`normalize`: `normalize_expression(text, dialect, max_length=...)` returning an
object with `.text` and `.method`; `normalize_description(text)`;
`comparable(a, b, dialect)`; `is_placeholder(text)`; the constants
`PLACEHOLDER`, `PARSED`, `TEXT_FALLBACK`.

`comparison`: `compare(baseline, live, dialect=None)`; `group_counts(findings)`.

`parsers` (the adapters package): `parse_ddl(text, view_name, platform)`,
`parse_yaml(text, view_name, platform, capabilities=None)`,
`assert_names_are_identifiers(definition)`, `split_top_level(text, separator)`,
`DefinitionUnreadable`.

A missing name fails with a message naming it, rather than skipping, because a
missing name is a gap in the contract.

## Files

| File | Covers |
| --- | --- |
| `test_normalize.py` | formatting that must disappear, meaning that must survive, fallbacks, placeholders, determinism, golden output |
| `test_comparison.py` | the four categories per kind, not-deployed suppression, capabilities, source path mismatch, the no-match safety net, deterministic order, counts |
| `test_parsers.py` | the name and expression split, trailing clauses, section boundaries, malformed and oversized input, the identifier guard, and the round trip where a fresh deploy shows zero findings |
| `test_contract_store_registry_lifecycle.py` | baseline store, registry and connection failures, concurrency, cache, event lifecycle and paging. Needs the product and its fixtures |
| `fixtures/` | one deployed definition and one reformatted live definition per format |

## The two cases that matter most

`test_3_20_sides_that_never_matched_are_refused_not_reported_as_drift` and
`test_2_1b_name_and_expression_are_not_swapped` both fail on the parser fault
that produced hundreds of additions and deletions with zero expression drift.

## Stateful cases

The fourth file is written against the contract and needs fixtures that deploy,
break a connection, run two schedulers and so on. Implement those fixtures in
the repository's own conftest, next to the existing drift tests. Until then the
file reports as not run; that is accurate, and better than a pass that proves
nothing.
