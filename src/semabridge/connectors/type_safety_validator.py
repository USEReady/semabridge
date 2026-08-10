"""General type-safety check for metric SQL expressions before DDL emission.

Built after a real production incident: a Tier-5 (Anthropic) translation for
a rolling-12-month metric (R12M) produced
`DATE_ADDDAYSTODATE(NEGATE(12), SALESFACT.MAX_DATE)` compared/combined
against an INTEGER surrogate-key column (a MONTHINDEX-shaped column). Nothing
in the pipeline validated that a date-producing expression and an
integer-typed column were being combined, so the mismatch reached Snowflake
raw and crashed the whole `CREATE SEMANTIC VIEW` DDL statement, taking down
every other metric in the same deploy with it.

This is deliberately NOT keyed to DATE_ADDDAYSTODATE, R12M, or any other
specific function/metric name — it is driven by (a) a general list of
date-arithmetic functions/keywords that produce a DATE/TIMESTAMP result in
Snowflake or Databricks SQL, and (b) the dataset's own declared column types
(SMLColumn.data_type / OSIColumn.data_type, the same schema info
dataset_col_lookup is built from — see converter/dax_translator.py's
build_schema_lookup). Any future combination of a date-shaped expression and
a number-shaped column, from any provider (Tier 1-5) and any metric, is
caught the same way.

This is intentionally a lightweight, adjacency-based structural check, not a
full SQL parser: it looks for a date-producing function call immediately
followed or preceded (skipping whitespace) by a comparison/arithmetic
operator and a qualified column reference (alias.COL / alias."COL"), then
resolves that column's declared type. That is exactly the shape Snowflake's
own semantic-view compiler rejects (a scalar type mismatch in a
comparison/arithmetic expression), and exactly the shape the real incident
had. It does not attempt to resolve types through nested expressions,
subqueries, or metric-to-metric references — a false negative there just
means Snowflake's own compile-time check is the backstop, same as today;
this validator is an *additional* earlier gate, not a replacement for it.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Functions/keywords that produce a DATE or TIMESTAMP value in Snowflake or
# Databricks SQL. Deliberately excludes functions that extract a NUMBER out
# of a date (YEAR, MONTH, DAY, DATEDIFF, EXTRACT, ...) -- those are exactly
# the safe way to combine a date column with an integer, and must never be
# flagged here.
DATE_PRODUCING_FUNCTIONS = frozenset({
    "DATEADD", "DATE_ADDDAYSTODATE", "DATE_ADD", "DATE_SUB",
    "TO_DATE", "TRY_TO_DATE", "DATE_TRUNC", "DATEFROMPARTS",
    "DATE_FROM_PARTS", "LAST_DAY", "ADD_MONTHS", "NEXT_DAY",
    "TO_TIMESTAMP", "TRY_TO_TIMESTAMP", "TIMESTAMPADD",
})
# Bare keywords that are valid with or without trailing parentheses.
DATE_PRODUCING_ZERO_ARG = frozenset({
    "CURRENT_DATE", "CURRENT_TIMESTAMP", "SYSDATE", "GETDATE",
})

# Schema type tokens treated as "a number", covering both the engine-neutral
# SMLColumn/OSIColumn DataType enum values (lowercase: "integer", "decimal",
# "float") and raw live-schema type names (uppercase, e.g. Snowflake's
# INFORMATION_SCHEMA.COLUMNS.DATA_TYPE: "NUMBER", "BIGINT", ...) so this
# keeps working if a future caller feeds it live-schema types instead of/
# alongside the model-declared ones.
NUMERIC_TYPE_TOKENS = frozenset({
    "INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT",
    "NUMBER", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL",
})
# Schema type tokens treated as "a date/timestamp".
DATE_TYPE_TOKENS = frozenset({
    "DATE", "DATETIME", "TIMESTAMP",
    "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ",
})

_COMPARISON_OPS = ("<=", ">=", "<>", "!=", "=", "<", ">", "+", "-")
_OPS_PATTERN = "|".join(re.escape(op) for op in _COMPARISON_OPS)

# alias.COL or alias."COL" — same shape MetricSqlValidator's own patterns use.
_QUALIFIED_COL_TAIL_RE = re.compile(
    r'([A-Za-z_][A-Za-z0-9_]*)\."?([A-Za-z_][A-Za-z0-9_$]*)"?\s*(' + _OPS_PATTERN + r')\s*$'
)
_QUALIFIED_COL_HEAD_RE = re.compile(
    r'^\s*(' + _OPS_PATTERN + r')\s*([A-Za-z_][A-Za-z0-9_]*)\."?([A-Za-z_][A-Za-z0-9_$]*)"?'
)


def build_dataset_col_types(datasets: Any) -> Dict[str, Dict[str, str]]:
    """Build a {dataset_name: {SANITIZED_COLUMN: TYPE_TOKEN}} map from
    SML/OSI dataset objects — the type-carrying sibling of
    DAXTranslator.build_schema_lookup() (converter/dax_translator.py), which
    keeps only column *names* and throws the declared data_type away.

    General over any dataset/column shape that has .unique_name and
    .columns[].unique_name/.data_type — not hardcoded to any specific
    dataset or column name. Never raises: a column with no resolvable type
    is simply omitted rather than failing the whole lookup.
    """
    from semabridge.utils.naming import sanitize_column

    col_types: Dict[str, Dict[str, str]] = {}
    for ds in datasets or []:
        ds_name = getattr(ds, "unique_name", None)
        if not ds_name:
            continue
        types_for_ds: Dict[str, str] = {}
        for col in getattr(ds, "columns", None) or []:
            col_name = getattr(col, "unique_name", None)
            if not col_name:
                continue
            raw_type = getattr(col, "data_type", None)
            token = getattr(raw_type, "value", raw_type)
            if not token:
                continue
            sanitized = sanitize_column(col_name, force_uppercase=True)
            types_for_ds[sanitized] = str(token).upper()
        col_types[ds_name] = types_for_ds
    return col_types


def _find_date_function_spans(sql: str):
    """Yield (start, end) spans covering each date-producing function call
    (including its balanced argument parens) or bare zero-arg keyword."""
    names = sorted(DATE_PRODUCING_FUNCTIONS, key=len, reverse=True)
    call_pattern = re.compile(r'\b(' + "|".join(re.escape(n) for n in names) + r')\s*\(', re.IGNORECASE)
    for m in call_pattern.finditer(sql):
        depth = 1
        i = m.end()
        while i < len(sql) and depth > 0:
            if sql[i] == '(':
                depth += 1
            elif sql[i] == ')':
                depth -= 1
            i += 1
        yield m.start(), i

    zero_arg_names = sorted(DATE_PRODUCING_ZERO_ARG, key=len, reverse=True)
    bare_pattern = re.compile(
        r'\b(' + "|".join(re.escape(n) for n in zero_arg_names) + r')\b(\s*\(\s*\))?', re.IGNORECASE
    )
    for m in bare_pattern.finditer(sql):
        yield m.start(), m.end()


def _resolve_column_type(
    alias: str,
    col_name: str,
    dataset_aliases: Dict[str, str],
    dataset_col_types: Dict[str, Dict[str, str]],
) -> Optional[str]:
    alias_to_dataset = {v: k for k, v in (dataset_aliases or {}).items()}
    dataset_name = alias_to_dataset.get(alias) or alias_to_dataset.get(alias.upper()) or alias_to_dataset.get(alias.lower())
    if not dataset_name:
        # Also accept the raw dataset name used directly as a qualifier.
        dataset_name = alias if alias in (dataset_col_types or {}) else None
    if not dataset_name:
        return None
    return (dataset_col_types.get(dataset_name) or {}).get(col_name.upper())


def detect_date_numeric_type_mismatch(
    sql: str,
    dataset_aliases: Dict[str, str],
    dataset_col_types: Dict[str, Dict[str, str]],
) -> Optional[str]:
    """Returns a clear, human-readable reason string if `sql` combines a
    date-producing expression directly against a column declared as a
    numeric type (INTEGER/NUMBER/DECIMAL/FLOAT/...) — the same shape of
    mismatch that crashed a real Snowflake DDL deploy. Returns None if no
    such mismatch is found (does NOT mean the SQL is otherwise valid — this
    is one targeted check, not a general SQL validator).
    """
    if not sql or not dataset_col_types:
        return None

    for start, end in _find_date_function_spans(sql):
        # Column immediately before the date expression: "<alias.col> <op> <date-fn>"
        head_match = _QUALIFIED_COL_TAIL_RE.search(sql[:start])
        if head_match:
            alias, col_name, _op = head_match.groups()
            col_type = _resolve_column_type(alias, col_name, dataset_aliases, dataset_col_types)
            if col_type in NUMERIC_TYPE_TOKENS:
                return (
                    f"Type mismatch: expression produces DATE but is compared against "
                    f"an INTEGER column ('{alias}.{col_name}', declared type {col_type})"
                )

        # Column immediately after the date expression: "<date-fn> <op> <alias.col>"
        tail_match = _QUALIFIED_COL_HEAD_RE.match(sql[end:])
        if tail_match:
            _op, alias, col_name = tail_match.groups()
            col_type = _resolve_column_type(alias, col_name, dataset_aliases, dataset_col_types)
            if col_type in NUMERIC_TYPE_TOKENS:
                return (
                    f"Type mismatch: expression produces DATE but is compared against "
                    f"an INTEGER column ('{alias}.{col_name}', declared type {col_type})"
                )

    return None
