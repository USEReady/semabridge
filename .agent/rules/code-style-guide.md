---
trigger: always_on
---

# Semabridge Code Style Guide

## Naming Conventions
- Folder names: lowercase.
- New file names: PascalCase by default unless platform/tooling conventions require a different format.
- Python symbols:
    - Classes: PascalCase.
    - Variables/functions: snake_case.
    - Constants: UPPER_SNAKE_CASE.
- Secret environment variable reference keys should end with _env.

## Typing and Data Modeling
- Fully annotate public function and method signatures.
- Avoid Any unless strictly necessary.
- Prefer TypedDict, Protocol, dataclasses, or Pydantic models for structured data.
- Keep OSI/intermediate model boundaries strongly typed and validated.

## Exceptions and Error Messages
- Use project-specific exceptions from semabridge.core.exceptions.
- Do not raise bare Exception in application logic.
- Error messages must include useful context (workspace, model, connector, operation).

## Logging and Output
- Use project logging utilities and structured logging patterns.
- Never log secrets, tokens, or raw credential values.
- Do not use print statements in production paths.
- Include run context identifiers where available.

## Formatting and Imports
- Maintain consistent import grouping: stdlib, third-party, internal.
- Prefer concise comments explaining why a complex block exists.
- Keep module docstrings meaningful for public modules.
- Keep function/class docstrings meaningful for public APIs.

## Test and Quality Style
- Keep tests deterministic and isolated from live external services.
- Prefer fixtures and mocks over hardcoded environment dependencies.
- Keep test names explicit and behavior-focused.