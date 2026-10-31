# SemaBridge — generic window-metric translation (ALL / REMOVEFILTERS / ALLEXCEPT)

## Facts proven in Snowflake (do not re-litigate)
- A semantic-view metric may contain window functions over **metric references**:
  `T.M / NULLIF(SUM(T.M) OVER (PARTITION BY EXCLUDING D.X), 0)` → exact (B1, 3/3).
- **Two windows in one metric** are accepted (P2, P3).
- Separate window metrics referenced by another metric are **rejected** (B2–B4).
- **Every dimension after `PARTITION BY EXCLUDING` must be requested in the query** (error 010277);
  queries without it — including "no dimensions" — fail.
- `AVG(avg_metric) OVER` and `SUM(rate_metric) OVER` give **wrong numbers** (P2W off by 0.27, P3W off by 2.8).
- Precomputed whole-table totals in an enriched view are wrong under any other filter (21.57 % vs 26.34 %).

## Goal
Every DAX measure that removes filters is emitted as exactly one of:
**WINDOW** (exact, needs its dimension in the query) · **PRECOMPUTE** (only for "ignore every filter") ·
**NULL + specific reason** (+ verified query when useful). Never silently wrong. Fully generic: no model,
table, column or measure names in code.

## 1. Classify the filter-removal argument(s)
For every `CALCULATE(<expr>, <args>)` (also inside VAR, DIVIDE, IF, arithmetic, measure references after inlining):

| Argument | Meaning | Route |
|---|---|---|
| `ALL(T[c])`, `REMOVEFILTERS(T[c])`, `ALLNOBLANKROW(T[c])` | remove filter on one column | WINDOW, exclusion = dimension of c |
| several column args (same or different tables) | union | WINDOW, exclusion = union |
| `ALL(T)` / `REMOVEFILTERS(T)` where T is a **dimension** table | remove filters on T | WINDOW, exclusion = **minimal** set (§3) |
| `ALL(F)` / `REMOVEFILTERS(F)` / `REMOVEFILTERS()` where F is the **fact** of the aggregated columns (or all tables) | ignore every filter | PRECOMPUTE in enriched view (`__SB_ALL_<agg>_<col>`), metric `MAX(F."__SB_…")`; if no enriched view → NULL + reason |
| `ALLEXCEPT(T, k1..)` | remove all filters of T's expanded table except k | WINDOW, exclusion = minimal set of T's groupable dims minus k; if empty → NULL + reason |
| `ALL(T)` where T has **no relationship path** to the fact | DAX no-op for this fact | plain metric (no window), log it |
| `ALLSELECTED(...)`, `KEEPFILTERS(ALL(...))`, `ALL` inside `FILTER(ALL(..), <row condition>)` with SELECTEDVALUE/EARLIER | depends on report selection / row context | NULL + reason |
| auto date hierarchy `T[Date].[Month]` / `.[Year]` / `.[Quarter]` | hierarchy level | map to a declared dimension of that level if it exists (date table column or derived dim); else NULL + reason |
| `RANKX`, `TOPN`, running totals (`<= MAX(date)`), `DATEADD` / `SAMEPERIODLASTYEAR` | not this feature | unchanged (existing reasons) |

## 2. Decompose the aggregate (never window a non-additive metric directly)
Let the filtered expression be `[M]` (after inlining measure references).

| `[M]` | WINDOW form (each base metric referenced by name) |
|---|---|
| `SUM(x)`, `COUNT(x)`, `COUNTROWS(T)`, `COUNTA` | `SUM(T.M) OVER (…)` |
| `MIN(x)` / `MAX(x)` | `MIN(T.M) OVER (…)` / `MAX(T.M) OVER (…)` |
| `AVERAGE(x)` | `SUM(T.M__SUM) OVER (…) / NULLIF(SUM(T.M__CNT) OVER (…), 0)` with base metrics `SUM(x)` and `COUNT(x)` |
| ratio `DIVIDE(A, B)` / `A / B` with additive A, B | `SUM(T.A::FLOAT) OVER (…) / NULLIF(SUM(T.B::FLOAT) OVER (…), 0)` |
| `DISTINCTCOUNT`, `MEDIAN`, `PERCENTILE`, closing balance (NON ADDITIVE BY), text, iterators over non-additive | NULL + reason "not decomposable for a window" |

Whole measure shapes:
- share: `DIVIDE([M], CALCULATE([M], ALL(..)))` → `T.M / NULLIF(<window of M>, 0)` (B1).
- `DIVIDE(..., ..., alt)` → `IFF(COALESCE(<den>, 0) = 0, alt, <num> / <den>)`.
- filtered numerator: `DIVIDE(CALCULATE([M], cond), CALCULATE([M], ALL(..)))` → base metric `M__COND` for the numerator, window over unfiltered `M`.
- fixed filter inside the removed part: `CALCULATE([M], ALL(D), T[s] = "x")` → base metric `M__S_X` (CASE WHEN) and window over it.
- arithmetic around it (`1 - share`, `[M] - CALCULATE([M], ALL(..))`, `IF(..)`) → keep the arithmetic, substitute the window term.
- comparison/benchmark (`CALCULATE(AVERAGE(x), ALL(Year))` shown next to per-year values) → same rules.

