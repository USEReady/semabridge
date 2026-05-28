# Fabric → Official SML Pipeline Migration

## Problem Statement
How might we replace the current internal SML path used in the Fabric → Snowflake pipeline with the official SML as the canonical intermediate representation, preserving existing behavior and avoiding breaking changes for consumers?

## Recommended Direction
Adopt official SML as the canonical intermediate contract between Fabric and Snowflake. Implement lightweight adapters that translate from Fabric's current semantic artifacts to official SML on export, and from official SML back to Snowflake-specific artifacts (or downstream formats) on deploy. Use `sml-converters`/`sml-cli` as the initial integration approach (external CLI or `npx`) and vendor only if we encounter performance, licensing, or feature gaps.

## Key Assumptions to Validate
- Fabric model constructs map losslessly to official SML for tables, relationships, measures, hierarchies, and metadata.
- `sml-converters` provides stable, deterministic transforms for the Snowflake target we need.
- Running the converter as an external CLI in CI and pipelines is operationally acceptable.

Validation tests:
- Round-trip tests (Fabric -> official SML -> Fabric) for representative models.
- Golden snapshot comparisons for canonical outputs.
- Repeatability tests with pinned converter versions.

## MVP Scope
In-scope:
- Canonicalization adapter: Fabric -> official SML (minimal feature set: datasets, columns, relationships, basic metrics).
- Deploy adapter: official SML -> Snowflake Cortex/YAML (via `sml-converters sml-to-cortex`).
- CI validation: golden tests + validator run on PRs.
- Rollout feature flag: `SML_CANONICAL_ENFORCE` with `warning` and `enforce` modes.

Out-of-scope:
- Full internal rewrite of model storage.
- Exhaustive coverage for every Fabric feature (iterative expansion instead).

## Rollout Plan (Phased, non-disruptive)
1. Spike & prototype (this repo): build adapters + golden tests (current state — done partly).
2. Warning mode: pipelines emit canonical SML and log deviations; no failures.
3. Opt-in enforcement: internal pilot teams enable `enforce` flag and validate production-like runs.
4. Default enforcement: flip to `enforce` once parity and performance verified; publish migration guide.

## Acceptance Criteria
- CI runs validator on changed semantic artifacts and fails only when `enforce` is enabled.
- Round-trip equivalence for representative models in golden suite.
- No consumer-facing breakages observed in pilot runs.

## Risks & Mitigations
- Risk: feature mismatch between Fabric constructs and official SML. Mitigation: define explicit extension policy and fallbacks; log unmapped fields and surface them in migration guide.
- Risk: converter non-determinism. Mitigation: pin converter versions in CI and add drift alerting for golden snapshots.

## Next Actions (first three)
1. Finalize canonical SML schema subset for Fabric MVP and record in `Docs/ideas`.
2. Implement Fabric -> official SML adapter slice for datasets/columns/relationships (prototype in `src/semabridge/sml`).
3. Add CI job to validate generated SML and run `sml-converters` as a smoke test in the pipeline.

## Open Questions
- Who are the pilot teams and what schedule works for opt-in testing?
- Which Fabric constructs require custom extensions in SML and how do we version them?
- Do we want to vendor parts of `sml-converters` or always call it externally?
