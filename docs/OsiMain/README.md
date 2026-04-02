# OsiMain

## Purpose
This section defines how Semabridge applies the OSI-centered architecture as the canonical semantic translation hub.

## Canonical Flow
All semantic transformations follow the same mandatory path:

Source -> OSI Validation -> Transformation -> OSI Validation -> Target

Direct Source -> Target conversions are not allowed.

## Responsibilities
- Describe OSI model responsibilities and boundaries.
- Document source-to-OSI and OSI-to-target expectations.
- Capture validation rules for intermediate objects.
- Record extension points for connectors and converters.

## Core Principles
- Intermediate-model first architecture.
- Fail-fast behavior for invalid schemas or missing config.
- Type-safe data contracts for semantic objects.
- Predictable transformation behavior across connectors.

## Implementation Touchpoints
- Intermediate models: src/semabridge/intermediate/
- Conversion logic: src/semabridge/converter/
- Format and schema rules: src/semabridge/formats/
- Connector orchestration: src/semabridge/connectors/

## Operational Guidance
- Validate OSI objects before and after transformations.
- Raise specific exceptions for schema and mapping failures.
- Keep transformations deterministic and traceable in logs.
- Update this section when OSI model shape or flow contracts change.

## Verification Checklist
- Source extract maps to valid OSI objects.
- Target emitter consumes only validated OSI structures.
- No direct source-target bypasses introduced.
- Documentation reflects current converter behavior.
