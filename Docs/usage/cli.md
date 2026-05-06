# CLI Reference

The Semabridge CLI entry point is defined in src/semabridge/cli/main.py.

## Top-Level Commands
- app-version
- init
- config
- validate
- extract
- build
- emit
- publish
- sync

## Command Groups
- semabridge semantic ... (semantic API and model management)
- semabridge diff ... (compare semantic models)
- semabridge logs ... (execution logs)
- semabridge version ... (model version control)
- semabridge sync ... (bidirectional sync workflows)

## Example

```
semabridge semantic sync
semabridge diff compare -d <id> --from <v1> --to <v2>
```
