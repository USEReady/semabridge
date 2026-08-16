"""Verbatim-salvaged schema-aware SQL validator.

Moved unmodified from connectors/translator.py's MetricExpressionTranslator
(:98-111, :29-47, :49-59, :784-1486 in the source file this was copied
from). This is the one genuinely tested, schema-aware validation logic in
the whole codebase — MetricSqlValidator's method bodies below are copied
unchanged from the original; only the surrounding class name and module
location have moved, so it can be shared by every Tier 5 call site instead
of existing only inside connectors/translator.py.

The original connectors/translator.py is untouched (per the migration's
guardrail — nothing is deleted or altered there yet). This is an additive
copy, not a move of the original.

The class needed a larger transitive closure of helper methods than the
handful named in the original consolidation design doc
(_validate_metric_column_references, _normalize_metric_column_references,
_resolve_column_name_for_dataset, _resolve_metric_reference_name,
_heal_unknown_alias, _rewrite_window_metric_expression) — reading the
actual body of _normalize_metric_column_references showed it also calls
_qualify_bare_column_identifiers, _route_qualified_metric_owner_refs,
_normalize_display_name_metric_references, _repair_bare_aggregate_identifiers,
_build_safe_sum_sql, _qualify_bare_metric_references,
_quote_bare_metric_references, _rewrite_metric_aggregate_wrappers,
_normalize_date_part_arguments, _normalize_rolling_monthindex_max_predicates,
_qualify_bare_partition_identifiers, _dedupe_qualified_column_tokens, and
_pick_preferred_aggregate_column. All of those had to move together as one
unit too, or the salvaged public methods would raise AttributeError the
first time they're actually exercised. All of them are copied verbatim
below, unchanged, for the same reason as the six originally named.

self._id (an IdentifierSanitizer) is not salvaged — it was already a
standalone, general, importable class (semabridge.utils.identifiers).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Set, Tuple

from semabridge.utils.logger import get_logger
from semabridge.utils.identifiers import IdentifierSanitizer

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Verbatim shape-check helpers (were @staticmethod on MetricExpressionTranslator)
# connectors/translator.py:29-47, :49-59, :98-111
# ---------------------------------------------------------------------------

def fix_common_llm_issues(sql: str, dax: str = "") -> str:
    if not sql: return sql
    # Keep CURRENT_DATE() — Snowflake semantic views accept it natively.
    # Do NOT replace with MAX_DATE (a synthetic enriched-view column that
    # is not in scope inside semantic view metric expressions).
    # sql = sql.replace("CURRENT_DATE()", "MAX_DATE")   # removed
    # sql = sql.replace("CURRENT_DATE", "MAX_DATE")     # removed
    # Remove bare ALIAS placeholders that LLMs occasionally emit
    sql = re.sub(r'\bALIAS\."?[A-Z_][A-Z0-9_]*"?', '', sql, flags=re.IGNORECASE).strip()

    return sql


def _dax_divide_lost_its_division(dax: str, sql: str) -> bool:
    """True if `dax` calls DIVIDE(...) as a function but `sql` contains
    no division operator — a known LLM failure mode where the numerator
    survives but the denominator is silently dropped. Keyed purely on
    DAX/SQL grammar shape (a DIVIDE call vs. an absent '/'), not on any
    specific measure name, so it catches this failure for any DIVIDE
    expression rather than one hardcoded pair of measures."""
    if not re.search(r"\bDIVIDE\s*\(", dax or "", re.IGNORECASE):
        return False
    return "/" not in (sql or "")


def _is_scalar_metric_sql(expr: str, dialect: str = "snowflake") -> bool:
    """Shape check applied to every Tier 5 candidate, for every provider.

    OVER/PARTITION BY are forbidden for Snowflake — semantic-view METRICS
    clauses don't support window functions (see metrics_clause_builder.py's
    and snowflake_metric_sql.py's own copies of this same guard, and
    dax_ast_parser.py's DaxSqlRenderer, which fails closed rather than
    emit one for ALLEXCEPT/ALL). This check previously omitted that guard
    entirely, salvaged from a copy that never had it — a real gap: a real
    LLM could plausibly suggest a window-function "fix" for an
    ALLEXCEPT-shaped metric and have it wrongly accepted. Databricks
    measure expressions legitimately support window functions (see
    databricks_measure_translation.py's own OVER/PARTITION BY usage), so
    the guard is dialect-gated, not blanket.
    """
    if not expr:
        return False
    if not _has_balanced_parentheses(expr):
        # Added after a real incident: a batch response salvaged from a
        # provider that emitted an unescaped quote mid-value (prompt.py's
        # _salvage_partial_batch_json necessarily stops scanning right at
        # the corruption point) can produce a syntactically incomplete
        # fragment like "SUM(CASE WHEN x=" -- no forbidden keyword above
        # catches that, and it has no qualified column reference for the
        # schema-existence check to reject either, so it was silently
        # accepted as a "successful" candidate. Any genuinely complete,
        # correct SQL expression this pipeline produces has balanced
        # parens; this is a cheap, general structural-completeness check,
        # not a salvage-specific patch.
        return False
    upper = f" {expr.upper()} "
    forbidden = [
        " SELECT ",
        " FROM ",
        " JOIN ",
        " WITH ",
        " UNION ",
        ";",
    ]
    dialect_value = getattr(dialect, "value", str(dialect)).lower()
    if dialect_value != "databricks":
        forbidden.extend([" OVER ", " PARTITION BY "])
    return not any(token in upper for token in forbidden)


def _has_balanced_parentheses(expr: str) -> bool:
    depth = 0
    for ch in expr:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


# ---------------------------------------------------------------------------
# Verbatim salvage: MetricExpressionTranslator's schema-validation and
# SQL-repair methods (connectors/translator.py:1042-1486, :784-1040).
# Method bodies unchanged; only `self._id` construction differs (an
# IdentifierSanitizer is created here rather than injected by the emitter).
# ---------------------------------------------------------------------------

# Mirrors connectors/translator.py's _DAX_QUALIFIED_COLUMN_PATTERN exactly
# (kept in sync deliberately, same as every other method in this file) --
# see MetricExpressionTranslator._extract_dax_table_hints's docstring for
# the real-incident motivation: Tier-5 output can be a fully "valid"-looking
# alias.column reference (real alias, real column there) that still
# disagrees with the table the source DAX explicitly named.
_DAX_QUALIFIED_COLUMN_PATTERN = re.compile(r"'([^']+)'\[([^\]]+)\]|\b([A-Za-z_][\w ]*)\[([^\]]+)\]")


class MetricSqlValidator:
    """Schema-aware metric SQL validator and repair pass — verbatim salvage."""

    def __init__(self, identifier_sanitizer: Optional[IdentifierSanitizer] = None) -> None:
        self._id = identifier_sanitizer or IdentifierSanitizer()

    def _heal_unknown_alias(
        self,
        table_alias: str,
        col_name: str,
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, set],
        metric_names: Optional[set] = None,
    ) -> Optional[str]:
        """
        Dynamically heal unknown or mismatched table aliases to their correct dataset names.

        Resolved structurally — by checking which real dataset actually
        declares `col_name` — never by pattern-matching `table_alias`'s
        (already-unresolved) spelling.
        """
        sanitized_col_name = self._id.sanitize_column(col_name) if self._id else col_name.upper()

        # 1. If col_name is a known metric, treat alias as a dummy and let metric resolution happen
        if metric_names:
            resolved_metric = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
            if resolved_metric:
                if dataset_aliases:
                    return next(iter(dataset_aliases.keys()))

        # 2. Resolve via real schema: which dataset actually declares this column?
        owners = [
            ds_name for ds_name, cols in dataset_col_lookup.items()
            if self._resolve_column_name_for_dataset(cols, sanitized_col_name)
        ]
        if len(owners) == 1:
            return owners[0]

        return None

    def _qualify_bare_column_identifiers(
        self,
        metric_sql: str,
        dataset_col_lookup: Dict[str, set],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set] = None,
        preferred_table_alias: Optional[str] = None,
    ) -> str:
        if not metric_sql:
            return metric_sql

        alias_to_dataset = {alias: ds for ds, alias in dataset_aliases.items()}
        sanitized_ds_to_dataset = {self._id.sanitize_column(ds): ds for ds in dataset_aliases.keys()}

        keywords = {
            "AND", "AS", "ASC", "AVG", "BETWEEN", "BY", "CASE", "CAST", "COALESCE",
            "CURRENT", "CURRENT_DATE", "DATEADD", "DATEDIFF", "DAY", "DESC",
            "DISTINCT", "DIVIDE", "DOUBLE", "ELSE", "END", "FALSE", "FLOAT", "FROM",
            "GROUP", "IFF", "IN", "INT", "IS", "LAG", "LEFT", "LIKE", "MAX",
            "MIN", "MONTH", "NOT", "NULL", "NULLIF", "OR", "ORDER", "OVER",
            "PARTITION", "ROWS", "SUM", "THEN", "TO_DATE", "TRUE",
            "TRY_CAST", "TRY_TO_DATE", "VARCHAR", "WHEN", "WITH", "SYNONYMS", "YEAR",
        }

        def _resolve_owner(token: str) -> Optional[tuple]:
            ident = self._id.sanitize_column(token)
            if metric_names and ident in metric_names:
                return None

            # If token matches a dataset alias/name, prefer column with same name
            dataset_name = alias_to_dataset.get(token) or sanitized_ds_to_dataset.get(token)
            if dataset_name:
                resolved_col = self._resolve_column_name_for_dataset(
                    dataset_col_lookup.get(dataset_name, set()),
                    ident,
                )
                if resolved_col:
                    return dataset_aliases.get(dataset_name), resolved_col

            # Prefer table alias if it contains the column
            if preferred_table_alias:
                preferred_dataset = alias_to_dataset.get(preferred_table_alias)
                if preferred_dataset:
                    resolved_col = self._resolve_column_name_for_dataset(
                        dataset_col_lookup.get(preferred_dataset, set()),
                        ident,
                    )
                    if resolved_col:
                        return preferred_table_alias, resolved_col

            owners = []
            for ds_name, cols in dataset_col_lookup.items():
                resolved_col = self._resolve_column_name_for_dataset(cols, ident)
                if resolved_col:
                    owners.append((ds_name, resolved_col))

            if len(owners) == 1:
                owner_ds, owner_col = owners[0]
                return dataset_aliases.get(owner_ds), owner_col

            return None

        def _replace(match: re.Match) -> str:
            token = match.group(1)
            upper = token.upper()
            if upper in keywords:
                return match.group(0)
            if metric_names and upper in metric_names:
                return match.group(0)
            if token.endswith("("):
                return match.group(0)
            owner = _resolve_owner(token)
            if not owner:
                return match.group(0)
            alias, col = owner
            if not alias:
                return match.group(0)
            return f'{alias}."{col}"'

        pattern = re.compile(r'(?<![\w\."])\b([A-Za-z_][A-Za-z0-9_$]*)\b(?![\w\."])')
        return pattern.sub(_replace, metric_sql)

    @staticmethod
    def _route_qualified_metric_owner_refs(metric_sql: str, metric_to_alias: Optional[Dict[str, str]]) -> str:
        if not metric_sql or not metric_to_alias:
            return metric_sql
        owner_by_metric = {str(name).upper(): str(alias).upper() for name, alias in metric_to_alias.items()}

        def _replace(match: re.Match) -> str:
            alias = match.group(1)
            metric_name = match.group(2).upper()
            owner_alias = owner_by_metric.get(metric_name)
            if owner_alias and alias.upper() != owner_alias:
                return f'"{metric_name}"'
            return match.group(0)

        return re.sub(r'\b([A-Za-z_][A-Za-z0-9_$]*)\."([A-Z_][A-Z0-9_$]*)"', _replace, metric_sql)

    def _normalize_display_name_metric_references(self, metric_sql: str, metric_names: Optional[set]) -> str:
        """Rewrite quoted display-name metric refs to their emitted Snowflake names."""
        if not metric_sql or not metric_names:
            return metric_sql

        def _replace(match: re.Match) -> str:
            token = match.group(1)
            sanitized = self._id.sanitize_alias(token)
            if sanitized in metric_names and sanitized != token:
                return f'"{sanitized}"'
            resolved = self._resolve_metric_reference_name(metric_names, sanitized, allow_fuzzy=True)
            if resolved and resolved != token:
                return f'"{resolved}"'
            return match.group(0)

        def _replace_qualified(match: re.Match) -> str:
            alias = match.group(1)
            token = match.group(2)
            sanitized = self._id.sanitize_alias(token)
            resolved = sanitized if sanitized in metric_names else self._resolve_metric_reference_name(
                metric_names,
                sanitized,
                allow_fuzzy=True,
            )
            if resolved:
                return f'{alias.upper()}."{resolved}"'
            return match.group(0)

        normalized = re.sub(
            r'"([A-Za-z_][A-Za-z0-9_$]*)"\."([^"]+)"',
            _replace_qualified,
            metric_sql,
        )
        return re.sub(r'(?<!\.)"([^"]+)"', _replace, normalized)

    def _repair_bare_aggregate_identifiers(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set] = None,
        preferred_table_alias: Optional[str] = None,
        dialect: str = "snowflake",
    ) -> str:
        if not metric_sql:
            return metric_sql

        alias_to_dataset = {alias: ds for ds, alias in dataset_aliases.items()}
        sanitized_ds_to_dataset = {self._id.sanitize_column(ds): ds for ds in dataset_aliases.keys()}

        agg_pattern = re.compile(r'\b(SUM|AVG|MIN|MAX|COUNT|DISTINCTCOUNT|COUNT_DISTINCT)\s*\(\s*(DISTINCT\s+)?"?([A-Z_][A-Z0-9_$]*)"?(?:\s*::\s*[A-Z0-9_]+)?\s*\)', flags=re.IGNORECASE)

        def _replace(match: re.Match) -> str:
            agg_fn = match.group(1).upper()
            distinct_kw = bool(match.group(2))
            ident = self._id.sanitize_column(match.group(3))

            if metric_names and ident in metric_names:
                return match.group(0)

            dataset_name = alias_to_dataset.get(ident) or sanitized_ds_to_dataset.get(ident)
            if dataset_name:
                dataset_alias = dataset_aliases.get(dataset_name)
                known_columns = dataset_col_lookup.get(dataset_name, set())
                preferred_col = self._pick_preferred_aggregate_column(metric_name, known_columns)
                if not dataset_alias or not preferred_col:
                    logger.warning("Metric '%s': unresolved aggregate identifier '%s' (dataset token path); coercing to NULL", metric_name, ident)
                    return "NULL"

                col_ref = f'{dataset_alias}."{preferred_col}"'
                if agg_fn in {"DISTINCTCOUNT", "COUNT_DISTINCT"}:
                    return f'COUNT(DISTINCT {col_ref})'
                if agg_fn == "SUM":
                    return self._build_safe_sum_sql(col_ref, preferred_col, dialect=dialect)
                if agg_fn == "COUNT" and distinct_kw:
                    return f'COUNT(DISTINCT {col_ref})'
                return f'{agg_fn}({col_ref})'

            owner_candidates: list = []
            if preferred_table_alias:
                preferred_dataset = alias_to_dataset.get(preferred_table_alias)
                if preferred_dataset:
                    preferred_col = self._resolve_column_name_for_dataset(dataset_col_lookup.get(preferred_dataset, set()), ident)
                    if preferred_col:
                        owner_candidates.append((preferred_dataset, preferred_col))

            if not owner_candidates:
                for ds_name, cols in dataset_col_lookup.items():
                    resolved_col = self._resolve_column_name_for_dataset(cols, ident)
                    if resolved_col:
                        owner_candidates.append((ds_name, resolved_col))

            if len(owner_candidates) != 1:
                logger.warning("Metric '%s': ambiguous/unresolved bare aggregate identifier '%s' owners=%s; coercing to NULL", metric_name, ident, sorted({ds for ds, _ in owner_candidates}))
                return "NULL"

            owner_ds, owner_col = owner_candidates[0]
            owner_alias = dataset_aliases.get(owner_ds)
            if not owner_alias:
                logger.warning("Metric '%s': no alias for resolved aggregate owner '%s'; coercing to NULL", metric_name, owner_ds)
                return "NULL"

            col_ref = f'{owner_alias}."{owner_col}"'
            if agg_fn in {"DISTINCTCOUNT", "COUNT_DISTINCT"}:
                return f'COUNT(DISTINCT {col_ref})'
            if agg_fn == "SUM":
                return self._build_safe_sum_sql(col_ref, owner_col, dialect=dialect)
            if agg_fn == "COUNT" and distinct_kw:
                return f'COUNT(DISTINCT {col_ref})'
            return f'{agg_fn}({col_ref})'

        return agg_pattern.sub(_replace, metric_sql)

    def _build_safe_sum_sql(self, expr_sql: str, identifier_hint: Optional[str] = None, dialect: str = "snowflake") -> str:
        """dialect param added during Step 4 (Pipeline C) verification: the
        IFF(...) flag-wrapping and ::FLOAT cast below are Snowflake-only
        syntax — invalid Databricks/Spark SQL. For any non-Snowflake
        dialect this now returns a plain, undecorated SUM(...) instead of
        emitting syntax the target dialect can't parse. Snowflake behavior
        (the default) is completely unchanged."""
        if dialect != "snowflake":
            return f"SUM({expr_sql})"

        is_flag = False
        if identifier_hint:
            hint = identifier_hint.strip().upper().replace('"', '')
            col_name = hint.split('.')[-1] if '.' in hint else hint
            flag_patterns = [r'^IS_', r'^HAS_', r'^WAS_', r'^DID_', r'^DOES_', r'_FLAG$', r'_FLG$', r'^DELETED$', r'_DELETED$']
            is_flag = any(re.search(p, col_name) for p in flag_patterns)

        if is_flag:
            return f"SUM(IFF({expr_sql} = 1 OR {expr_sql} = TRUE, 1, 0))"

        if expr_sql.strip().upper().endswith("::FLOAT"):
            return f"SUM({expr_sql})"
        return f"SUM({expr_sql}::FLOAT)"

    def _resolve_column_name_for_dataset(self, known_columns: set, candidate: str) -> Optional[str]:
        if not known_columns: return None
        # Case-insensitive check
        candidate_lower = candidate.lower()
        for col in known_columns:
            if col.lower() == candidate_lower:
                return col

        compact = candidate.replace("_", "").lower()
        for col in known_columns:
            if col.replace("_", "").lower() == compact: return col

        if candidate.lower().startswith("total_"):
            base = candidate[len("TOTAL_"):]
            base_lower = base.lower()
            for col in known_columns:
                if col.lower() == base_lower:
                    return col
        return None

    def _resolve_metric_reference_name(self, metric_names: Optional[set], candidate: str, *, allow_fuzzy: bool = True) -> Optional[str]:
        if not metric_names: return None
        if candidate in metric_names: return candidate
        compact_candidate = candidate.replace("_", "")
        compact_matches = [m for m in metric_names if m.replace("_", "") == compact_candidate]
        if len(compact_matches) == 1: return compact_matches[0]
        return None

    def _qualify_bare_metric_references(self, metric_sql: str, metric_to_alias: Optional[Dict[str, str]]) -> str:
        if not metric_to_alias:
            return metric_sql
        normalized = metric_sql
        # Loop through metrics sorted by length descending to prevent partial replacements
        for metric_name, owner_alias in sorted(metric_to_alias.items(), key=lambda x: len(x[0]), reverse=True):
            sanitized_metric_name = self._id.sanitize_column(metric_name)

            # 1. Match already quoted bare references: e.g. "SENTIMENT" not preceded by a dot
            quoted_pattern = rf'(?<!\.)"{re.escape(sanitized_metric_name)}"'
            normalized = re.sub(quoted_pattern, f'{owner_alias}."{sanitized_metric_name}"', normalized)

            # 2. Match unquoted bare references: e.g. SENTIMENT_GAP not preceded by dot, quotes or word chars
            unquoted_pattern = rf'(?<![\w\.\"])\b{re.escape(metric_name)}\b(?![\w\."])'
            normalized = re.sub(unquoted_pattern, f'{owner_alias}."{sanitized_metric_name}"', normalized)

        return normalized

    def _quote_bare_metric_references(self, metric_sql: str, metric_names: Optional[set]) -> str:
        if not metric_names: return metric_sql
        normalized = metric_sql
        for metric_name in sorted(metric_names, key=len, reverse=True):
            pattern = rf'(?<![\w\.\"])\b{re.escape(metric_name)}\b(?![\w\."])'
            normalized = re.sub(pattern, f'"{metric_name}"', normalized)
        return normalized

    def _rewrite_metric_aggregate_wrappers(self, metric_sql: str, metric_names: Optional[set]) -> str:
        if not metric_names: return metric_sql
        normalized = metric_sql
        agg_pattern = r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)(?!\s+OVER\b)'
        def _replace(match: re.Match) -> str:
            metric_name = match.group(2)
            if metric_name in metric_names: return f'"{metric_name}"'
            return match.group(0)
        normalized = re.sub(agg_pattern, _replace, normalized)
        composite_agg_pattern = r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*((?:"[A-Z_][A-Z0-9_]*"\s*[+\-*/]\s*)+"[A-Z_][A-Z0-9_]*")\s*\)'
        def _replace_composite(match: re.Match) -> str:
            expr = match.group(2)
            metric_refs = set(re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr))
            if metric_refs and all(ref in metric_names for ref in metric_refs): return expr
            return match.group(0)
        return re.sub(composite_agg_pattern, _replace_composite, normalized)

    def _normalize_date_part_arguments(self, metric_sql: str) -> str:
        normalized = metric_sql
        def _is_date_like(identifier: str) -> bool:
            upper = identifier.upper()
            return upper.endswith('.DATE') or upper.endswith('_DATE')
        def _wrap_try_to_date(match: re.Match) -> str:
            fn = match.group(1)
            arg = match.group(2).strip()
            if _is_date_like(arg) and 'TRY_TO_DATE(' not in arg.upper(): return f"{fn}(TRY_TO_DATE({arg}))"
            return match.group(0)
        normalized = re.sub(r'\b(YEAR|MONTH|DAY|WEEK|QUARTER)\s*\(\s*([^\)]+)\)', _wrap_try_to_date, normalized, flags=re.IGNORECASE)
        def _wrap_extract(match: re.Match) -> str:
            part = match.group(1)
            arg = match.group(2).strip()
            if _is_date_like(arg) and 'TRY_TO_DATE(' not in arg.upper(): return f"EXTRACT({part} FROM TRY_TO_DATE({arg}))"
            return match.group(0)
        normalized = re.sub(r'\bEXTRACT\s*\(\s*([A-Z_]+)\s+FROM\s+([^\)]+)\)', _wrap_extract, normalized, flags=re.IGNORECASE)
        return normalized

    def _normalize_rolling_monthindex_max_predicates(self, metric_sql: str) -> str:
        normalized = metric_sql
        pattern = r'(?P<id>[A-Z_][A-Z0-9_\.]+)\s*<=\s*MAX\(\s*(?P=id)\s*\)\s*AND\s*(?P=id)\s*>\s*MAX\(\s*(?P=id)\s*\)\s*-\s*12'
        def _replace(match: re.Match) -> str:
            month_index_id = match.group('id')
            return f"{month_index_id} > ((YEAR(CURRENT_DATE()) * 12) + MONTH(CURRENT_DATE()) - 12)"
        return re.sub(pattern, _replace, normalized, flags=re.IGNORECASE)

    def _qualify_bare_partition_identifiers(self, metric_sql: str, dataset_col_lookup: Dict[str, set], dataset_aliases: Dict[str, str], preferred_table_alias: Optional[str] = None) -> str:
        if not metric_sql: return metric_sql
        alias_to_dataset = {alias: ds for ds, alias in dataset_aliases.items()}
        pattern = re.compile(r'(?i)(PARTITION\s+BY\s+)("?[A-Z_][A-Z0-9_]*"?)')
        def _replace(match: re.Match) -> str:
            prefix = match.group(1)
            raw_id = match.group(2)
            identifier = self._id.sanitize_column(raw_id.strip('"'))
            if preferred_table_alias:
                ds = alias_to_dataset.get(preferred_table_alias)
                if ds and self._resolve_column_name_for_dataset(dataset_col_lookup.get(ds, set()), identifier):
                    return f'{prefix}{preferred_table_alias}."{identifier}"'
            owners = [ds for ds, cols in dataset_col_lookup.items() if identifier in cols]
            if len(owners) == 1:
                alias = dataset_aliases.get(owners[0])
                if alias: return f'{prefix}{alias}."{identifier}"'
            return match.group(0)
        return pattern.sub(_replace, metric_sql)

    def _dedupe_qualified_column_tokens(self, metric_sql: str) -> str:
        if not metric_sql: return metric_sql
        repaired = metric_sql
        repaired = re.sub(r'(\b\w+\.)"([A-Z_][A-Z0-9_]*)"\.\2\b', r'\1"\2"', repaired)
        repaired = re.sub(r'(\b\w+\.)([A-Z_][A-Z0-9_]*)\.\2\b', r'\1\2', repaired)
        return repaired

    def _rewrite_window_metric_expression(self, metric_sql: str, preferred_table_alias: Optional[str] = None, dialect: str = "snowflake") -> str:
        """dialect param added during Step 4 (Pipeline C) verification: the
        DIV0(...) pattern below is Snowflake-only syntax the prompt never
        instructs a Databricks LLM call to produce, so this branch is
        already naturally inert for Databricks — dialect is still threaded
        through to _build_safe_sum_sql for defense-in-depth/consistency."""
        if not metric_sql or "OVER" not in metric_sql.upper(): return metric_sql
        ratio_pattern = re.compile(r'(?is)^\s*DIV0\s*\(\s*SUM\((?P<num>[^\)]+)\)\s*,\s*SUM\((?P<den>[^\)]+)\)\s+OVER\s*\([^\)]*\)\s*\)\s*$')
        match = ratio_pattern.match(metric_sql.strip())
        if match:
            num = match.group("num").strip()
            den = match.group("den").strip()
            if num.upper() == den.upper(): return self._build_safe_sum_sql(num, num, dialect=dialect)
            return "NULL"
        ytd_like = re.compile(r'(?is)^\s*(SUM|AVG|COUNT|MIN|MAX)\s*\([^\)]+\)\s+OVER\s*\(\s*PARTITION\s+BY\s+.+?\s+ORDER\s+BY\s+.+?\)\s*$')
        if ytd_like.match(metric_sql.strip()):
            if preferred_table_alias:
                refs = set(a.upper() for a in re.findall(r'(?i)\b(\w+)\s*\.', metric_sql.split("OVER", 1)[1] if "OVER" in metric_sql.upper() else ""))
                if refs and any(a != preferred_table_alias.upper() for a in refs): return "NULL"
            return metric_sql
        sply_like = re.compile(r'(?is)^\s*(LAG|LEAD)\s*\(\s*.+?\)\s+OVER\s*\(.*\)\s*$')
        if sply_like.match(metric_sql.strip()): return metric_sql
        return "NULL"

    def _pick_preferred_aggregate_column(self, metric_name: str, known_columns: set) -> Optional[str]:
        """Only auto-resolves when excluding structural key/FK/date-suffixed
        columns leaves exactly one candidate — never by scoring keyword
        overlap with the metric's own name, which can silently substitute
        the wrong column."""
        if not known_columns: return None
        excluded_suffixes = (
            "_CK", "_ID", "_KEY", "_DATE",
            "_TYPE", "_STATUS", "_FLAG", "_NAME", "_CODE",
            "_CLASS", "_SEGMENT", "_DESC", "_DESCRIPTION", "_GROUP", "_CATEGORY",
        )
        candidates = [col for col in known_columns if not col.upper().endswith(excluded_suffixes)]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _validate_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set] = None
    ) -> Tuple[bool, Optional[str]]:
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        alias_to_dataset.update({str(v).lower(): k for k, v in dataset_aliases.items()})
        alias_to_dataset.update({str(v).upper(): k for k, v in dataset_aliases.items()})
        # Also accept raw dataset/table names as valid qualifiers (for cross-table refs
        # generated by rule translators, e.g. PRODUCT."ISVANARSDEL").
        raw_name_to_dataset = {k: k for k in dataset_col_lookup}
        raw_name_to_dataset.update({k.upper(): k for k in dataset_col_lookup})
        raw_name_to_dataset.update({k.lower(): k for k in dataset_col_lookup})

        patterns = [
            r'(\w+)\."([^"]+)"',
            r'(\w+)\.([A-Za-z_][A-Za-z0-9_]*)',
        ]

        all_refs = []
        for pattern in patterns:
            matches = re.findall(pattern, metric_sql)
            all_refs.extend(matches)

        if not all_refs:
            bare_identifiers = set(re.findall(r'"([A-Z_][A-Z0-9_$]*)"', metric_sql))
            for ident in sorted(bare_identifiers):
                if metric_names and ident in metric_names:
                    continue
                owners = [
                    ds_name for ds_name, cols in dataset_col_lookup.items()
                    if self._resolve_column_name_for_dataset(cols, ident)
                ]
                if owners:
                    error = (
                        f"Unqualified physical identifier '{ident}' in metric SQL; owners={sorted(set(owners))}"
                    )
                    logger.debug(f"Metric '{metric_name}': {error}")
                    return False, error
            logger.debug(f"No cross-table references found in metric '{metric_name}'")
            return True, None

        for table_alias, col_name in all_refs:
            dataset_name = alias_to_dataset.get(table_alias)
            if not dataset_name:
                # Fall back to checking raw table names (e.g. PRODUCT."ISVANARSDEL")
                dataset_name = raw_name_to_dataset.get(table_alias)
            if not dataset_name:
                dataset_name = self._heal_unknown_alias(table_alias, col_name, dataset_aliases, dataset_col_lookup, metric_names)
            if not dataset_name:
                error = f"Alias '{table_alias}' not found in dataset mapping"
                logger.warning(f"Metric '{metric_name}': {error}")
                return False, error

            known_columns = dataset_col_lookup.get(dataset_name, set())
            sanitized_col_name = self._id.sanitize_column(col_name)
            resolved_metric_ref = self._resolve_metric_reference_name(
                metric_names,
                sanitized_col_name,
                allow_fuzzy=False,
            )
            if resolved_metric_ref:
                continue

            fuzzy_metric_ref = self._resolve_metric_reference_name(
                metric_names,
                sanitized_col_name,
                allow_fuzzy=True,
            )
            if fuzzy_metric_ref:
                continue
            resolved_col = self._resolve_column_name_for_dataset(
                known_columns,
                sanitized_col_name,
            )
            if not resolved_col:
                error = (
                    f"Column '{col_name}' (sanitized: '{sanitized_col_name}') not found in dataset '{dataset_name}'. Available columns: {sorted(known_columns)}"
                )
                logger.debug(f"Metric '{metric_name}': {error}")
                return False, error

        logger.debug(f"Metric '{metric_name}': All column references valid")
        return True, None

    def _extract_dax_table_hints(self, dax: Optional[str]) -> Dict[str, str]:
        """Mirrors MetricExpressionTranslator._extract_dax_table_hints
        (connectors/translator.py) verbatim in behavior — see its docstring."""
        hints: Dict[str, str] = {}
        if not dax or not self._id:
            return hints
        for m in _DAX_QUALIFIED_COLUMN_PATTERN.finditer(dax):
            table_name = (m.group(1) or m.group(3) or "").strip()
            col_name = (m.group(2) or m.group(4) or "").strip()
            if not table_name or not col_name:
                continue
            hints[self._id.sanitize_column(col_name)] = table_name
        return hints

    def _resolve_dax_hinted_dataset(
        self,
        sanitized_col_name: str,
        current_dataset_name: Optional[str],
        dax_table_hints: Optional[Dict[str, str]],
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, set],
    ) -> Optional[str]:
        """Mirrors MetricExpressionTranslator._resolve_dax_hinted_dataset
        (connectors/translator.py) verbatim in behavior — see its docstring."""
        if not dax_table_hints:
            return None
        hinted_table = dax_table_hints.get(sanitized_col_name)
        if not hinted_table:
            return None
        hinted_cf = hinted_table.strip().strip("'").casefold()
        hinted_dataset = next(
            (ds for ds in dataset_aliases if ds.strip().casefold() == hinted_cf),
            None,
        )
        if not hinted_dataset or hinted_dataset == current_dataset_name:
            return None
        if not self._resolve_column_name_for_dataset(
            dataset_col_lookup.get(hinted_dataset, set()), sanitized_col_name
        ):
            return None
        return hinted_dataset

    def _normalize_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set] = None,
        preferred_table_alias: Optional[str] = None,
        metric_to_alias: Optional[Dict[str, str]] = None,
        dialect: str = "snowflake",
        original_dax: Optional[str] = None,
    ) -> str:
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        alias_to_dataset.update({str(v).lower(): k for k, v in dataset_aliases.items()})
        alias_to_dataset.update({str(v).upper(): k for k, v in dataset_aliases.items()})
        dax_table_hints = self._extract_dax_table_hints(original_dax)
        normalized_sql = metric_sql
        normalized_sql = self._normalize_display_name_metric_references(normalized_sql, metric_names)

        try:
            from semabridge.utils.naming import to_alias as _to_alias
            legacy_alias_remap: Dict[str, str] = {}
            for ds_name, declared_alias in dataset_aliases.items():
                legacy = _to_alias(ds_name)
                if legacy and legacy != declared_alias and legacy not in alias_to_dataset:
                    legacy_alias_remap[legacy] = declared_alias
            if legacy_alias_remap:
                for legacy, declared in legacy_alias_remap.items():
                    normalized_sql = re.sub(
                        rf'\b{re.escape(legacy)}\.',
                        f'{declared}.',
                        normalized_sql,
                        flags=re.IGNORECASE,
                    )
        except Exception:
            pass

        def _format_metric_ref(table_alias: str, col_name: str) -> str:
            safe_alias = f'"{table_alias}"' if table_alias.lower() in self._id._reserved else table_alias
            if "$" in col_name or col_name.lower() in self._id._reserved:
                return f'{safe_alias}."{col_name}"'
            return f"{safe_alias}.{col_name}"

        quoted_pattern = r'(?:"(\w+)"|(\w+))\.(["\'])([^"\']+)\3'
        for match in re.finditer(quoted_pattern, normalized_sql):
            table_alias = match.group(1) or match.group(2)
            col_name = match.group(4)
            sanitized_col_name = self._id.sanitize_column(col_name)
            dataset_name = alias_to_dataset.get(table_alias)
            hinted_dataset = self._resolve_dax_hinted_dataset(
                sanitized_col_name, dataset_name, dax_table_hints, dataset_aliases, dataset_col_lookup,
            )
            if hinted_dataset:
                dataset_name = hinted_dataset
                table_alias = dataset_aliases.get(hinted_dataset, table_alias)
            elif not dataset_name:
                dataset_name = self._heal_unknown_alias(table_alias, col_name, dataset_aliases, dataset_col_lookup, metric_names)
                if dataset_name:
                    table_alias = dataset_aliases.get(dataset_name, table_alias)
            if not dataset_name:
                continue
            known_columns = dataset_col_lookup.get(dataset_name, set())
            resolved_col = self._resolve_column_name_for_dataset(known_columns, sanitized_col_name)
            if not resolved_col:
                owners = [ds for ds, cols in dataset_col_lookup.items() if self._resolve_column_name_for_dataset(cols, sanitized_col_name)]
                if len(owners) == 1:
                    owner_alias = dataset_aliases.get(owners[0])
                    if owner_alias:
                        owner_col = self._resolve_column_name_for_dataset(dataset_col_lookup.get(owners[0], set()), sanitized_col_name) or sanitized_col_name
                        old_ref = match.group(0)
                        new_ref = _format_metric_ref(owner_alias, owner_col)
                        normalized_sql = normalized_sql.replace(old_ref, new_ref)
                        logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {new_ref}")
                        continue

                resolved_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=False)
                if resolved_metric_ref:
                    old_ref = match.group(0)
                    new_ref = f'"{resolved_metric_ref}"'
                    normalized_sql = normalized_sql.replace(old_ref, new_ref)
                    continue

                fuzzy_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
                if fuzzy_metric_ref:
                    old_ref = match.group(0)
                    normalized_sql = normalized_sql.replace(old_ref, f'"{fuzzy_metric_ref}"')
                    logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {fuzzy_metric_ref}")
                    continue
            elif resolved_col != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, resolved_col)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue

            old_ref = match.group(0)
            new_ref = _format_metric_ref(table_alias, sanitized_col_name)
            normalized_sql = normalized_sql.replace(old_ref, new_ref)
            logger.debug(f"Normalized metric '{metric_name}': {old_ref} → {new_ref}")

        unquoted_pattern = r'(?:"(\w+)"|(\w+))\.([A-Za-z_][A-Za-z0-9_$]*)'
        for match in re.finditer(unquoted_pattern, normalized_sql):
            table_alias = match.group(1) or match.group(2)
            col_name = match.group(3)
            sanitized_col_name = self._id.sanitize_column(col_name)
            dataset_name = alias_to_dataset.get(table_alias)
            hinted_dataset = self._resolve_dax_hinted_dataset(
                sanitized_col_name, dataset_name, dax_table_hints, dataset_aliases, dataset_col_lookup,
            )
            if hinted_dataset:
                dataset_name = hinted_dataset
                table_alias = dataset_aliases.get(hinted_dataset, table_alias)
            elif not dataset_name:
                dataset_name = self._heal_unknown_alias(table_alias, col_name, dataset_aliases, dataset_col_lookup, metric_names)
                if dataset_name:
                    table_alias = dataset_aliases.get(dataset_name, table_alias)
            if not dataset_name:
                continue
            known_columns = dataset_col_lookup.get(dataset_name, set())
            resolved_col = self._resolve_column_name_for_dataset(known_columns, sanitized_col_name)
            if not resolved_col:
                owners = [ds for ds, cols in dataset_col_lookup.items() if self._resolve_column_name_for_dataset(cols, sanitized_col_name)]
                if len(owners) == 1:
                    owner_alias = dataset_aliases.get(owners[0])
                    if owner_alias:
                        owner_col = self._resolve_column_name_for_dataset(dataset_col_lookup.get(owners[0], set()), sanitized_col_name) or sanitized_col_name
                        old_ref = match.group(0)
                        new_ref = _format_metric_ref(owner_alias, owner_col)
                        normalized_sql = normalized_sql.replace(old_ref, new_ref)
                        logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {new_ref}")
                        continue

                resolved_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=False)
                if resolved_metric_ref:
                    old_ref = match.group(0)
                    new_ref = f'"{resolved_metric_ref}"'
                    normalized_sql = normalized_sql.replace(old_ref, new_ref)
                    continue

                fuzzy_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
                if fuzzy_metric_ref:
                    old_ref = match.group(0)
                    normalized_sql = normalized_sql.replace(old_ref, f'"{fuzzy_metric_ref}"')
                    logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {fuzzy_metric_ref}")
                    continue
            elif resolved_col != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, resolved_col)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue
            else:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, sanitized_col_name)
                if old_ref != new_ref:
                    normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue

            if col_name != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, sanitized_col_name)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                logger.debug(f"Normalized metric '{metric_name}': {old_ref} → {new_ref}")

        normalized_sql = self._normalize_display_name_metric_references(normalized_sql, metric_names)
        normalized_sql = self._qualify_bare_metric_references(normalized_sql, metric_to_alias)
        normalized_sql = self._quote_bare_metric_references(normalized_sql, metric_names)
        normalized_sql = self._rewrite_metric_aggregate_wrappers(normalized_sql, metric_names)
        normalized_sql = self._repair_bare_aggregate_identifiers(normalized_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_names, preferred_table_alias=preferred_table_alias, dialect=dialect)
        normalized_sql = self._qualify_bare_column_identifiers(
            normalized_sql,
            dataset_col_lookup,
            dataset_aliases,
            metric_names,
            preferred_table_alias=preferred_table_alias,
        )
        normalized_sql = self._normalize_date_part_arguments(normalized_sql)
        normalized_sql = self._qualify_bare_partition_identifiers(normalized_sql, dataset_col_lookup, dataset_aliases, preferred_table_alias=preferred_table_alias)
        normalized_sql = self._dedupe_qualified_column_tokens(normalized_sql)
        normalized_sql = self._rewrite_window_metric_expression(normalized_sql, preferred_table_alias=preferred_table_alias, dialect=dialect)
        normalized_sql = self._normalize_rolling_monthindex_max_predicates(normalized_sql)
        normalized_sql = self._qualify_bare_metric_references(normalized_sql, metric_to_alias)
        normalized_sql = self._route_qualified_metric_owner_refs(normalized_sql, metric_to_alias)

        return normalized_sql


# ---------------------------------------------------------------------------
# Ergonomic adapters for tier5/service.py — NOT part of the verbatim salvage.
# These translate the TranslationRequest shape into the many positional
# parameters the original methods expect, and share one default validator
# instance. This glue is new code, written for this consolidation.
#
# Quote-translation sandwich (added during Step 4 / Pipeline C verification):
# the verbatim-salvaged validator's regexes only recognize Snowflake-style
# double-quoted identifiers ("alias"."column") — empirically confirmed to
# silently accept Databricks backtick-quoted SQL referencing a column that
# doesn't exist at all, because backtick-delimited references don't match
# either the qualified-reference or bare-identifier patterns, so they're
# invisible to the checks entirely (not "checked and passed" — never seen).
# Rather than duplicating every regex in the ~700-line verbatim class for a
# second quote character, non-Snowflake SQL is translated to Snowflake-style
# quoting before being handed to the unmodified verbatim methods, and
# translated back to the target dialect's real quoting on the way out.
# General (quote-character-only), not hardcoded to any table/column/model
# name; a no-op for Snowflake.
# ---------------------------------------------------------------------------

_default_validator = MetricSqlValidator()

# The verbatim-salvaged patterns expect Snowflake's convention specifically:
# a *bare* alias followed by a *quoted* column (alias."COLUMN"), not both
# sides quoted. Databricks output backtick-quotes both sides
# (`alias`.`column`) — converting naively (every backtick -> double-quote)
# produces "alias"."column", which the verbatim regexes don't recognize
# either (they require the alias to be unquoted \w+), so it must be
# unwrapped specifically, not just re-quoted.
_BACKTICK_DOT_PAIR_RE = re.compile(r'`([^`]+)`\.`([^`]+)`')
_BACKTICK_IDENTIFIER_RE = re.compile(r'`([^`]+)`')
_DOUBLEQUOTE_DOT_PAIR_RE = re.compile(r'(\w+)\."([^"]+)"')
_DOUBLEQUOTE_IDENTIFIER_RE = re.compile(r'"([^"]+)"')


def _dialect_str(request: Any) -> str:
    dialect = getattr(request, "dialect", "snowflake")
    return getattr(dialect, "value", str(dialect))


def _to_snowflake_style_quoting(sql: str, dialect: str) -> str:
    # Triggered by backtick PRESENCE, not the declared dialect: backticks
    # are never valid Snowflake syntax, so a Snowflake-dialect request
    # whose LLM output backtick-quotes identifiers anyway (e.g. confused by
    # prompt.py's Databricks-flavored few-shot examples) must still be
    # normalized before the verbatim regexes below can see it — gating this
    # on dialect == "databricks" left that case invisible to validation
    # entirely, the same blindness this sandwich was built to close.
    if not sql or "`" not in sql:
        return sql
    # `alias`.`column` -> alias."column" (bare alias, quoted column)
    sql = _BACKTICK_DOT_PAIR_RE.sub(r'\1."\2"', sql)
    # any remaining standalone `token` (e.g. a bare metric reference) -> "token"
    sql = _BACKTICK_IDENTIFIER_RE.sub(r'"\1"', sql)
    return sql


def _from_snowflake_style_quoting(sql: str, dialect: str) -> str:
    if dialect != "databricks" or not sql:
        return sql
    # alias."column" -> `alias`.`column`
    sql = _DOUBLEQUOTE_DOT_PAIR_RE.sub(r'`\1`.`\2`', sql)
    # any remaining standalone "token" -> `token`
    sql = _DOUBLEQUOTE_IDENTIFIER_RE.sub(r'`\1`', sql)
    return sql


def validate_metric_column_references(
    sql: str,
    request: Any,
    metric_names: Optional[Set[str]] = None,
) -> Tuple[bool, Optional[str]]:
    metric_names = metric_names if metric_names is not None else request.metric_names
    dialect = _dialect_str(request)
    internal_sql = _to_snowflake_style_quoting(sql, dialect)
    return _default_validator._validate_metric_column_references(
        internal_sql,
        request.metric_name or "",
        request.dataset_col_lookup,
        request.dataset_aliases,
        metric_names,
    )


def normalize_metric_column_references(
    sql: str,
    request: Any,
    metric_names: Optional[Set[str]] = None,
) -> str:
    metric_names = metric_names if metric_names is not None else request.metric_names
    dialect = _dialect_str(request)
    internal_sql = _to_snowflake_style_quoting(sql, dialect)
    normalized = _default_validator._normalize_metric_column_references(
        internal_sql,
        request.metric_name or "",
        request.dataset_col_lookup,
        request.dataset_aliases,
        metric_names,
        preferred_table_alias=request.table_alias,
        dialect=dialect,
        original_dax=request.dax,
    )
    return _from_snowflake_style_quoting(normalized, dialect)
