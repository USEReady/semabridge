# SemaBridge - Project Structure

This project follows the standard `src`-layout pattern for Python packages.

## Directory Structure

```
semabridge/
├── src/
│   └── semabridge/          # Main package
│       ├── core/            # Execution lifecycle, config loading, logging
│       ├── connectors/      # Source/target integrations (Snowflake, Fabric)
│       ├── formats/         # Schema definitions, YAML validation
│       ├── converter/       # Transformation logic (TMSL ↔ SML)
│       ├── sml/             # SML model definitions and serialization
│       ├── repository/      # Persistence, version control, DuckDB
│       ├── cli/             # CLI commands and argument parsing
│       ├── plugins/         # Extension points (future)
│       └── utils/           # Shared helpers, logging, cache
├── tests/                   # Unit and integration tests
├── docs/                    # Documentation
├── examples/                # Example configurations
├── scripts/                 # Utility scripts
├── main.py                  # CLI entry point (run: python main.py)
├── pyproject.toml           # Package configuration
└── README.md                # Project README
```

## Module Mapping

| Old Location           | New Location                    |
|------------------------|---------------------------------|
| `semabridge/config/`   | `src/semabridge/core/` + `formats/` |
| `semabridge/extract/`  | `src/semabridge/connectors/`    |
| `semabridge/emit/`     | `src/semabridge/connectors/`    |
| `semabridge/transform/`| `src/semabridge/converter/`     |
| `semabridge/state/`    | `src/semabridge/repository/`    |
| `semabridge/api/`      | `src/semabridge/repository/`    |
| `semabridge/sml/`      | `src/semabridge/sml/`           |
| `semabridge/cli/`      | `src/semabridge/cli/`           |
| `semabridge/utils/`    | `src/semabridge/utils/`         |

## Running the CLI

```bash
# From project root
python main.py --help

# Show version
python main.py version

# Full pipeline
python main.py sync --name MyModel

# Reverse sync (Fabric → Snowflake)
python main.py reverse-sync --dataset-id <ID>
```

## Installing as Package

```bash
pip install -e .
semabridge --help
```

## Running Tests

```bash
pytest tests/ -v
```