Base metrics required by a window (e.g. `M__SUM`, `M__CNT`) are emitted once, named
`<METRIC>__<PART>`, with COMMENT 'helper for <metric>'. Reuse an existing identical metric if present.

## 3. Exclusion list — minimal and declared (Snowflake 010277)
1. Only **declared dimension names** (exact emitted names, quoted; disambiguated names like
   `PRODUCTS."PRODUCTS_PRODUCTSEQ"` included). Undeclared → NULL + reason, never a raw column.
2. `ALL(T[c])` → exactly c. `ALL(Table)` / `ALLEXCEPT` → **the columns of that table the report layout uses
   together with this measure** (visual axis/legend/rows). Fallback when the layout has none: the table's main
   display column = a non-key, non-numeric text column (prefer one whose name matches the table/entity,
   else the first such column). Never keys/IDs/FKs, numeric attributes, hidden or helper (`__SB_`) columns.
3. Optional: one extra metric per additional report-used column of the same table: `<METRIC>_<COLUMN>`.
4. If no groupable dimension exists (measure only used on cards/totals) → NULL + reason
   "needs window metric but no grouping dimension is used" and a verified query (§5).

## 4. Emission
- B1 form only: window arguments are metric references; never inline `COUNT(*)` into `SUM(...) OVER`.
- Cast numerators/denominators of ratios to `::FLOAT`.
- Every WINDOW metric gets `COMMENT = '<what it is>. Must be queried with <required dims>.'`
- Every NULL gets a COMMENT with its specific reason (no "Snowflake does not support window functions" — false).
- Run all validators (nested aggregate, scalar check, pre-deploy check) on the **final** expression, after every
  rewrite. The pre-deploy check validates EXCLUDING names against DIMENSIONS and metric refs against METRICS.
- Remove the share-of-total precompute (`ALL_COUNT`, `_rerender_window_enriched_metrics`); keep enriched views
  for dimension copies, time-intelligence anchors and §1 PRECOMPUTE.
- Deploy remediation (NULL-ing a failing metric) must start from the **latest** DDL, not the original.

## 5. Verified queries (for §3.4 and as Cortex examples)
For each WINDOW metric emit one verified query grouped by its required dimension. For NULL-because-no-grouping
measures emit a query computing the window on the result:
`SELECT d, m, m / NULLIF(SUM(m) OVER (), 0) FROM SEMANTIC_VIEW(... DIMENSIONS d METRICS m)`.

## 6. Tests (all must pass; scores identical under --mutate 1/2; audit 0 HARD)
Feature cases (synthetic fixture, values from pandas with DAX semantics):
W1 ALL(col) share · W2 ALL(dim table) share (minimal exclusion) · W3 SUM base · W4 REMOVEFILTERS ·
W5 `/` instead of DIVIDE · W6 DIVIDE with alternate · W7 two ALL args · W8 ALLEXCEPT · W9 VAR form ·
W10 COUNTROWS without base measure · W11 `1 - share` · W12 filtered numerator · W13 ALL(fact) → PRECOMPUTE ·
W14 AVERAGE + ALL(col) → two windows · W15 ratio measure + ALL → decomposed · W16 MAX + ALL ·
W17 DISTINCTCOUNT → NULL · W18 ALLSELECTED → NULL · W19 RANKX unchanged · W20 fixed filter inside ALL part ·
W21 ALL on unrelated table → plain metric · W22 auto date hierarchy with/without matching dim ·
W23 ALL(table) whose only text column is a key → NULL · W24 measure used only on cards → NULL + verified query ·
W25 ALL of a related table's column via inactive relationship alias (exclude the alias dim).
Each evaluated with: grouped by the required dim; + year filter; + extra context dim. Expect error (not wrong
value) when the required dim is absent.

Gates:
1. `run_feature_tests.py` (0 REGRESSED), `--mutate 1`, `--mutate 2`, `audit_hardcoding.py` (0 HARD)
2. `test_window_metrics.py --part detector` (N1–N12) and `test_window_rules.py --pbix <m> --ddl <ddl>` for
   AI, Life Expectancy, Sales & Returns, Competitive Marketing, HR
3. `ddl_regression_diff.py --before baseline_ddl --after output/debug` → no REAL→NULL except documented
4. Deploy AI; `sf_window_patterns_check.py --repo .` → Part A "share dimension only" and "year filter" PASS
   (Design app 2021 = 26.34 %); Part B P1, P2, P3, P5, P6 PASS, P2W/P3W FAIL as expected,
   "dimension not in query" = EXPECTED ERROR.
Commit: "Window metrics: generic ALL/REMOVEFILTERS/ALLEXCEPT with decomposition and minimal exclusions".
