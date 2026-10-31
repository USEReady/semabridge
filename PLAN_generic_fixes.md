# Generic fixes G1–G10 — what to do

Sales & Returns is the first test case; every fix is a general rule and must not mention any model,
table, column or measure name in `src/`.

## 0. Before starting
1. Commit the current state (safe point): `git add -A; git commit -m "checkpoint before G1-G10"`.
2. Save baselines: copy `output/debug/*.sql` for AI, HR, Employee Hiring, Life Expectancy and Sales & Returns
   to `baseline_ddl/`.
3. Record the current result: `uv run python test_generic_fixes.py ddl --pbix <S&R>.pbix --ddl <S&R>.sql --log <deploy log>`
   (today: 10 FAIL, 8 WARN).

## 1. Quick wins (do these)

| Id | Rule | Where | Done when |
|---|---|---|---|
| G1 | A measure from a measure-only table is anchored on the fact whose columns it aggregates. Never on an enriched copy (`<OTHER>_<COL>`) or on the table of an excluded dimension | anchoring / re-anchoring in `ddl_builder.py` | no `G1` lines |
| G2 | A window metric is on the same table as the metrics inside its `OVER(...)`; pre-deploy check enforces it (Snowflake 010263) | window emission + `_validate_ddl_against_information_schema` | no `G2` lines, no 010263 at deploy |
| G3 | `CALCULATE([X], ALL…)` where `[X]` already contains a window (directly or through measures) → `CAST(NULL AS DOUBLE)` + 'nested window over a non-additive measure'. No helpers that sum dimension columns | window classifier in `dax_ast_parser.py` | no `G3` lines |
| G4 | `SELECTEDVALUE` over a table with no relationships and no default = `NULL` (BLANK). In `+`, `-` and comparisons wrap in `COALESCE(…, 0)`; in `*` and `/` keep NULL. Same rule on every path — never 'not reachable' | parameter resolution (all translation paths) | no `G4` FAIL; no `* (0)` |
| G5 | One `DIVIDE` implementation (AST `_render_divide`); remove the regex DIVIDE path. Non-zero literal denominator → plain division (no `IFF(COALESCE(100,0)=0…)`, no `/ NULLIF(100,0)`) | `dax_translator.py` fast path, `_render_divide`, `_render_binary` | no `G5` lines |
| G6 | Window over a wrapped ratio: decompose the inner ratio, keep the outer arithmetic (`ROUND(... * 100, 0) / 100`) | window decomposition | no `G6` lines |
| G7 | A measure that depends on a NULL measure gets `depends on [X]: <X's reason>`; every NULL has a COMMENT | dependency resolution + metrics clause | no `G7` lines |
| G8 | Deploy remediation: COMMENT = Snowflake error text; remediated metrics listed in the run result; test mode fails the run | `snowflake_emitter.py` remediation | `--log` shows no `G8` lines after the generator fixes |

Expected SQL (Sales & Returns):
```sql
SALES.RETURN_RATE AS ROUND(IFF(COALESCE(SUM(SALES.AMOUNT::FLOAT), 0) = 0, 0,
    SUM(CASE WHEN SALES.STATUS = 'Returned' THEN SALES.AMOUNT ELSE 0 END::FLOAT) / SUM(SALES.AMOUNT::FLOAT)) * 100, 0) / 100
SALES.TOTAL_RETURN_RATE AS ROUND(SUM(SALES.RETURN_RATE_NUM::FLOAT) OVER (PARTITION BY EXCLUDING CALENDAR.MONTH)
    / NULLIF(SUM(SALES.RETURN_RATE_DEN::FLOAT) OVER (PARTITION BY EXCLUDING CALENDAR.MONTH), 0) * 100, 0) / 100
SALES.WIF_ADJUSTED_UNITS_RETURNED AS SUM(SALES.UNIT::FLOAT) * NULL
SALES.WIF_TOTAL_FORECAST AS CAST(NULL AS DOUBLE) COMMENT = 'nested window over a non-additive measure'
```

## 2. Optional (only if time allows)

| Id | Rule | Unlocks (S&R) |
|---|---|---|
| G9 | `ALLSELECTED(T[c])` → window excluding c (query WHERE = selection) | WIF Units Returned_1/_2 → Difference, WIF Adjusted Sales, Profit Difference |
| G10 | `CALCULATE(DISTINCTCOUNT(T[c]), ALL(T))` → whole-table constant (precompute) | WIF Units Returned Average → WIF Units Returned |

## 3. Leave as NULL (correct)
Text measures (indicators, labels), RANKX/ISINSCOPE ranking, constant URLs — each with its reason.

## 4. Gates (all must pass before "done")
1. `uv run pytest Tests -q --ignore=Tests/Integration` → 0 failed (any changed assertion listed with a reason)
2. `run_feature_tests.py` normal, `--mutate 1`, `--mutate 2` → 0 REGRESSED; `audit_hardcoding.py` → 0 HARD
3. `test_generic_fixes.py ddl` on AI, HR, Employee Hiring, Life Expectancy, Sales & Returns → 0 FAIL
   (WARN allowed only for G9/G10)
4. `ddl_regression_diff.py --before baseline_ddl --after output/debug` → no unexplained REAL → NULL in any model
5. Deploy Sales & Returns through the UI (not a script); the deploy log has no `pass N: fixing` lines
6. `test_generic_fixes.py sf --repo . --view SEMABRIDGE.SALES_AND_RETURNS.SALES__RETURNS_SAMPLE_V201912_SEMANTIC`
   → Net Sales by month, Return Rate by month, Total Return Rate, WIF Profit = 0 all PASS

## 5. Expected result for Sales & Returns
| | Before | After G1–G8 | + G9–G10 |
|---|---|---|---|
| Correct real metrics | ~24 | ~28 | ~34 |
| Wrong real metrics | 2–3 | 0 | 0 |
| NULL without reason | ~15 | 0 | 0 |

## 6. Working rules for the agent
- Edit files directly; no patch scripts or `python -c` string replacement.
- One fix → one small commit with its test.
- After each change: `py_compile`, the gates above, dry run of all five models.
- Never change a test expectation without writing why.
