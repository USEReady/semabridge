# Feature tests: run after every feature

Generic test harness for SemaBridge's DAX → SQL translation. No model, table or column names from real
models: every case is a DAX pattern on a small synthetic star (fixture.py). Each case's SQL is **executed**
on that data (DuckDB, Snowflake SQL transpiled with sqlglot) and the **result** is compared with a value
computed independently from the data, so a test passes only when the number is right.

| File | What it is |
|---|---|
| fixture.py | synthetic tables: Orders (fact), Product, Measures (empty measure table), Param (what-if) |
| cases.py | 34 cases grouped by feature F1-F7, each with an independent expected value |
| adapter.py | the only file that calls SemaBridge (DAXTranslator). Wire it once if your API differs |
| run_feature_tests.py | runs the cases, prints PASS / TOTAL per feature, fails on any regression |

## Workflow per feature

    pip install duckdb sqlglot pandas                       # once
    uv run python phase_a\feature_tests\run_feature_tests.py --selftest   # once: proves cases + fixture agree
    uv run python phase_a\feature_tests\run_feature_tests.py              # baseline before any change

    # implement feature N, then:
    uv run python phase_a\feature_tests\run_feature_tests.py --feature F<N>   # the feature's own cases must pass
    uv run python phase_a\feature_tests\run_feature_tests.py                  # everything: 0 REGRESSED

Results are kept in feature_test_results\latest.json; each run lists "newly passing" and "REGRESSED"
cases against the previous run and exits 1 on a regression.

After the feature tests pass, check real models end to end:

    uv run python phase_a\measure_inventory.py --report <PowerBI_Measures_Report_Model_wise.xlsx> --repo . --compare-baseline measure_results\baseline.json
    uv run python phase_a\run_phase_a.py        # relationships: nothing that was EMITTED may become MISSING

## Feature order and cases

| Feature | Implement | Cases |
|---|---|---|
| F1 Lexer & parser | skip --, //, /* */; TRUE/FALSE as booleans; '-' is minus after a value; fail on leftover tokens | F1.1-F1.8 |
| F2 References | keep the table named in the DAX (measure-only tables); bare [Name] -> home-table column; quote/sanitise names | F2.1-F2.4 |
| F3 Iterators & filters | SUMX/AVERAGEX/MINX/MAXX/COUNTAX, KEEPFILTERS, COUNTROWS, CALCULATE + FILTER(same table, ISBLANK / NOT ISBLANK / compare), DATEDIFF | F3.1-F3.9 |
| F4 VAR / RETURN | inline variables (scoped) | F4.1-F4.2 |
| F5 Functions | VALUE, LEFT, MROUND, ROUNDUP, ROUNDDOWN, DIVIDE alternate result | F5.1-F5.4 |
| F6 Not applicable | text, what-if SELECTEDVALUE, RANKX, ALL() share -> excluded with a reason, never a metric | F6.1-F6.5 |
| F7 Dependencies | measures referencing measures resolve through the base measures' SQL | F7.1-F7.2 |

## No-hardcoding rules (review every change against these)

- No real table, column, measure or model name in SemaBridge code; tests only use the synthetic fixture.
- Each rule handles a DAX pattern for any table: the home table comes from the measure, other tables from the DAX.
- Anything not handled fails visibly (NULL with a reason, or "not applicable" with a reason), never partial SQL.
- A new pattern = a new case in cases.py first (failing), then the rule.

## Covering every stage (so no measure is lost unnoticed)

| Stage | Script | Needs |
|---|---|---|
| S1-S6 extraction from a real .pbix -> SML | `../measure_stage_diagnostic.py` | real .pbix files |
| Translation only (DAX -> SQL) | `run_feature_tests.py` | DAXTranslator (adapter.py) |
| TMSL -> OSI -> SML -> DDL -> value (offline) | `run_pipeline_tests.py` | pipeline_adapter.py wired to your repo |
| ... -> Snowflake view created -> each metric queried -> value | `run_snowflake_tests.py` | pipeline_adapter.py + Snowflake (scratch schema) |

