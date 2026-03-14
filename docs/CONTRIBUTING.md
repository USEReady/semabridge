# Contributing to SemaBridge

Thank you for contributing to SemaBridge! This document provides guidelines to ensure consistent, high-quality contributions.

---

## Development Setup

### Prerequisites

- Python 3.11
- Git
- Access to Snowflake and/or Microsoft Fabric (for integration testing)

### Installation

```powershell
# Clone the repository
git clone https://github.com/inarva-solutions-pvt-ltd/sema-bridge.git
cd sema-bridge

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your credentials

# Verify setup
python main.py validate
```

---

## Code Style

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Files/Folders | lowercase, snake_case | `snowflake_extractor.py` |
| Classes | PascalCase | `SnowflakeEmitter` |
| Functions | snake_case | `extract_metadata()` |
| Constants | UPPERCASE_SNAKE | `MAX_RETRY_COUNT` |
| Private | Leading underscore | `_internal_method()` |

### File Organization

```
src/semabridge/
├── cli/           # CLI commands
├── connectors/    # Platform integrations
├── converter/     # Transformation logic
├── core/          # Central orchestration
├── formats/       # Schema definitions
├── plugins/       # Extensions
├── repository/    # DuckDB versioning
├── sml/           # Semantic Modeling Language (core)
└── utils/         # Shared utilities
```

### Code Principles

1. **Maintainability over cleverness** - Write code a new engineer can understand
2. **Explicit over implicit** - Clear variable names, no magic numbers
3. **SML adapter is mandatory** - All data transformations must validate through SML
4. **No junk files** - No `temp`, `misc`, or experimental files

---

## Testing

### Requirements

| Metric | Threshold |
|--------|-----------|
| **Coverage** | ≥ 80% for new code |
| **All Tests** | Must pass before merge |

### Running Tests

```powershell
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src/semabridge --cov-report=term-missing

# Run specific test file
pytest tests/test_sml_models.py -v
```

### Writing Tests

- Place tests in `tests/` directory
- Name test files `test_<module>.py`
- Use fixtures from `tests/conftest.py`
- Mock external services (Snowflake, Fabric) for unit tests

---

## Pull Request Process

### Before Submitting

- [ ] All tests pass locally
- [ ] Coverage ≥ 80% for new code
- [ ] No debug/print statements
- [ ] Documentation updated (if API changed)
- [ ] Follows naming conventions
- [ ] No vague file names

### PR Description Template

```markdown
## Summary
Brief description of changes

## Changes
- List of specific changes

## Testing
- How this was tested

## Related Issues
Fixes #123
```

### Review Criteria

1. **Correctness**: Does it work as intended?
2. **Tests**: Are changes covered by tests?
3. **Style**: Follows conventions?
4. **Architecture**: Aligns with design principles?
5. **Documentation**: Is it clear what changed?

---

## Architecture Principles

1. **SML as Intermediate Representation** - Decouples source/target for extensibility
2. **DuckDB for State** - Embedded, zero-config versioning
3. **Non-destructive Rollback** - Creates new snapshot; history preserved
4. **Tiered DAX Translation** - Graceful degradation for complex expressions
5. **Adapter Interface** - Standardized contract for all platforms

See `docs/ARCHITECTURE.md` for detailed design documentation.

---

## Adding New Features

### New Connector

1. Create file in `src/semabridge/connectors/`
2. Implement extraction/emission interface
3. Add tests in `tests/`
4. Update `docs/ARCHITECTURE.md`

### New CLI Command

1. Add to `src/semabridge/cli/main.py` or create new command module
2. Follow existing command patterns
3. Add tests
4. Update `docs/PRODUCT.md`

### New Format

1. Add schema in `src/semabridge/formats/`
2. Implement validation in SML layer
3. Add tests

---

## Questions?

- Review existing code in `src/semabridge/`
- Check `docs/ARCHITECTURE.md` for design rationale
- Read `agent.md` for behavioral guidelines
