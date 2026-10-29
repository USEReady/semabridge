# Complex-measure validation (2 scripts, generic, offline)

| Script | Checks | Needs SemaBridge |
|---|---|---|
| `validate_complex_translation.py` | Translation stage: dependency DAG, cascade vs own failure, anchor from lineage, dependency inlining, what-if/disconnected tables, exact SELECTEDVALUE, structure, values | yes (`--repo .`) |
| `validate_complex_ddl.py` | Generated DDL: presence/silent drops, phantom measure columns, unknown columns, aggregate rules, anchor, reachability, enriched-view columns (built from your CREATE VIEW SQL), values | no |

Run (AI example):

    # 1) translation (full pipeline from the .pbix)
    uv run python Tests\phase_a\complex_measure_tests\validate_complex_translation.py ^
        --pbix "C:\...\Artificial Intelligence Sample.pbix" --repo . ^
        --dax-query Tests\phase_a\complex_measure_tests\ai_complex_measures.dax ^
        --expected Tests\phase_a\complex_measure_tests\ai_expected_values.json
    #    add --stage translator to test the translator alone (isolates pipeline issues)

    # 2) generated DDL (+ enriched views)
    uv run python Tests\phase_a\complex_measure_tests\validate_complex_ddl.py ^
        --pbix "C:\...\Artificial Intelligence Sample.pbix" ^
        --ddl output\debug\debug_generated_sql_Artificial_Intelligence_Sample.sql ^
        --dax-query Tests\phase_a\complex_measure_tests\ai_complex_measures.dax ^
        --enriched-sql <file with the CREATE VIEW ..._ENRICHED statements> ^
        --expected Tests\phase_a\complex_measure_tests\ai_expected_values.json

Other models: pass that model's .pbix and DDL; --dax-query is optional (all .pbix measures are used);
--expected = {"measure name": value} from a Power BI DAX query. --measure "X" limits to some measures.
Exit code 1 = blocking issues (wrong value, phantom/unknown column, nested aggregate, wrong anchor, ANY_VALUE,
unsafe division, silent drop, NULL although translatable).
