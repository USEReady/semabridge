# Merging this harness update into your repo (do NOT replace files your agent already edited)

New files (copy as-is): fixture_cross_table.py, cases_cross_table.py, cross_table.py

Small edits in existing files:
1. fixture.py - append at the end:
       INACTIVE = []
       from fixture_cross_table import extend as _extend_cross_table
       _extend_cross_table(TABLES, RELATIONSHIPS, INACTIVE)
2. cases.py - append at the end:
       from cases_cross_table import build as _build_cross_table
       CASES += _build_cross_table(BASE)
3. adapter.py - add to NA_WORDS: window metric | non-?additive | unrelated | time intelligence
4. mutate.py - plan() gets fallback names for tables/columns without a pool entry; apply() takes INACTIVE
   and sets mutate.MUTATED_INACTIVE; maps case fields anchor / last_period (see this file's diff)
5. run_feature_tests.py - build_db() / evaluate() use cross_table.build / cross_table.from_clause
   (joins, enriched copies, inactive aliases, last_period); prints "expected solution" on failure;
   anchor check. KEEP your agent's own changes (alias = to_alias, uppercase views) when merging.
6. run_pipeline_tests.py - passes INACTIVE to mutate; checks anchor table and ddl_contains in the DDL.
7. ../measure_inventory.py - ddl_metrics() now parses `NAME non additive by (...) as ...`
   (before this fix, NON ADDITIVE metrics were counted as MISSING).
