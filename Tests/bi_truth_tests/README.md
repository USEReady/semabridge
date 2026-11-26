# BI Truth test pack

Covers the comparison engine, time windows on a case, the deploy gate, and an
end-to-end pass against a running API.

Nothing here names a real project, measure or table. Nothing creates a database,
a session or a warehouse connection: if a test ever needs one, the module under
test is reaching somewhere it should not, and that is the finding.

## Running

Against the reference implementation bundled here, which proves the suite is self
consistent:

    pytest bi_truth_tests -q
    # 55 passed

Against the product:

    SB_BT_IMPL=product pytest bi_truth_tests -q

Module overrides when paths differ:

    SB_BT_COMPARISON_MODULE   default semabridge.ai.bi_truth_comparison
    SB_BT_WINDOWS_MODULE      default semabridge.ai.bi_truth_windows
    SB_BT_GATE_MODULE         default semabridge.ai.bi_truth_gate

End to end, against a running server:

    SB_BT_API_BASE=http://localhost:8002 \
    SB_BT_PROJECT=<project id> \
    SB_BT_TOKEN=<bearer token> \
    pytest bi_truth_tests/test_e2e_api.py -v

Add `SB_BT_RUN=1` to actually execute the suite against the warehouse. It is off
by default because a real run takes minutes.

Without `SB_BT_API_BASE` the end-to-end file reports as not run, which is
accurate; it never passes without a server.

## About the PBIX files

This pack does not read PBIX files, and deliberately so. A test that depends on a
particular report cannot be run by anyone else and breaks when the report changes.

The PBIX files belong one layer up: use them to **create** cases through
`POST /api/bi-truth/generate`, then point the end-to-end file at that project.
The generated cases are the input; these tests check that the engine treats them
correctly.

If you want a repeatable set of cases from a PBIX file, export them once with the
cases endpoint and keep the JSON beside the pack. Do not import the PBIX itself.

## What the product must expose

`bi_truth_comparison`: `compare_values(expected, actual, tolerance_pct, floor,
expected_row_count, actual_row_count, context)` returning an object with
`verdict`, `variance_pct`, `used_absolute_rule`, `reason` and `context`;
`not_comparable(reason, context)`; `summarise(results)`; and a `Verdict` with
`PASS`, `FAIL`, `ROWS_DIFFER` and `NOT_COMPARABLE`.

`bi_truth_windows`: `parse_case_windows(raw)` returning `CaseWindow` objects with
`window_key`, `window_label`, `filter_clause`, `expected_value` and
`has_expected_value`; `build_windowed_sql(sql, filter_clause)`;
`is_semantic_view_query(sql)`.

`bi_truth_gate`: `check_deploy_gate(critical_measures, latest_run, mode,
max_age_hours, now)` returning a verdict with `status`, `mode`, `reason`,
`action`, `findings`, `blocks_deploy` and `to_dict()`;
`gate_could_not_evaluate(reason, mode)`; `GateMode`, `GateStatus`, `GateFinding`.

A missing name fails with that name rather than skipping, because a missing name
is a gap in the contract.

## Files

| File | Covers |
| --- | --- |
| `test_comparison.py` | Tolerance, the three null rules, the per-measure floor, row counts, not-comparable, and that every verdict explains itself |
| `test_windows.py` | Parsing, building windowed SQL, and refusing to filter a semantic view query |
| `test_gate.py` | Report mode never blocks, block mode blocks only when configured, stale and incomplete runs, and that the gate never executes the suite |
| `test_e2e_api.py` | The live routes: cases, windows, run history, verdicts, counts and capture dates |
| `_ref/` | A working reference implementation, so the suite is proven rather than plausible |

## The cases that matter most

- `test_1_6_null_against_null_passes`: today both nulls return a failure, so an
  empty quarter reads as a failure. With windows that fires constantly.
- `test_1_11_the_floor_is_in_the_measures_own_units`: the current
  `max(1.0, abs(expected))` gives a fixed allowance of 0.001 in raw units, which
  is ten percent for a ratio stored as 0.01 and a tenth of a cent for a currency
  total.
- `test_1_13_a_matching_value_with_differing_rows_is_not_a_pass`:
  `expected_row_count` exists on the case and nothing reads it.
- `test_2_14_a_semantic_view_query_is_refused_rather_than_filtered`: appending a
  WHERE to a semantic view query produces SQL that runs and returns the
  unfiltered number.
- `test_3_1_report_mode_never_blocks_whatever_the_verdict`: no deploy is stopped
  unless a project is explicitly configured to block.
