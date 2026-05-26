# OSI/SML-Centered Model

Semabridge enforces an OSI/SML-first architecture. All transformations must pass
through the OSI/SML intermediate model, and direct source-to-target shortcuts
are not allowed.

## Canonical Flow

```
Source -> OSI/SML validation -> Transformation -> OSI/SML validation -> Target
```

## Responsibilities
- Define the OSI/SML model shape and contracts.
- Validate intermediate data before and after transformation.
- Provide deterministic transformation behavior across connectors.

## Implementation Touchpoints
- Intermediate models: src/semabridge/intermediate/
- OSI/SML serialization: src/semabridge/sml/
- Conversion logic: src/semabridge/converter/
- Format validation: src/semabridge/formats/

## Operational Guidance
- Validate OSI/SML objects on input and output.
- Keep transformations deterministic and traceable in logs.
- Update OSI/SML docs when model shape or validation changes.
