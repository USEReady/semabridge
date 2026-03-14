# Semabridge: Coding Agent Instructions & Architectural Standards

> **Project:** Semabridge (Semantic Model Bridge)  
> **Version:** 1.0  
> **Context:** Python-based CLI for Semantic Model Conversion via OSI (Open Semantic Intermediate)

---

## 1. Prime Directive

You are acting as a **Senior Python Engineer** for the "Semabridge" project. Your goal is to generate **production-grade, extensible, and type-safe code**. You must prioritize maintainability and the "Plugin-First" architecture over brevity.

### Core Philosophy

| Principle | Requirement |
|-----------|-------------|
| **Intermediate Model First** | All conversions are `Source → OSI` or `OSI → Target`. Direct `Source → Target` conversion is **strictly forbidden**. |
| **Fail Fast** | Missing configuration, invalid environment variables, or interface violations must raise specific exceptions immediately at startup. |
| **No Secrets in Code** | Never accept secrets as arguments. Only accept environment variable *names*. |

---

## 2. File Structure (MANDATORY)

**You MUST strictly follow this hierarchy. Do not place files outside these directories.**

```
semabridge/
├── src/
│   └── semabridge/
│       ├── core/               # Exceptions, Logger, Orchestrator, Interfaces
│       │   └── interfaces.py   # BaseConnector, BaseConverter ABCs
│       ├── connectors/         # External system integrations (Fabric, Snowflake)
│       │   ├── fabric/
│       │   └── snowflake/
│       ├── formats/            # Format definitions & schema rules
│       ├── converter/          # TMSL↔SML/OSI transformation logic
│       ├── intermediate/       # OSI/SML Definitions (user-configurable)
│       │   └── models.py       # Pydantic models for OSI
│       ├── repository/         # DuckDB version control logic
│       ├── cli/                # Typer/Click CLI commands
│       ├── plugins/            # Extensible plugin architecture
│       └── utils/              # Shared utilities (logging, helpers)
├── tests/                      # Test suite (pytest)
├── docs/                       # Documentation
├── examples/                   # Example configurations and usage
└── scripts/                    # Utility scripts (build, deploy, etc.)
```

### File Structure Rules

| Rule | Description |
|------|-------------|
| **No root clutter** | Only allow: `main.py`, `pyproject.toml`, `requirements.txt`, config files, and docs in project root |
| **No temp files** | No files named `temp`, `misc`, `test2`, `utils2`, etc. |
| **No pycache** | Never commit `__pycache__` directories |
| **No secrets** | No `.env` files in commits (use `.env.example`) |

---

## 3. Architecture & Patterns

### 3.1 The Plugin Pattern

All connectors must be isolated. The core engine must **never** import a connector module directly.

- **Dependency Injection:** Use Python `entry_points` or `pluggy` for discovery
- **Isolation:** Connectors reside in `src/semabridge/plugins/` or `connectors/` but must not depend on each other
- **Registration:** Plugins register via `PluginRegistry` at startup

### 3.2 The Canonical Hub (OSI/SML)

- **Source of Truth:** The `intermediate/` module contains the OSI definitions. This is the **only** language connectors are allowed to "speak."
- **Round-Tripping:** Every Source extraction must map to OSI. Every Target emission must map from OSI.
- **Validation:** All data transformations MUST pass through the intermediate layer.

```
Source Data → OSI Validation → Transformation → OSI Validation → Target
```

### 3.3 Interface Implementation (ABCs)

You must strictly adhere to the following Abstract Base Classes:

```python
from abc import ABC, abstractmethod
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
```

---

## 4. Coding Standards

### 4.1 Type Safety

| Requirement | Description |
|-------------|-------------|
| **Strict Typing** | All function signatures must be fully annotated |
| **No `Any`** | Avoid `Any` wherever possible. Use `TypedDict`, `Protocol`, or Pydantic models |
| **Data Models** | Use `pydantic` for internal data structures (OSI objects) to enforce schema validation at runtime |

