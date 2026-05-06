# Configuration Files

Semabridge configuration is split between global runtime config, behavior
policies, and project-specific settings.

## Global Runtime Config

File: Config/config.yaml

Controls:
- repository path
- database backend
- logging defaults
- concurrency defaults

## Behavior Policy

File: Config/behavior.yaml

Controls:
- feature flags for Databricks and discovery
- semantic model sync behavior
- cross-table join settings

## Project Configuration

File: Config/semabridge.yaml

Controls:
- source/target connectors
- model selection and mappings
- UI preferences

## Example Run Config

Reference example:
- src/semabridge/formats/example_run_config.yaml

Use this as a starting point for new project-level run configs.

## Environment Variables

See .env.example for the baseline environment variables needed for:
- Snowflake credentials
- Fabric credentials
- API keys for optional LLM integrations
