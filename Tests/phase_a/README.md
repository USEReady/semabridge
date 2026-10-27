# Phase A: relationship inventory + report-only resolver (read-only)

Nothing in SemaBridge's source, generated DDL or Snowflake is changed.

| File | Purpose |
|---|---|
| `run_phase_a.py` | One command: inventory for all models, CSV + JSON baseline, root-cause report |
| `relationship_inventory.py` | .pbix relationships vs generated DDL, one outcome per relationship |
| `validate_semantic_view.py` | DDL parser + key / path checks (used by the inventory) |
| `relationship_resolver.py` | Report-only resolver: what SHOULD happen to every relationship (plan) |
| `semabridge_trace.py` | Runtime trace of `_build_relationships` (inputs, aliases, `relationship_target_alias`, emitted lines, resolver plan) |

## Run order (from the SemaBridge repo root, folder copied to `.\phase_a`)

1. Baseline with today's DDL (no trace yet):

       uv run python phase_a\run_phase_a.py

   Auto-pairs `output\debug\debug_generated_sql_<model>.sql` with the newest upload in
   `%LOCALAPPDATA%\Temp\semabridge\uploads`. Other locations: `--pbix-dir <folder>` (repeatable),
   `--ddl-dir <folder>`, or `--batch batch.csv` (columns `model,pbix,ddl`).

2. Start SemaBridge through the tracer (your usual command after `--`; no `--reload`, because a reloader
   starts a new process without the trace):

       uv run python phase_a\semabridge_trace.py -- -m uvicorn <your.app.module>:app --port 8000

   Then run a dry run for Sales & Returns, Human Resources and the other models.
   Trace is written to `output\trace\relationship_trace.jsonl`.

3. Run again; the report now shows the root-cause stage for each problem relationship:

       uv run python phase_a\run_phase_a.py

## Output (`phase_a_results\`)

- `inventory.csv` - every source relationship: outcome, key on referenced side, root-cause stage
- `summary.csv` - one row per model
- `baseline.json` - first run; later runs write `latest.json` and list changes vs baseline
- `phase_a_report.md` - summary table + per-relationship root cause + matching log lines

## Root-cause stages

| Stage | Meaning | Where to look next |
|---|---|---|
| DROPPED BEFORE BUILDER | Never reached `_build_relationships` | extraction / OSI->SML / relationship normalisation; SML `model.yaml` |
| DROPPED IN BUILDER | Reached the builder, no line emitted | alias resolution (`from_alias`/`to_alias`), `relationship_target_alias`, column validation |
| DROPPED AFTER BUILDER | Emitted by the builder, missing in saved DDL | sanitiser / remediation / pre-deploy check |
| EMITTED WITHOUT KEY | In DDL, referenced side has no PK/UNIQUE | key planning in `tables_clause_builder` |
| NO TRACE | Model not traced yet | run step 2 for that model |

## Regression tests (`tests\`)

| Suite | File | Checks |
|---|---|---|
| 1 Normalisation | `test_relationship_normalization.py` | inactive edges are not paths; inactive never dropped; same table pair + different columns both kept; exact duplicates removed once; real active diamond not dropped silently; plain star unchanged |
| 2 Key resolution | `test_key_resolution.py` | referenced fact column -> PK when verified unique; second referenced column -> UNIQUE; non-unique -> rejected with reason; unverifiable -> `rejected_unverified_key`; inactive -> alias, original table unchanged; composite keys; M:1 star needs no new keys |
| 3 Acceptance | `test_acceptance_ddl.py` | real DDL: no relationship worse than the Phase A baseline; static validator 0 FAIL |

Run against the reference rules (default):

    uv run python -m pytest phase_a\tests -q

Run against SemaBridge's real code: fill in `phase_a_adapters.py`, then

    $env:SEMABRIDGE_NORMALIZER = "phase_a_adapters:semabridge_normalize"
    uv run python -m pytest phase_a\tests\test_relationship_normalization.py -q

Acceptance on real DDL (after regenerating it):

    $env:ACCEPT_BATCH_CSV = "phase_a\batch.csv"; $env:ACCEPT_BASELINE = "phase_a_results\baseline.json"
    uv run python -m pytest phase_a\tests\test_acceptance_ddl.py -q

## Measures: where are they dropped or NULLed? (`measure_inventory.py`)

Compares every Power BI measure (from your model-wise Excel report, or the .pbix) with the generated DDL,
the run log and the SML model, and gives each measure one outcome (REAL_SQL / NULL_PLACEHOLDER / MISSING),
a reason (UNREACHABLE, NOT_TRANSLATABLE, COLUMN_NOT_RESOLVED, VALIDATION_GATE, CIRCULAR, CASCADED,
NO_EXPRESSION, BY_DESIGN, UNKNOWN) and the pipeline stage. `simple_but_null = yes` marks simple DAX that
still failed - check those first.

    # all models: finds output\debug\debug_generated_sql_<model>.sql, newest output\runs\**\<model>.log, sml model.yaml
    uv run python phase_a\measure_inventory.py --report "C:\Users\manoj.s\Downloads\PowerBI_Measures_Report_Model_wise.xlsx" --repo . --translate

    # one model, explicit files
    uv run python phase_a\measure_inventory.py --report <report.xlsx> --model "Life expectancy v202009" `
        --ddl output\debug\debug_generated_sql_Life_expectancy_v202009.sql `
        --log output\runs\proj-life-expect\<run>\Life_expectancy_v202009.log `
        --sml output\reverse\proj-life-expect\sml\model.yaml --translate

Output in `measure_results\`: measure_inventory.csv (one row per measure), measure_summary.csv (per model),
measure_extra_in_ddl.csv (metrics in the view that are not in the report), baseline.json (later runs list changes).

## Step 1 - are all measures extracted? (`measure_stage_diagnostic.py`)

Tracks every measure (and its table) through S1 raw model -> S2 pbixray -> S3 SemaBridge pbixray extraction ->
S4 TMSL -> S5 OSI -> S6 SML (what the translator receives) -> S7 generated DDL, and reports the FIRST stage where
each one disappears, plus the kind of table it lives on (normal / calculated / measure-only / hidden / auto-date).

    uv run python phase_a\measure_stage_diagnostic.py --pbix-dir "C:\Users\manoj.s\Downloads\PBIX Files"
    uv run python phase_a\measure_stage_diagnostic.py --pbix "<model>.pbix" --ddl output\debug\debug_generated_sql_<model>.sql

Output (`measure_stage_results\`): measure_stages.csv, table_stages.csv, stage_summary.csv.
