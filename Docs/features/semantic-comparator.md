# Semantic Comparator

The semantic comparator analyzes YAML model files and computes structural and
semantic diffs across multiple formats.

## Supported Formats
- OSI
- SML
- TSML
- Snowflake semantic model YAML

## API Endpoints
Base path: /api/comparator
- POST /api/comparator/parse
- POST /api/comparator/compare
- POST /api/comparator/compare-semantic

## LLM Evaluation
Optional LLM evaluation is supported for semantic comparisons. The comparator
checks for provider keys in the environment and returns 503 if none are set.

## Implementation References
- Comparator router: src/semabridge/api/routers/comparator_router.py
- Adapters: src/semabridge/api/routers/comparator_router.py (adapter classes)
