# Ask AI SQL guard - 69 test cases

Tests for the issues seen in Ask AI answers:
- wrong metric for generic words ("employees" -> active employees instead of all employees),
- generated SQL anchored to today's date (CURRENT_DATE year filters on historical data),
- free-form SQL on base tables or SemaBridge-generated objects (marked in metadata) instead of the semantic view,
- averages over snapshot rows, joins that multiply rows, two fact tables through one dimension,
- invented business rules (e.g. a status guessed from a date column), helper metrics (SUM_OF_*),
- status splits that should be governed (filtered measures -> IS_<MEASURE> dimensions),
- verified queries only from certified answers.

## Files
| File | Purpose |
|---|---|
| test_ai_sql_guard.py | The tests (groups A-H) |
| model_fixture.py | Generic model metadata the guard reads (tables, grain, keys, metrics, filters, dimensions) |
| adapter.py | Points the tests at the implementation |
| reference_sql_guard.py | Reference implementation: same code as sql_validator.py; proves the tests are consistent (69/69 pass) |
| conftest.py | Path setup |

## Contract (module `semabridge.ai.sql_validator`, to implement)
- `find_synonym_collisions(model)`, `assign_synonyms(model)`, `resolve_metric(question, model)`
- `validate_sql(sql, model, question)` -> action / path / sql / flags / assumptions / unmapped / suggestion / certifiable
- `status_dimensions(model)`, `verified_queries_from_cases(cases)`
Flag codes are listed at the top of test_ai_sql_guard.py. The product code must be generic: no model,
table, measure or value names.

## Run
Copy the folder to `Tests/ai_sql_guard_tests/`, then:

    pip install sqlglot
    # against the product implementation (fails until semabridge.ai.sql_validator exists)
    .venv\Scripts\python -m pytest Tests/ai_sql_guard_tests -q -o addopts=""
    # against the reference implementation (shows the target behaviour)
    set SB_SQL_GUARD_IMPL=reference
    .venv\Scripts\python -m pytest Tests/ai_sql_guard_tests -q -o addopts=""

Results: reference 69/69 pass. The validator reads everything model-specific from metadata (no words, names or naming conventions in code).
A failure is a finding: fix the code, not the test.
