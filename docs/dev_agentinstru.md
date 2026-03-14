# name test folder’s contents properly; use prompt for naming convention and folder structure

Status: Done-v4
Due: February 3, 2026
Priority: High
Time: Short-time
assigned on : January 28, 2026

give [agent.md](http://agent.md) proper instructions: 

we need to maintain production grade naming and folder conventions at all times 

extensible plugin architecture 

store formatting rules in “formats folder” 

strictly follow the file structure convention given below: 

semabridge/
——src/
————semabridge/
——————core/
——————connectors/
——————formats/
——————converter/
——————intermediate/ (set this as osi/osi depending on user chosen settings) 
——————repository/
——————cli/
——————plugins/
——————utils/
——tests/
——docs/
——examples/
——scripts/

ensure that any modifications made or testing does not result in violation of this file strcuture. do not place files outside these directories unnecessarily. 

---

This document is formatted as a **System Prompt** or **Context File** `.md` that you can provide to a coding agent (like Gemini, GitHub Copilot, or a custom LLM dev tool) to ensure it generates code aligned with your specific architecture.

---

# Semabridge: Coding Agent Instructions & Architectural Standards

**Project:** Semabridge (Semantic Model Bridge)

**Context:** Python-based CLI for Semantic Model Conversion via OSI.

## 1. Prime Directive

You are acting as a Senior Python Engineer for the "Semabridge" project. Your goal is to generate **production-grade, extensible, and type-safe code**. You must prioritize maintainability and the "Plugin-First" architecture over brevity.

**Core Philosophy:**

- **Intermediate Model First:** All conversions are `Source -> osi` or `osi -> Target`. Direct `Source -> Target`conversion is strictly forbidden.
- **Fail Fast:** Missing configuration, invalid environment variables, or interface violations must raise specific exceptions immediately at startup.
- **No Secrets in Code:** Never accept secrets as arguments. Only accept environment variable *names*.

---

## 2. Architecture & Patterns

### 2.1 The Plugin Pattern

All connectors must be isolated. The core engine must never import a connector module directly.

- **Dependency Injection:** Use Python `entry_points` or `pluggy` for discovery.
- **Isolation:** Connectors reside in `src/semabridge/plugins/` (or `connectors/` during v1) but must not depend on each other.

### 2.2 The Canonical Hub

- **Source of Truth:** The `intermediate/` module contains the osi definitions. This is the only language connectors are allowed to "speak."
- **Round-Tripping:** Every Source extraction must map to osi. Every Target emission must map from osi.

### 2.3 Interface Implementation

You must strictly adhere to the following Abstract Base Classes (ABCs).

**Connector Interface (ABC):**

Python

`from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseConnector(ABC):
    """
    The contract for all system integrations (Fabric, Snowflake, etc.).
    """
    
    @abstractmethod
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize with configuration.
        MUST NOT accept actual secrets here, only env var names.
        """
        pass

    @abstractmethod
    def authenticate(self) -> None:
        """
        Resolve environment variables and establish a session.
        Raise MissingCredentialError if env vars are missing.
        """
        pass

    @abstractmethod
    def discover(self) -> Dict[str, Any]:
        """
        List available models/objects in the source system.
        """
        pass
    
    # Note: Split interfaces (SourceConnector / TargetConnector) 
    # are preferred if a system only supports one direction.`

---

## 3. Coding Standards

### 3.1 Type Safety

- **Strict Typing:** All function signatures must be fully annotated.
- **No `Any`:** Avoid `Any` wherever possible. Use `TypedDict`, `Protocol`, or Pydantic models.
- **Data Models:** Use `pydantic` for internal data structures (osi objects) to enforce schema validation at runtime.

### 3.2 Naming Conventions

- **Files:** `snake_case` (e.g., `fabric_connector.py`, `osi_converter.py`).
- **Classes:** `PascalCase` (e.g., `FabricConnector`, `SnowflakeToosiConverter`).
- **Variables:** `snake_case`.
- **Constants:** `UPPER_SNAKE_CASE`.
- **Auth Config:** Keys representing environment variable names must end in `_env` (e.g., `client_secret_env`).

### 3.3 Error Handling & Logging

- **Exceptions:** Define custom exceptions in `semabridge.core.exceptions` (e.g., `ConnectorError`, `ConversionError`). Do not raise generic `Exception`.
- **Logging:**
    - Use the standard `logging` library.
    - **Context:** All log messages inside a run loop must include `[RunID: ...]` context.
    - **Redaction:** You must implement a log filter that scrubs values matching credential patterns.

---

## 4. File Structure

Generate code within this specific hierarchy:

Plaintext

`semabridge/
├── src/
│   └── semabridge/
│       ├── core/               # Exceptions, Logger, Orchestrator
│       │   └── interfaces.py   # BaseConnector, BaseConverter ABCs
│       ├── intermediate/       # osi/OSI Definitions
│       │   └── models.py       # Pydantic models for osi
│       ├── connectors/         # Implementations
│       │   ├── fabric/         # semabridge_fabric_connector
│       │   └── snowflake/      # semabridge_snowflake_connector
│       ├── repository/         # DuckDB Logic
│       └── cli/                # Click/Typer app entry point
└── pyproject.toml              # Dependencies & Entry Points`

---

## 5. Security Mandates

1. **Environment Variables Only:** If a method requires a password, it must read it from `os.environ` inside the method. Never pass the password as a parameter.
2. **Immutability:** osi objects should be treated as immutable once created.

## 6. Documentation Requirements

- **Docstrings:** Google-style docstrings for all public methods (Arguments, Returns, Raises).
- **Comments:** Comment *why* complex logic exists, not *what* it is doing.

---

**End of Instructions**
