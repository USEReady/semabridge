"""Unified Tier 5 prompt template.

Base (schema-grounding + rules) salvaged from connectors/translator.py's
_build_openai_dax_prompt (:699-756) — the only prompt among the existing
LLM call sites that actually includes real dataset_col_lookup-derived
schema text. Dialect is made a parameter here instead of being
Snowflake-only.

The few-shot examples and DAX-semantics/CALCULATE-translation rules below
are salvaged from converter/llm_dax_translator.py (:234-326) — the
highest-quality prompt content in the codebase, previously attached to a
class with zero callers. Appended as an additional section rather than
rewritten: the examples show Databricks-style backtick-quoted SQL as
originally written (that file targeted Databricks), not re-authored per
dialect. This is a known limitation to revisit, not a silent decision —
see the Step 1 report.

The SEMABRIDGE_LLM_SKILLS_DIR optional override mechanism is kept as an
extra layer on top of this now-populated default template, exactly as in
the original.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from semabridge.dax_translation.types import Dialect, TranslationRequest

_DIALECT_TARGET = {
    Dialect.SNOWFLAKE: "Snowflake Semantic View METRICS clause",
    Dialect.DATABRICKS: "Databricks SQL expression",
}

_DIALECT_SYSTEM_TARGET = {
    Dialect.SNOWFLAKE: "Snowflake Semantic View metric SQL",
    Dialect.DATABRICKS: "Databricks SQL metric expression",
}

# Found during Step 4 (Pipeline C) verification: the original rules text
# said "quote identifiers only when needed" without ever naming Databricks'
# actual quote character, so the LLM had no instruction to use backticks —
# it previously only worked by coincidence when the LLM omitted quoting
# entirely. Explicit per-dialect quote character, not a hardcoded example.
_DIALECT_QUOTING_RULE = {
    Dialect.SNOWFLAKE: (
        '- Quote identifiers only when needed as ALIAS."COLUMN"; uppercase Snowflake column names.'
    ),
    Dialect.DATABRICKS: (
        "- Quote identifiers only when needed using backticks like `alias`.`column`; snake_case Databricks column names."
    ),
}

# Added after a real incident: a rolling-N-month/day window translation
# (e.g. "trailing 12 months", "R12M") produced a date-arithmetic expression
# (DATEADD/DATE_ADDDAYSTODATE-shaped) compared directly against an INTEGER
# surrogate-key column (a MONTHINDEX/DATEID-shaped column), which Snowflake
# rejected and crashed the whole deploy DDL. connectors/type_safety_validator.py
# now catches this shape at DDL-emission time as a backstop, but the model
# should never produce it in the first place -- this rule plus the
# type-annotated schema context above (see _schema_text) are the
# prevention-side half of that fix. Kept dialect-agnostic and generic (no
# metric/column name hardcoded) -- any rolling-window pattern, any surrogate
# key, any dialect.
_ROLLING_WINDOW_RULE = (
    "- For rolling/trailing-window patterns (e.g. \"last N months\", \"R12M\", \"trailing period\"): "
    "check the schema context's column types. If the column you are comparing the window "
    "boundary against is declared INTEGER/NUMBER (a surrogate key like a month-index or "
    "date-id column, not DATE/DATETIME), compute the window boundary as an INTEGER "
    "expression in that same column's domain (e.g. arithmetic on YEAR(...)*12 + MONTH(...)) "
    "-- never produce a DATE-typed expression (DATEADD, DATE_ADDDAYSTODATE, TO_DATE, "
    "DATE_TRUNC, CURRENT_DATE, etc.) and then compare or combine it directly with an "
    "INTEGER/NUMBER column. Only compare a DATE-producing expression against a column "
    "the schema context marks as DATE or DATETIME."
)

# Salvaged verbatim from converter/llm_dax_translator.py:239-311 (the
# CALCULATE-translation-rules section, the DAX-function-mapping rules, and
# the 9 worked few-shot examples). Dialect-agnostic in intent; the example
# SQL syntax itself is Databricks-flavored as originally written.
_FEW_SHOT_SECTION = '''
═══════════════════════════════════════════
CALCULATE RULES (Very Important)
═══════════════════════════════════════════
CALCULATE(<expression>[, <filter1> [, <filter2> [, ...]]])
The first parameter <expression> is itself a measure. It can be ANY expression, not just SUM.

How to translate:
1. Identify the aggregation inside <expression>:
     SUM(col)          → SUM(CASE WHEN <filters> THEN col ELSE 0 END)
     COUNT(col)        → COUNT(CASE WHEN <filters> THEN col ELSE NULL END)
     MIN/MAX(col)      → MIN/MAX(CASE WHEN <filters> THEN col ELSE NULL END)
     DISTINCTCOUNT(col)→ COUNT(DISTINCT CASE WHEN <filters> THEN col ELSE NULL END)
     DIVIDE(a, b)      → COALESCE(SUM(CASE WHEN <f> THEN a ELSE 0 END) / NULLIF(SUM(CASE WHEN <f> THEN b ELSE 0 END), 0), 0)
     scalar/string     → ANY_VALUE(CASE WHEN <filters> THEN <expression> ELSE NULL END)
2. Filter predicates are ANDed inside the CASE WHEN condition.

═══════════════════════════════════════════
DAX-FUNCTION-MAPPING RULES
═══════════════════════════════════════════
1. Column references → qualified with the table alias and quoted column name.
2. Aggregations → preserve the original DAX function (SUM, AVG, COUNT, MIN, MAX). DAX AVERAGE is SQL AVG.
3. TODAY()  → MAX(current_date())
4. NOW()    → MAX(current_timestamp())
5. DIVIDE(num, den [, alt]) → COALESCE( (num) / NULLIF((den), 0), COALESCE(alt, 0) )
6. CONCATENATE(a, b) → CONCAT(a, b)
7. FORMAT(expr, fmt) → date_format(expr, fmt) (only for date columns)
8. IF(cond, true_val, false_val) → CASE WHEN cond THEN true_val ELSE false_val END
9. BLANK() → NULL
10. Time intelligence (SAMEPERIODLASTYEAR, TOTALYTD, etc.) → not supported directly here; prefer the deterministic AST renderer for these.
11. Measure references [Measure Name] → use the pre-computed column name if available, otherwise inline the resolved SQL (but avoid recursion).
12. String literals: DAX "text" → SQL 'text'
13. No SELECT, FROM, WHERE, GROUP BY – output only the expression.

═══════════════════════════════════════════
FEW-SHOT EXAMPLES
═══════════════════════════════════════════
DAX: TODAY()
SQL: MAX(current_date())

DAX: CONCATENATE("Last Refreshed: ", MAX('Sync Log'[Last Sync Timestamp]))
SQL: ANY_VALUE(CONCAT('Last Refreshed: ', DATE_FORMAT(`sync_log`.`last_sync_timestamp`, 'MM/dd/yyyy HH:mm:ss')))

DAX: SUM('Sales Aggregate'[Monthly Revenue])
SQL: SUM(`sales_aggregate`.`monthly_revenue`)

DAX: DIVIDE([Total Cost], [Total Revenue])
SQL: COALESCE(SUM(`sales_aggregate`.`total_cost_amt`) / NULLIF(SUM(`sales_aggregate`.`total_revenue_amt`), 0), 0)

DAX: CALCULATE(SUM('Sales Fact'[Order Quantity]), 'Region'[Region Name] = "West")
SQL: SUM(CASE WHEN `region`.`region_name` = 'West' THEN `sales_fact`.`order_quantity` ELSE 0 END)

DAX: CALCULATE(COUNTROWS('Customer'), Customer[City] = "London")
SQL: COUNT(CASE WHEN `customer`.`city` = 'London' THEN 1 ELSE NULL END)

DAX: CALCULATE(DISTINCTCOUNT('Product'[ID]), Product[Category] = "Electronics")
SQL: COUNT(DISTINCT CASE WHEN `product`.`category` = 'Electronics' THEN `product`.`id` ELSE NULL END)

DAX: IF(ISBLANK([Sales]), 0, [Sales])
SQL: COALESCE(sales, 0)
'''.strip()

# Added after this session's incident review: several of the real,
# documented Tier-5 failures (rolling-window day/month unit confusion,
# LAG()/window-function misuse, DATE-vs-INTEGER type mixing) were all cases
# where the model *could* have caught its own mistake before answering, if
# it had been explicitly told to look for that exact shape. This is a
# self-verification pass baked into the single prompt/response -- not a
# second LLM call, not a confidence score, not an agentic loop. It is
# deliberately positioned after the schema context and few-shot examples
# (so the model has already seen the column types and translation
# patterns) and before the actual DAX/metrics request is presented, so the
# checklist reads as "apply this to what you're about to be asked," not as
# a postscript the model can skim past. Kept dialect-agnostic and generic
# (no metric/column name hardcoded), and uniform across every provider --
# every adapter is handed the exact same built prompt string from
# service.py, so there is no provider-specific variant to keep in sync.
_VERIFICATION_CHECKLIST = '''
═══════════════════════════════════════════
BEFORE YOU ANSWER, VERIFY
═══════════════════════════════════════════
Before finalizing the SQL you are about to return, check it against each of these -- all four are real failure patterns seen in production:

1. Unit/type consistency: if the expression adds or subtracts a time period, is the unit (days vs. months vs. years) correct for what the metric name and DAX describe? A "rolling 12 months" / "R12M" / "trailing N months" pattern must use month-based (or the schema's native surrogate-key) arithmetic -- never day-based date arithmetic that happens to produce a numerically similar range.
2. Forbidden constructs: does this expression use only plain aggregate functions (SUM, AVG, COUNT, MIN, MAX, CASE WHEN, etc.)? Window functions (LAG, LEAD, ROW_NUMBER, OVER, PARTITION BY) and SELECT/FROM/JOIN/CTEs/subqueries are not valid inside a metric expression here, even if they would look correct in ordinary SQL. If the calculation seems to require one, re-express it as a plain aggregate instead -- if that is genuinely not possible, return CAST(NULL AS DOUBLE) rather than attempting a window function or subquery.
3. Type compatibility: is a DATE/DATETIME-typed value (per the schema context above) ever being compared against, joined with, or arithmetically combined with a column the schema context marks as INTEGER/NUMBER (e.g. a surrogate key, month-index, or date-id column)? These must never be mixed directly -- only compare a DATE-producing expression against a column the schema marks as DATE or DATETIME, and only do integer arithmetic against INTEGER/NUMBER-typed columns.
4. Scope/reachability: if the DAX filters by a dimension from a table other than the metric's own base table, check the "Tables reachable via a declared relationship" list (single-metric requests) or this metric's own "reachable_tables" field (batch requests). If the filtered table is listed there, you MAY reference its column directly using the alias shown -- e.g. that_alias."COLUMN" inside a CASE WHEN -- this is not a forbidden JOIN; Snowflake resolves the join itself via the semantic view's own declared relationship. If the filtered table is NOT listed there, its reachability is unconfirmed -- do not assume or invent a join path; prefer CAST(NULL AS DOUBLE) instead.

Only return your final SQL after checking all four.
'''.strip()

# The examples above are written in Databricks' backtick-quoted style (see
# the module docstring) but are appended regardless of the request's actual
# dialect — for a non-Databricks request this silently contradicts the
# dialect-specific quoting rule already given above it. Rather than
# hand-authoring a second, unverified set of examples per dialect, make the
# conflict explicit and tell the model which one to trust.
_FEW_SHOT_DIALECT_DISCLAIMER = (
    "Note: the examples below use Databricks-style backtick quoting to "
    "illustrate translation PATTERNS and CALCULATE/filter logic only. "
    "Follow the quoting and function-name conventions in the Rules above "
    "for the actual target dialect — do not copy the examples' literal "
    "backtick quoting or Databricks-specific function names verbatim."
)


def _few_shot_section(dialect: Dialect) -> str:
    if dialect == Dialect.DATABRICKS:
        return _FEW_SHOT_SECTION
    return f"{_FEW_SHOT_DIALECT_DISCLAIMER}\n\n{_FEW_SHOT_SECTION}"


def build_system_message(dialect: Dialect | str) -> str:
    dialect = Dialect.coerce(dialect)
    target = _DIALECT_SYSTEM_TARGET[dialect]
    return (
        f"You translate Power BI DAX measures to {target}. Return a single "
        'JSON object with keys "sql" (the SQL expression) and "confidence" '
        "(a number 0-1, your own estimate of how likely this exact SQL is "
        "to execute without a compile-time or runtime error). Return JSON "
        "only. Do not use markdown."
    )


def _load_skill_blocks() -> List[str]:
    skill_dir = os.getenv("SEMABRIDGE_LLM_SKILLS_DIR", "prompts")
    skill_blocks: List[str] = []
    try:
        base = Path(skill_dir)
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
        for name in (
            "system.md",
            "rules.md",
            "valid_examples.md",
            "invalid_examples.md",
            "rolling_windows.md",
            "calculate_filters.md",
            "schema_resolution.md",
            "time_intelligence.md",
        ):
            path = base / name
            if path.exists():
                skill_blocks.append(path.read_text(encoding="utf-8").strip())
    except Exception:
        pass
    return skill_blocks


def _schema_text(
    dataset_col_lookup: Dict[str, set],
    dataset_col_types: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """Render the schema-context block for the prompt. When
    dataset_col_types is available (see TranslationRequest.dataset_col_types
    / connectors/type_safety_validator.py's build_dataset_col_types), each
    column is annotated with its declared type -- e.g. "MONTHINDEX
    (INTEGER)" -- so the model can see which columns are DATE-typed vs.
    INTEGER/NUMBER-typed instead of guessing from the bare name alone. With
    no type info at all (dataset_col_types is None/empty, or a given
    dataset/column isn't in it), a column renders exactly as it always has
    -- bare name, no annotation -- so this is purely additive."""
    schema_lines = []
    for table, cols in sorted(dataset_col_lookup.items()):
        types_for_table = (dataset_col_types or {}).get(table, {})
        rendered_cols = []
        for col in sorted(cols)[:80]:
            col_type = types_for_table.get(col)
            rendered_cols.append(f"{col} ({col_type})" if col_type else col)
        schema_lines.append(f"- {table}: {', '.join(rendered_cols)}")
    return "\n".join(schema_lines[:40]) or "- <schema unavailable>"


def _reachable_tables_text(reachable_table_aliases: Optional[Dict[str, str]]) -> str:
    """Render the "safe to cross-reference" table list for a single-metric
    prompt. Empty/None renders an explicit "<none>" line rather than
    omitting the section entirely -- see _VERIFICATION_CHECKLIST item 4,
    which tells the model to treat an unlisted table as unconfirmed; an
    absent section could otherwise read as "not checked" rather than
    "checked, nothing reachable"."""
    if not reachable_table_aliases:
        return "- <none other than the default table above>"
    return "\n".join(
        f"- {table} (alias: {alias})" for table, alias in sorted(reachable_table_aliases.items())
    )


def build_prompt(request: TranslationRequest) -> str:
    """Build the unified Tier 5 user prompt: schema-grounded base rules
    (dialect-parameterized) + the salvaged few-shot/DAX-semantics section,
    with any external skill-file overrides layered on top."""
    dialect = Dialect.coerce(request.dialect)
    target = _DIALECT_TARGET[dialect]
    skill_blocks = _load_skill_blocks()
    schema_text = _schema_text(request.dataset_col_lookup, request.dataset_col_types)
    reachable_text = _reachable_tables_text(request.reachable_table_aliases)

    base = (
        f"Dialect: {target}\n"
        f"Metric name: {request.metric_name or 'unnamed'}\n"
        f"Default dataset: {request.dataset_name}\n"
        f"Default table alias: {request.table_alias}\n"
        "Rules:\n"
        "- Return only a single JSON object of the shape "
        '{"sql": "<the SQL expression>", "confidence": <a number 0-1, '
        'your own estimate of how likely this exact SQL is to execute '
        'without error>}, no markdown, no explanation outside the JSON.\n'
        "- Use table aliases and columns from the schema context when known.\n"
        "- Prefer aggregate expressions valid in a semantic-view/metric-view metric.\n"
        "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
        "- Do not nest aggregate functions like SUM(MAX(...)).\n"
        "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN measure_column ELSE 0 END).\n"
        f"{_ROLLING_WINDOW_RULE}\n"
        f"{_DIALECT_QUOTING_RULE[dialect]}\n"
        '- If a pattern is impossible in a metric expression, set "sql" to CAST(NULL AS DOUBLE).\n'
        "Schema context (column types shown in parentheses when known):\n"
        f"{schema_text}\n"
        "Tables reachable from the default dataset via a declared relationship "
        "(safe to reference directly using the alias shown -- no JOIN needed, "
        "Snowflake resolves it via the relationship):\n"
        f"{reachable_text}"
    )

    # Positioned after the schema context and few-shot examples, and before
    # the actual DAX request below -- see _VERIFICATION_CHECKLIST's comment.
    # The JSON-shape reminder is restated right next to the request itself
    # (same reason build_batch_prompt restates its response_shape next to
    # the metrics list) -- a format instruction stated once, several
    # sections earlier, is more likely to be dropped than one restated
    # immediately before the model has to produce output.
    final_request = (
        f"DAX:\n{request.dax}\n\n"
        "Respond with exactly one JSON object of this shape:\n"
        '{"sql": "<sql expression>", "confidence": <0.0-1.0>}'
    )

    sections = []
    if skill_blocks:
        sections.append("\n\n".join(b for b in skill_blocks if b))
    sections.append(base)
    sections.append(_few_shot_section(dialect))
    sections.append(_VERIFICATION_CHECKLIST)
    sections.append(final_request)
    return "\n\n".join(sections)


# Same checklist as build_prompt's, plus one line clarifying that "before
# you answer" means per-key here, since a batch response covers many
# metrics at once rather than one DAX expression.
_VERIFICATION_CHECKLIST_BATCH = (
    _VERIFICATION_CHECKLIST
    + "\n\nApply this checklist independently to every metric key above before including its SQL in your response."
)


def batch_key(index: int) -> str:
    """Synthetic, always-present, always-unique JSON key for one request's
    position in a batch — deliberately not request.metric_name, so a batch
    never depends on every metric having a name or on names being unique
    within the batch. The real name (when present) is still surfaced to
    the LLM via the "name" field on each metric line in build_batch_prompt,
    for its own context only."""
    return f"m{index}"


def build_batch_system_message(dialect: Dialect | str) -> str:
    dialect = Dialect.coerce(dialect)
    target = _DIALECT_SYSTEM_TARGET[dialect]
    return (
        f"You translate Power BI DAX measures to {target}. Return a single "
        'JSON object mapping each metric\'s "key" to an object of the '
        'shape {"sql": "<the SQL expression>", "confidence": <a number '
        "0-1, your own estimate of how likely this exact SQL is to "
        'execute without error>}. Return JSON only. Do not use markdown.'
    )


def build_batch_prompt(requests: List[TranslationRequest]) -> str:
    """One prompt for many metrics — the JSON-map response contract
    Pipeline B's original OpenAI batch-prefetch used
    (connectors/translator.py's _build_openai_batch_prompt /
    _parse_openai_batch_payload), generalized: dialect-parameterized like
    build_prompt(), and keyed by synthetic position (see batch_key) rather
    than by metric name.

    All requests in one call are assumed to share one dialect — every real
    caller only ever batches metrics from a single pipeline run, which is
    always single-dialect. The schema context is the union of every
    request's own dataset_col_lookup, since a batch can span metrics from
    more than one dataset.
    """
    if not requests:
        return ""

    dialect = Dialect.coerce(requests[0].dialect)
    target = _DIALECT_TARGET[dialect]
    skill_blocks = _load_skill_blocks()

    merged_schema: Dict[str, set] = {}
    merged_col_types: Dict[str, Dict[str, str]] = {}
    for req in requests:
        for table, cols in (req.dataset_col_lookup or {}).items():
            merged_schema.setdefault(table, set()).update(cols)
        for table, types_for_table in (req.dataset_col_types or {}).items():
            merged_col_types.setdefault(table, {}).update(types_for_table)
    schema_text = _schema_text(merged_schema, merged_col_types)

    metric_lines = []
    response_shape = {}
    for i, req in enumerate(requests):
        key = batch_key(i)
        dax_expr = " ".join((req.dax or "").split())
        metric_lines.append(json.dumps({
            "key": key,
            "name": req.metric_name or key,
            "dataset": req.dataset_name,
            "dax": dax_expr,
            # Per-metric, NOT merged across the batch like merged_schema
            # above -- different metrics in one batch can have different
            # base datasets, each with a different reachable-table set;
            # merging them would let the model assume a table reachable
            # for metric A is also reachable for metric B, which may be
            # false. Empty dict (not omitted) for the same "checked,
            # nothing reachable" reason as _reachable_tables_text.
            "reachable_tables": req.reachable_table_aliases or {},
        }))
        response_shape[key] = {"sql": "<sql>", "confidence": "<0.0-1.0>"}

    base = (
        f"Dialect: {target}\n"
        f"Default table alias: {requests[0].table_alias}\n"
        "Rules:\n"
        '- Return ONLY a single valid JSON object mapping each metric\'s "key" to an '
        'object {"sql": "<sql expression>", "confidence": <0.0-1.0>}.\n'
        "- No markdown, no extra keys, no prose.\n"
        "- Use table aliases and columns from the schema context when known.\n"
        "- Prefer aggregate expressions valid in a semantic-view/metric-view metric.\n"
        "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
        "- Do not nest aggregate functions like SUM(MAX(...)).\n"
        "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN measure_column ELSE 0 END).\n"
        "- Each metric below carries its own \"reachable_tables\" ({table: alias}) -- "
        "a table listed there for THAT metric may be referenced directly via the "
        "alias shown (no JOIN needed); a table not listed there (for that metric) "
        "has unconfirmed reachability -- see the verification checklist.\n"
        f"{_ROLLING_WINDOW_RULE}\n"
        f"{_DIALECT_QUOTING_RULE[dialect]}\n"
        '- If a pattern is impossible for one metric, set "sql" to CAST(NULL AS DOUBLE) for just that key.\n'
        "Schema context (column types shown in parentheses when known):\n"
        f"{schema_text}"
    )

    # Positioned after the schema context and few-shot examples, and before
    # the actual per-metric requests below -- see _VERIFICATION_CHECKLIST's
    # comment. Apply the checklist to EACH metric's key below individually.
    final_request = (
        "Metrics (one JSON object per line):\n"
        + "\n".join(metric_lines) + "\n"
        "Respond with exactly one JSON object of this shape:\n"
        + json.dumps(response_shape)
    )

    sections = []
    if skill_blocks:
        sections.append("\n\n".join(b for b in skill_blocks if b))
    sections.append(base)
    sections.append(_few_shot_section(dialect))
    sections.append(_VERIFICATION_CHECKLIST_BATCH)
    sections.append(final_request)
    return "\n\n".join(sections)


def _extract_sql_and_confidence(value: Any) -> Tuple[Optional[str], Optional[float]]:
    """Pull (sql, confidence) out of one candidate value -- either the
    requested {"sql":..., "confidence":...} shape, or a bare SQL string
    from a provider that ignored the output-format instruction (tolerated,
    same posture parse_batch_payload already takes toward partial key
    coverage: not everything a provider gets "wrong" about the response
    contract is a failed translation).

    confidence is None whenever it's missing or not a real number --
    never guessed or defaulted to a placeholder. See
    TranslationResult.llm_self_reported_confidence's docstring for why
    that value must stay display-only and how it differs from the
    existing translation_provider_confidence accept/reject gate.
    """
    if isinstance(value, dict):
        sql = value.get("sql")
        sql = sql if isinstance(sql, str) else None
        conf = value.get("confidence")
        confidence = (
            float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
        )
        return sql, confidence
    if isinstance(value, str):
        return value, None
    return None, None


def parse_structured_response(text: str) -> Tuple[Optional[str], Optional[float]]:
    """Parse a single-metric Tier 5 response into (sql, confidence).

    Tolerates a provider that ignores the {"sql":..., "confidence":...}
    contract and returns bare SQL text instead: sql=<the cleaned text>,
    confidence=None in that case -- exactly the value that would have
    been used before this parsing step existed. Only (None, None) means a
    genuinely empty response; an unparseable-as-JSON non-empty response is
    treated as bare SQL, not as failure, since ignoring a response-FORMAT
    instruction while still answering the actual translation question
    isn't the same failure as returning nothing.
    """
    from semabridge.dax_translation.tier5.adapters.base import strip_markdown_fences

    cleaned = strip_markdown_fences(text).strip()
    if not cleaned:
        return None, None
    normalized = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(normalized)
    except Exception:
        return cleaned, None
    return _extract_sql_and_confidence(parsed)


def parse_batch_payload(text: str) -> Optional[Dict[str, Any]]:
    """Parse a provider's batch response into {key: candidate}, where each
    candidate is either a string (a provider that ignored the per-key
    {"sql":..., "confidence":...} contract and returned bare SQL for that
    key -- tolerated) or a dict of that shape. Callers use
    _extract_sql_and_confidence() to read whichever shape a given key
    actually has.

    Returns None only for a genuinely malformed response where NOTHING
    could be recovered -- not valid JSON at all with no salvageable
    top-level entries either. That is the signal a caller should treat as
    this provider's whole batch attempt failing (try the next provider).
    A response that *does* parse (fully OR partially, see
    _salvage_partial_batch_json below) but only covers some of the
    requested keys is NOT malformed here — it is returned as-is, and it is
    the caller's job (Tier5Service.translate_batch) to decide whether
    partial key coverage is close enough to a real answer to accept per
    request, versus so sparse it should also be treated as a failed
    attempt.
    """
    from semabridge.dax_translation.tier5.adapters.base import strip_markdown_fences

    cleaned = strip_markdown_fences(text).strip()
    if not cleaned:
        return None
    cleaned = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        # Real incident: a 13-metric batch response was cut off mid-JSON
        # by the adapter's output-token cap (since fixed separately --
        # see Tier5Service._batch_max_tokens), and the whole-string
        # json.loads() above naturally fails on a response that never
        # closes its outer braces. Before discarding the whole chunk,
        # try to salvage whatever complete top-level "key": value entries
        # appear before the truncation point -- almost always most of the
        # batch, since a token-cap cutoff lands near the END of the
        # response, not the beginning.
        salvaged = _salvage_partial_batch_json(cleaned)
        return salvaged or None
    if not isinstance(parsed, dict):
        return None
    return {
        str(k): v for k, v in parsed.items()
        if isinstance(v, str) or isinstance(v, dict)
    }


def _salvage_partial_batch_json(text: str) -> Dict[str, Any]:
    """Best-effort recovery of complete top-level "key": value entries
    from a JSON object that failed to parse as a whole -- almost always
    because the response was cut off mid-object by an output-token cap,
    not because the model wrote genuinely invalid JSON from the start.

    Manually scans for the outermost '{' and walks forward pairing off
    complete "key": value entries (value is either a quoted string or a
    brace-balanced object, respecting escaped quotes inside both), each
    validated with its own json.loads() call. Stops at the first entry it
    can't complete -- exactly the truncation point -- and returns
    everything gathered before it. This is deliberately NOT a general
    JSON-repair library: it only needs to handle the one real shape this
    module ever emits (a flat object of string/object values), and a
    narrow hand-rolled scanner is easier to reason about here than
    pulling in and trusting a third-party lenient-JSON parser for
    something this contract-specific.

    Returns {} (never None) when nothing could be salvaged -- e.g. the
    response wasn't shaped like a JSON object at all, or was cut off
    before even the first entry completed. Callers treat {} the same as
    "unparseable".
    """
    result: Dict[str, Any] = {}
    start = text.find("{")
    if start == -1:
        return result

    i = start + 1
    n = len(text)

    def _skip_ws_and_commas(pos: int) -> int:
        while pos < n and text[pos] in " \t\r\n,":
            pos += 1
        return pos

    def _scan_string(pos: int) -> Optional[int]:
        """pos is at the opening quote. Returns the index just past the
        closing quote, or None if the string never closes (truncated)."""
        j = pos + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                return j + 1
            j += 1
        return None

    def _scan_object(pos: int) -> Optional[int]:
        """pos is at the opening '{'. Returns the index just past the
        matching closing '}', or None if it never balances (truncated)."""
        depth = 0
        j = pos
        while j < n:
            ch = text[j]
            if ch == '"':
                end = _scan_string(j)
                if end is None:
                    return None
                j = end
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return None

    while True:
        i = _skip_ws_and_commas(i)
        if i >= n or text[i] == "}":
            break
        if text[i] != '"':
            break  # not a well-formed "key": ... entry -- stop here
        key_end = _scan_string(i)
        if key_end is None:
            break  # truncated inside the key itself
        key_raw = text[i:key_end]
        i = _skip_ws_and_commas(key_end)
        if i >= n or text[i] != ":":
            break
        i = _skip_ws_and_commas(i + 1)
        if i >= n:
            break

        if text[i] == '"':
            value_end = _scan_string(i)
        elif text[i] == "{":
            value_end = _scan_object(i)
        else:
            # Bare literal (number/true/false/null) -- scan to the next
            # top-level ',' or '}'.
            j = i
            while j < n and text[j] not in ",}":
                j += 1
            value_end = j if j < n else None
        if value_end is None:
            break  # truncated inside the value -- this is the cutoff point

        value_raw = text[i:value_end]
        try:
            key = json.loads(key_raw)
            value = json.loads(value_raw)
        except Exception:
            break
        result[key] = value
        i = value_end

    return result
