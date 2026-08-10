## Rolling / trailing-window patterns (R12M, trailing-N-month, etc.)

DAX measures that describe a rolling or trailing window — "last 12 months",
"trailing 3 months", "R12M", "rolling quarter" — are one of the most
common sources of a type-mismatch bug: the model computes a *date* boundary
(via DATEADD, a Snowflake function like DATE_ADDDAYSTODATE, TO_DATE,
DATE_TRUNC, CURRENT_DATE, etc.) and then compares that date directly against
a column that is not actually a date — most often an INTEGER surrogate key
such as a month-index or date-id column used for fast range filtering.
Snowflake's semantic-view compiler rejects that comparison outright and the
DDL statement fails to deploy, which can take down every other metric in
the same deploy along with it.

**Before writing the window-boundary comparison, check the schema context's
column types (shown in parentheses next to each column name):**

- If the column you are comparing against is `DATE` or `DATETIME` — a
  date-arithmetic boundary is correct. Compare date to date.
- If the column you are comparing against is `INTEGER` or `NUMBER` (a
  surrogate key, not a real date column) — compute the window boundary as
  an **integer expression in that same domain**, never as a date. For a
  month-index-shaped column (an integer that increases by 1 each calendar
  month), the boundary for "last N months" is typically:

  ```
  <month_index_column> > ((YEAR(CURRENT_DATE()) * 12) + MONTH(CURRENT_DATE()) - N)
  ```

  not:

  ```
  <month_index_column> > DATEADD(MONTH, -N, CURRENT_DATE())   -- WRONG: DATE compared to INTEGER
  ```

**Never mix the two domains.** A date-producing function's result
(DATEADD/DATE_ADDDAYSTODATE/TO_DATE/DATE_TRUNC/CURRENT_DATE/etc.) must only
be compared, joined, or otherwise combined with a column the schema context
marks as DATE or DATETIME. If you are not sure which domain a comparison
column belongs to, check its declared type in the schema context first —
never guess from the column's name alone (e.g. a column named `...DATE`
that is declared INTEGER is still an integer for this purpose).

If a rolling-window pattern's target column type can't be determined from
the schema context at all, prefer returning `CAST(NULL AS DOUBLE)` over
guessing — an untranslated metric is recoverable (it is dropped and
recorded, not silently wrong); a type-mismatched expression that reaches
Snowflake can crash the entire deploy.