`run_pipeline_tests.py` and `run_snowflake_tests.py` build a TMSL model from the synthetic fixture and the
cases (one measure per case, on the case's home table, plus the base measures they reference) and send it
through SemaBridge's own stages. For every case they print the stages reached
(`OSI | SML | SML_SQL | DDL REAL/NULL/MISSING | VIEW_CREATED | QUERY_OK | VALUE`) and group failures by the
stage where they were lost.

    uv run python phase_a\feature_tests\run_pipeline_tests.py
    uv run python phase_a\feature_tests\run_snowflake_tests.py --dry-run     # prints the SQL, touches nothing
    uv run python phase_a\feature_tests\run_snowflake_tests.py               # loads SEMABRIDGE.SB_FEATURE_TESTS, creates the view, queries every metric, drops the schema
    uv run python phase_a\feature_tests\run_snowflake_tests.py --keep        # keep the schema to inspect

**Wire `pipeline_adapter.py` first**: `to_osi` and `to_sml` use the converter classes seen in your repo;
`generate_ddl` is a placeholder (marked "adjust to your repo") because the DDL entry point depends on your
version: call whatever produces the CREATE SEMANTIC VIEW text in a dry run, with the live columns it is given.
Alternatively pass `--ddl-file` to `run_snowflake_tests.py` with a DDL SemaBridge generated for the
SB_FEATURE_TESTS schema.

## No hardcoding, generic for every model

Two checks, run after every feature (both must pass before the commit):

**1. Rename test** - the same cases with every table and column renamed (spaces, digits, %, $, &, ?), same data,
same expected values. Generic code scores exactly the same; code tied to names fails.

    uv run python phase_a\feature_tests\run_feature_tests.py --mutate 1
    uv run python phase_a\feature_tests\run_feature_tests.py --mutate 2
    uv run python phase_a\feature_tests\run_pipeline_tests.py --mutate 1

PASS / TOTAL per feature must equal the normal run. Each seed keeps its own results file and regression check.

**2. Code audit** - scans every line added under src/ since F1 for names from your real models (all tables,
columns, measures in the .pbix folder and the measures report) and from the test fixture.

    uv run python phase_a\feature_tests\audit_hardcoding.py --pbix-dir "C:\Users\manoj.s\Downloads\PBIX Files" --report <measures report.xlsx>

HARD = a real name in a string literal (fails); REVIEW = name-like code to look at (== "Name",
startswith("Name"), lists of names, a model name in Snowflake form like "SALES."); INFO = names only in comments.

## Cross-table features (F11-F15) - implement one, then run its tests

| Feature | What the implementation must do (generic) | Command |
|---|---|---|
| F10 SELECTEDVALUE | `IFF(COUNT(DISTINCT c) + MAX(IFF(c IS NULL,1,0)) = 1, MAX(c), alt)`; parameter table (no relationships) used in another measure -> its default constant; `'Table'[Measure]` = measure | `run_feature_tests.py --feature F10` |
| F11 Anchor by lineage | Anchor = table the columns come from (after inlining), not the DAX home table; in translation AND at DDL emission | `--feature F11` + `run_pipeline_tests.py --feature F11` (checks the DDL table) |
| F12 Dimension filters | Filter on the dim KEY -> fact foreign key; filter on a dim attribute -> enriched copy `<DIM>_<COL>` or join; unreachable -> NULL + reason | `--feature F12` |
| F13 Inactive relationships | Each inactive relationship -> alias of the dimension named `<Dim> <FK column>` (e.g. calendar_ship_date) joined on that FK; `USERELATIONSHIP` uses it; other measures unchanged | `--feature F13` |
| F14 Share of total (ALL) | Until window metrics exist: `CAST(NULL)` + reason "needs window metric"; never a constant, never dropped | `--feature F14` |
| F15 Non-additive | Closing balance -> plain aggregate + `NON ADDITIVE BY`; ratios/arithmetic over a non-additive metric must not inline it -> NULL + reason (or metric reference if supported) | `--feature F15` + pipeline (checks `NON ADDITIVE BY` in the DDL) |

Each failing case prints its **expected solution**. Values are computed from the synthetic data; the
runner simulates joins, enriched copies, inactive-relationship aliases and "value at the last period".

Loop per feature: `--selftest` (once) -> `--feature Fx` -> full run (0 REGRESSED) -> `--mutate 1` and `--mutate 2`
-> `run_pipeline_tests.py --feature Fx` -> `audit_hardcoding.py` -> regenerate DDLs + measure_inventory.
