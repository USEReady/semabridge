# SemaBridge edge-case suite (1,734 tests)

Copy this folder to `Tests/edge_suite/` in the repo and run (Windows):

    .venv\Scripts\python -m pip install pyflakes duckdb python-dateutil
    .venv\Scripts\python -m pytest Tests/edge_suite -q -o addopts="" -o log_cli=false

Each test states the required behaviour. A failure is a finding: fix the code, not the test.

| File | Tests | What it checks |
|---|---|---|
| test_e01_ti_shapes_semantics.py | 508 | Time-intelligence flag SQL executed in DuckDB: 21 shapes x 24 anchors (leap days, month/quarter/year ends, fiscal years) vs an independent reference; no CURRENT_DATE |
| test_e02_ti_final_gate.py | 91 | Final gate: today-anchored SQL fails closed unless the DAX uses TODAY()/NOW() |
| test_e03_upsert_identity.py | 102 | Upsert identity: equivalent spellings match, distinct names never merge or disappear |
| test_e04_drift_compare.py | 144 | Drift comparison: formatting ignored, meaning detected, idempotent; exact NULL placeholders |
| test_e05_ddl_strip.py | 28 | WITH SYNONYMS / COMMENT removed; real SQL and string literals untouched |
| test_e06_sql_guard.py | 72 | Read-only guard: one read statement allowed; writes, side effects, multi-statements blocked |
| test_e07_fanout_guard.py | 33 | Ask AI join paths: many-to-one safe; fan-out and many-to-many flagged |
| test_e08_bi_truth_helpers.py | 83 | Ratio/window/private classification, expected SQL, project scope, JSON values, certification |
| test_e09_naming.py | 90 | Deployed identifiers valid, stable, within length, distinct |
| test_e10_mock_and_hygiene.py | 28 | No demo data in production code/UI; drift never invents events; scanner allow-list |
| test_e11_no_undefined_names.py | 555 | Every module: no undefined names in executable code (runtime NameError) |
