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
from typing import Dict, List, Optional

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
    return f"You translate Power BI DAX measures to {target}. Return only one SQL expression. Do not use markdown."


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


def build_prompt(request: TranslationRequest) -> str:
    """Build the unified Tier 5 user prompt: schema-grounded base rules
    (dialect-parameterized) + the salvaged few-shot/DAX-semantics section,
    with any external skill-file overrides layered on top."""
    dialect = Dialect.coerce(request.dialect)
    target = _DIALECT_TARGET[dialect]
    skill_blocks = _load_skill_blocks()
    schema_text = _schema_text(request.dataset_col_lookup, request.dataset_col_types)

    base = (
        f"Dialect: {target}\n"
        f"Metric name: {request.metric_name or 'unnamed'}\n"
        f"Default dataset: {request.dataset_name}\n"
        f"Default table alias: {request.table_alias}\n"
        "Rules:\n"
        "- Return only a single SQL expression, no explanation.\n"
        "- Use table aliases and columns from the schema context when known.\n"
        "- Prefer aggregate expressions valid in a semantic-view/metric-view metric.\n"
        "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
        "- Do not nest aggregate functions like SUM(MAX(...)).\n"
        "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN measure_column ELSE 0 END).\n"
        f"{_ROLLING_WINDOW_RULE}\n"
        f"{_DIALECT_QUOTING_RULE[dialect]}\n"
        "- If a pattern is impossible in a metric expression, return CAST(NULL AS DOUBLE).\n"
        "Schema context (column types shown in parentheses when known):\n"
        f"{schema_text}\n"
        "DAX:\n"
        f"{request.dax}"
    )

    sections = []
    if skill_blocks:
        sections.append("\n\n".join(b for b in skill_blocks if b))
    sections.append(base)
    sections.append(_few_shot_section(dialect))
    return "\n\n".join(sections)


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
        'JSON object mapping each metric\'s "key" to its translated SQL '
        "expression. Return JSON only. Do not use markdown."
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
        }))
        response_shape[key] = "<sql>"

    base = (
        f"Dialect: {target}\n"
        f"Default table alias: {requests[0].table_alias}\n"
        "Rules:\n"
        '- Return ONLY a single valid JSON object mapping each metric\'s "key" to its SQL expression.\n'
        "- No markdown, no extra keys, no prose.\n"
        "- Use table aliases and columns from the schema context when known.\n"
        "- Prefer aggregate expressions valid in a semantic-view/metric-view metric.\n"
        "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
        "- Do not nest aggregate functions like SUM(MAX(...)).\n"
        "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN measure_column ELSE 0 END).\n"
        f"{_ROLLING_WINDOW_RULE}\n"
        f"{_DIALECT_QUOTING_RULE[dialect]}\n"
        "- If a pattern is impossible for one metric, use CAST(NULL AS DOUBLE) for just that key.\n"
        "Schema context (column types shown in parentheses when known):\n"
        f"{schema_text}\n"
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
    return "\n\n".join(sections)


def parse_batch_payload(text: str) -> Optional[Dict[str, str]]:
    """Parse a provider's batch response into {key: sql}.

    Returns None only for a genuinely malformed response — not valid JSON,
    or not a JSON object at all. That is the signal a caller should treat
    as this provider's whole batch attempt failing (try the next
    provider). A response that *does* parse but only covers some of the
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
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str)}
