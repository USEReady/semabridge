# SemaBridge issue tests

One script per issue from *Relationship_Branch_Fixes_Report.docx*. Each script reads the **generated semantic view DDL**
(and the **.pbix** where needed) and reports PASS / FAIL / WARN / INFO. No table or column names are hard-coded.

Needs: `pip install pbixray` (for tests that read the .pbix).

```powershell
# all tests on one model
uv run python run_all.py --ddl output\debug\<model>.sql --pbix "<model>.pbix" [--log run.log] [--src src] [--ddl2 other_run.sql] [--converted N]
# one test
uv run python t16_dimension_fanout.py --ddl <model>.sql --pbix "<model>.pbix"
# only some tests, summary only
uv run python run_all.py --ddl <model>.sql --pbix "<model>.pbix" --only t05,t16,t25 --quiet
```

Exit code 1 if any test fails, so it can run in CI. WARN = check by hand (usually a value check against Power BI).

| Test | Report item | What it checks |
|---|---|---|
| t01_double_enriched_name | 1.1 | No table source ends with `_ENRICHED_ENRICHED` |
| t02_hierarchy_reference | 1.2 | Measures using `'T'[Col].[Level]` get SQL, without leftover `.[` |
| t03_var_failure | 1.3 | No "Failed to render variable"; NULL measures with unused VARs flagged |
| t04_quick_measure_error_guard | 1.4 | `IF(…, ERROR(…), calc)` measures are translated |
| t05_dateadd_amount | 1.5 | DATEADD amount (-2, -7…) and DAY kept in the SQL; unknown intervals not translated |
| t06_lastnonblank | 1.6 | LASTNONBLANK measures, if translated, use NON ADDITIVE BY (value check) |
| t07_hasonevalue | 1.7 | HASONEVALUE-guarded measures: value check on totals |
| t08_null_reasons | 1.8 | Every NULL measure has a specific reason; dependencies are named |
| t09_sql_size_and_wrapper | 2.1 | Metric size limit; no wrapper around COUNT; SUM ± SUM handles empty sides |
| t10_reason_text | 2.2 | Reasons not cut mid-word, no column catalogs, no quoted identifiers |
| t11_unneeded_enriched_view | 2.3 | `_ENRICHED` views add at least one column; measure-only tables not emitted |
| t12_reserved_words | 2.4, 3.3 | Snowflake reserved words (e.g. START) prefixed; ordinary words (e.g. SEX) not |
| t13_helper_columns_private | 2.5 | Helper columns/metrics PRIVATE; real Power BI columns public |
| t14_one_to_one_direction | 3.1 | 1:1 relationships point from the table holding the metrics |
| t15_unlinked_tables | 3.2 | No generated table without a relationship |
| t16_dimension_fanout | 3.4 | Dimension values not summed/counted on fact rows |
| t17_iteration_grain | 3.5 | AVERAGEX/SUMX over VALUES/DISTINCT not turned into a plain aggregate |
| t18_whole_table_all | 3.6 | `ALL(Table)` measures use a whole-table value, not a per-group aggregate |
| t19_bare_column_row_context | 3.7 | Bare `[col]` inside `FILTER(T, …)` resolved to T |
| t20_previousyear_semantics | 3.8 | PREVIOUSYEAR/PARALLELPERIOD not using the same-period-last-year flag |
| t21_retry_loop | 3.9 | Run log has no "pass N: fixing"; `--src` scan for remediation code |
| t22_run_report_counts | 3.10 | `--converted` from the run report equals metrics with SQL in the view |
| t23_view_name_stability | 3.11 | Two runs (`--ddl2`) produce the same view name |
| t24_blocked_by_helpers | 3.12 | Which NULL measures block the most others; leave-one-out status |
| t25_countrows_filter | regression guard | `COUNTROWS(FILTER(T,…))` keeps the filter and sits on T |
| t26_metric_column_name_clash | regression guard | No metric aggregating a name that is also a metric (010220) |
| t27_unshifted_fallback | regression guard | Time-shifted measures differ from their base; no CURRENT_DATE anchor |

Name matching between Power BI measures and DDL metrics uses the same normalisation as SemaBridge
(`%`→PCT, `#`→NUM, other symbols → `_`). If a measure shows as "missing from the view", check the name mapping first.