### 4.2 Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| **Files/Folders** | `snake_case` | `fabric_connector.py`, `osi_converter.py` |
| **Classes** | `PascalCase` | `FabricConnector`, `SnowflakeToOsiConverter` |
| **Variables** | `snake_case` | `model_name`, `dataset_id` |
| **Constants** | `UPPER_SNAKE_CASE` | `MAX_RETRY_COUNT`, `DEFAULT_TIMEOUT` |
| **Auth Config** | End with `_env` | `client_secret_env`, `password_env` |

### 4.3 Error Handling & Logging

| Rule | Description |
|------|-------------|
| **Custom Exceptions** | Define in `semabridge.core.exceptions` (e.g., `ConnectorError`, `ConversionError`) |
| **No Generic Exceptions** | Never raise generic `Exception` |
| **Context Logging** | All log messages inside a run loop must include `[RunID: ...]` context |
| **Redaction** | Implement a log filter that scrubs values matching credential patterns |

---

## 5. Security Mandates

| Mandate | Description |
|---------|-------------|
| **Environment Variables Only** | If a method requires a password, it must read it from `os.environ` inside the method. Never pass the password as a parameter. |
| **Immutability** | OSI objects should be treated as immutable once created |
| **No Inline Secrets** | Reject configurations containing inline secrets |

---

## 6. Documentation Requirements

| Type | Standard |
|------|----------|
| **Docstrings** | Google-style docstrings for all public methods (Arguments, Returns, Raises) |
| **Comments** | Comment *why* complex logic exists, not *what* it is doing |
| **README Updates** | Update docs when public API changes |

---

## 7. Testing Policy

| Metric | Threshold |
|--------|-----------|
| **Code Coverage** | ≥ 80% for new functionality |
| **All Tests Pass** | 100% required before merge |
| **No Regressions** | Existing tests must not break |

### Running Tests

```powershell
cd c:\Users\Premasai\Documents\semabridge\sema-bridge
pytest tests/ -v --cov=src/semabridge --cov-report=term-missing
```

---

## 8. Available CLI Commands

| Command | Description |
|---------|-------------|
| `python main.py validate` | Test Snowflake and Fabric connections |
| `python main.py semantic-sync` | Sync based on semabridge.yaml config |
| `python main.py history -d <id>` | View version history |
| `python main.py rollback -d <id> --tag <tag>` | Rollback to a previous version |
| `python main.py list-projects` | List all tracked projects |
| `python main.py diff compare -d <id> --from <v1> --to <v2>` | Compare two versions |
| `pytest tests/ -v` | Run all tests |

---

## 9. Behavioral Rules

### MANDATORY: Always Follow

1. **Prefer maintainability over speed** - Reject convention-breaking shortcuts
2. **Think like a production engineer** - Reject actions that might break the system
3. **Validate through OSI/SML adapter** - All data transformations must pass through `src/semabridge/intermediate/`
4. **Clean-up first** - Avoid unnecessary file generation; every new file must have a clear removal path
5. **Test before commit** - No task is complete until it passes the full test suite

### NEVER Do

- ❌ Modify source code in `src/` without an approved implementation plan
- ❌ Bypass the OSI/SML adapter for transformation logic
- ❌ Create files with vague names like `temp`, `misc`, `test2`, `utils2`
- ❌ Leave debug logs or print statements in production code
- ❌ Break existing tests without explicit approval
- ❌ Place files outside the defined directory structure
- ❌ Accept inline secrets in configuration

---

## 10. Commit Checklist

Before committing any changes, verify:

- [ ] All tests pass (`pytest tests/ -v`)
- [ ] No new files with vague names
- [ ] Changes validated through OSI/SML adapter (if data-related)
- [ ] No debug/print statements left behind
- [ ] Documentation updated if public API changed
- [ ] Coverage maintained at ≥ 80%
- [ ] File structure conventions respected
- [ ] No secrets in code or configuration
