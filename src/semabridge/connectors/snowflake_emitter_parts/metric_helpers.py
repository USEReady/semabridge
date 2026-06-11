from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from semabridge.formats.sml.models import SMLMetric, SMLModel


def sanitize_sql_markdown(sql: str) -> str:
    if not sql or not isinstance(sql, str):
        return sql

    sql = re.sub(r'^\s*```(?:sql|python|javascript|js|\w*)?\s*\n?', '', sql, flags=re.MULTILINE | re.IGNORECASE)
    sql = re.sub(r'\n?\s*```\s*$', '', sql, flags=re.MULTILINE | re.IGNORECASE)
    return sql.strip()


def try_basic_dax_metric_fallback_expression(
    emitter,
    metric: SMLMetric,
    table_alias: str,
    dataset_col_lookup: Dict[str, set[str]],
) -> Optional[str]:
    raw_expr = (metric.expression or "").strip()
    if not raw_expr:
        return None

    expr = " ".join(raw_expr.split())
    known_cols = dataset_col_lookup.get(metric.dataset, set())

    if re.match(r"(?i)^COUNTROWS\(\s*'[^']+'\s*\)$", expr):
        return "COUNT(*)"

    m_blank = re.match(r"(?i)^COUNTBLANK\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$", expr)
    if m_blank:
        col_name = emitter._sanitize_col_name(m_blank.group(1))
        if known_cols and col_name not in known_cols:
            return None
        return f'COUNT_IF({table_alias}."{col_name}" IS NULL)'

    m_agg = re.match(r"(?i)^(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$", expr)
    if m_agg:
        agg = m_agg.group(1).upper()
        col_name = emitter._sanitize_col_name(m_agg.group(2))
        if known_cols and col_name not in known_cols:
            return None
        if agg == "AVERAGE":
            return f'AVG({table_alias}."{col_name}")'
        if agg == "DISTINCTCOUNT":
            return f'COUNT(DISTINCT {table_alias}."{col_name}")'
        if agg == "SUM":
            return emitter._build_safe_sum_sql(f'{table_alias}."{col_name}"', col_name)
        return f'{agg}({table_alias}."{col_name}")'

    return None


def build_schema_validation_map(emitter, sml: SMLModel) -> Dict[str, set[str]]:
    schema_map: Dict[str, set[str]] = {}
    for dataset in sml.datasets:
        columns = set()
        for col in dataset.columns:
            if col.unique_name.startswith("_") or col.unique_name.startswith("RowNumber"):
                continue
            source_expr = getattr(col, "source_expression", None)
            if source_expr and not emitter._is_physical_source_column(source_expr):
                continue
            columns.add(emitter._sanitize_col_name(col.unique_name))
        schema_map[dataset.unique_name] = columns
    return schema_map


def try_llm_metric_fallback_expression(
    emitter,
    *,
    metric: SMLMetric,
    metric_name: str,
    table_alias: str,
    alias_by_raw: Dict[str, str],
    dataset_col_lookup: Dict[str, set[str]],
    dataset_aliases: Dict[str, str],
    metric_name_set: set[str],
    all_physical_col_names: set[str],
    emittable_metric_name_set: set[str],
    skipped_metric_names: set[str],
    column_owner_index: Optional[dict[str, list[str]]] = None,
) -> Optional[str]:
    dax_expression = (getattr(metric, "expression", None) or "").strip()
    if not dax_expression:
        return None

    candidate_expressions: list[str] = []

    try:
        from semabridge.converter.dax_rule_translator import is_simple_metric, rule_based_translation
        if is_simple_metric(dax_expression):
            local_expr = rule_based_translation(dax_expression, table_alias.lower())
            if local_expr:
                candidate_expressions.append(local_expr)
    except Exception:
        pass

    try:
        from semabridge.converter.gemini_dax_translator import get_gemini_translator
        translator = get_gemini_translator()
    except Exception:
        translator = None

    if translator and getattr(translator, "use_gemini", False) and getattr(translator, "api_key", None):
        schema_context = {ds_name: sorted(list(cols)) for ds_name, cols in dataset_col_lookup.items()}
        llm_result = translator.translate(
            dax=dax_expression,
            table_alias=table_alias.lower(),
            dataset_name=metric.dataset,
            metric_name=metric.unique_name,
            schema_context=schema_context,
        )
        if llm_result and llm_result.is_valid and llm_result.sql:
            candidate_expressions.append(llm_result.sql)

    for candidate_sql in candidate_expressions:
        expr = sanitize_sql_markdown(candidate_sql)
        if not expr or "SELECT" in expr.upper():
            continue

        expr = emitter._id.resolve_dot_notation(
            expr,
            alias_by_raw,
            sanitize_col_fn=emitter._sanitize_col_name,
        )
        expr = emitter._normalize_metric_column_references(
            expr,
            metric.unique_name,
            dataset_col_lookup,
            dataset_aliases,
            metric_names=metric_name_set,
            column_owner_index=column_owner_index,
        )

        is_valid, _ = emitter._validate_metric_column_references(
            expr,
            metric.unique_name,
            dataset_col_lookup,
            dataset_aliases,
            metric_names=metric_name_set,
            column_owner_index=column_owner_index,
        )
        if not is_valid or not expr.strip():
            continue

        expr_upper = expr.upper().strip()
        if expr_upper == "SUM(*)" or expr_upper.endswith("SUM(*)"):
            continue

        unresolved_metric_refs = [
            r for r in re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr)
            if r in metric_name_set
            and r not in all_physical_col_names
            and (r not in emittable_metric_name_set or r in skipped_metric_names)
            and r != metric_name
        ]
        if unresolved_metric_refs:
            continue

        return expr

    return None


def validate_metric_column_references(
    emitter,
    metric_sql: str,
    metric_name: str,
    dataset_col_lookup: Dict[str, set[str]],
    dataset_aliases: Dict[str, str],
    metric_names: Optional[set[str]] = None,
    column_owner_index: Optional[dict[str, list[str]]] = None,
) -> Tuple[bool, Optional[str]]:
    alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
    patterns = [
        r'(\w+)\."([^"]+)"',
        r'(\w+)\.([A-Za-z_][A-Za-z0-9_]*)',
    ]
    all_refs = []
    for pattern in patterns:
        all_refs.extend(re.findall(pattern, metric_sql))

    if not all_refs:
        return True, None

    for table_alias, col_name in all_refs:
        dataset_name = alias_to_dataset.get(table_alias)
        if not dataset_name:
            return False, f"Alias '{table_alias}' not found in dataset mapping"

        known_columns = dataset_col_lookup.get(dataset_name, set())
        sanitized_col_name = emitter._sanitize_col_name(col_name)
        resolved_metric_ref = resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=False)
        if resolved_metric_ref:
            continue
        fuzzy_metric_ref = resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
        if fuzzy_metric_ref:
            continue
        resolved_col = resolve_column_name_for_dataset(known_columns, sanitized_col_name)
        if not resolved_col:
            owners = column_owner_index.get(sanitized_col_name, []) if column_owner_index else [
                ds for ds, cols in dataset_col_lookup.items()
                if resolve_column_name_for_dataset(cols, sanitized_col_name)
            ]
            if len(owners) == 1:
                continue
            return False, (
                f"Column '{col_name}' (sanitized: '{sanitized_col_name}') not found in dataset '{dataset_name}'. "
                f"Available columns: {sorted(known_columns)}"
            )

    return True, None


def normalize_metric_column_references(
    emitter,
    metric_sql: str,
    metric_name: str,
    dataset_col_lookup: Dict[str, set[str]],
    dataset_aliases: Dict[str, str],
    metric_names: Optional[set[str]] = None,
    column_owner_index: Optional[dict[str, list[str]]] = None,
) -> str:
    alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
    normalized_sql = metric_sql

    quoted_pattern = r'(?:"(\w+)"|(\w+))\.(["\'])([^"\']+)\3'
    for match in re.finditer(quoted_pattern, normalized_sql):
        table_alias = match.group(1) or match.group(2)
        col_name = match.group(4)
        dataset_name = alias_to_dataset.get(table_alias)
        if not dataset_name:
            continue
        sanitized_col_name = emitter._sanitize_col_name(col_name)
        resolved_metric_ref = resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=False)
        if resolved_metric_ref:
            normalized_sql = normalized_sql.replace(match.group(0), resolved_metric_ref)
            continue
        known_columns = dataset_col_lookup.get(dataset_name, set())
        resolved_col = resolve_column_name_for_dataset(known_columns, sanitized_col_name)
        if not resolved_col:
            fuzzy_metric_ref = resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
            if fuzzy_metric_ref:
                normalized_sql = normalized_sql.replace(match.group(0), fuzzy_metric_ref)
                continue
            owners = column_owner_index.get(sanitized_col_name, []) if column_owner_index else [
                ds for ds, cols in dataset_col_lookup.items()
                if resolve_column_name_for_dataset(cols, sanitized_col_name)
            ]
            if len(owners) == 1:
                owner_alias = dataset_aliases.get(owners[0])
                if owner_alias:
                    owner_col = resolve_column_name_for_dataset(
                        dataset_col_lookup.get(owners[0], set()),
                        sanitized_col_name,
                    ) or sanitized_col_name
                    normalized_sql = normalized_sql.replace(match.group(0), f'{owner_alias}.{owner_col}')
                    continue
        elif resolved_col != sanitized_col_name:
            normalized_sql = normalized_sql.replace(match.group(0), f'{table_alias}.{resolved_col}')
            continue
        normalized_sql = normalized_sql.replace(match.group(0), f'{table_alias}.{sanitized_col_name}')

    unquoted_pattern = r'(?:"(\w+)"|(\w+))\.([A-Za-z_][A-Za-z0-9_$]*)'
    for match in re.finditer(unquoted_pattern, normalized_sql):
        table_alias = match.group(1) or match.group(2)
        col_name = match.group(3)
        dataset_name = alias_to_dataset.get(table_alias)
        if not dataset_name:
            continue
        sanitized_col_name = emitter._sanitize_col_name(col_name)
        resolved_metric_ref = resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=False)
        if resolved_metric_ref:
            normalized_sql = normalized_sql.replace(match.group(0), resolved_metric_ref)
            continue
        known_columns = dataset_col_lookup.get(dataset_name, set())
        resolved_col = resolve_column_name_for_dataset(known_columns, sanitized_col_name)
        if not resolved_col:
            fuzzy_metric_ref = resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
            if fuzzy_metric_ref:
                normalized_sql = normalized_sql.replace(match.group(0), fuzzy_metric_ref)
                continue
            owners = column_owner_index.get(sanitized_col_name, []) if column_owner_index else [
                ds for ds, cols in dataset_col_lookup.items()
                if resolve_column_name_for_dataset(cols, sanitized_col_name)
            ]
            if len(owners) == 1:
                owner_alias = dataset_aliases.get(owners[0])
                if owner_alias:
                    owner_col = resolve_column_name_for_dataset(
                        dataset_col_lookup.get(owners[0], set()),
                        sanitized_col_name,
                    ) or sanitized_col_name
                    normalized_sql = normalized_sql.replace(match.group(0), f'{owner_alias}.{owner_col}')
                    continue
        elif resolved_col != sanitized_col_name:
            normalized_sql = normalized_sql.replace(match.group(0), f'{table_alias}.{resolved_col}')
            continue
        if col_name != sanitized_col_name:
            normalized_sql = normalized_sql.replace(match.group(0), f'{table_alias}.{sanitized_col_name}')

    normalized_sql = quote_bare_metric_references(normalized_sql, metric_names)
    normalized_sql = rewrite_metric_aggregate_wrappers(normalized_sql, metric_names)
    normalized_sql = normalize_date_part_arguments(normalized_sql)
    normalized_sql = normalize_rolling_monthindex_max_predicates(normalized_sql)
    return normalized_sql


def resolve_column_name_for_dataset(known_columns: set[str], candidate: str) -> Optional[str]:
    if not known_columns:
        return None
    if candidate in known_columns:
        return candidate
    compact = candidate.replace("_", "")
    for col in known_columns:
        if col.replace("_", "") == compact:
            return col
    if candidate.startswith("TOTAL_"):
        base = candidate[len("TOTAL_"):]
        if base in known_columns:
            return base

    def _stem(token: str) -> str:
        t = token.upper()
        if len(t) > 4 and t.endswith("IES"):
            return t[:-3] + "Y"
        if len(t) > 3 and t.endswith("S"):
            return t[:-1]
        return t

    candidate_tokens = [t for t in candidate.split("_") if t]
    if candidate_tokens:
        scored: list[tuple[int, str]] = []
        candidate_token_set = {_stem(t) for t in candidate_tokens}
        for col in known_columns:
            col_tokens = [t for t in col.split("_") if t]
            if not col_tokens:
                continue
            col_token_set = {_stem(t) for t in col_tokens}
            overlap = len(candidate_token_set.intersection(col_token_set))
            if overlap == 0:
                continue
            score = overlap * 10 - abs(len(candidate_tokens) - len(col_tokens))
            scored.append((score, col))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            best_score = scored[0][0]
            best = [col for score, col in scored if score == best_score]
            if len(best) == 1:
                return best[0]

    synonym_groups = [
        {"AMOUNT", "REVENUE", "SALES", "VALUE"},
        {"UNIT", "UNITS", "QUANTITY", "QTY", "COUNT", "VOLUME"},
    ]
    compact_candidate_tokens = {_stem(t) for t in candidate.split("_") if t}
    for group in synonym_groups:
        normalized_group = {_stem(t) for t in group}
        if not compact_candidate_tokens.intersection(normalized_group):
            continue
        candidates_in_group = []
        for col in known_columns:
            col_tokens = {_stem(t) for t in col.split("_") if t}
            if col_tokens.intersection(normalized_group):
                candidates_in_group.append(col)
        if len(candidates_in_group) == 1:
            return candidates_in_group[0]

    if candidate.endswith("_DATE") and "DATE" in known_columns:
        return "DATE"

    return None


def resolve_metric_reference_name(metric_names: Optional[set[str]], candidate: str, *, allow_fuzzy: bool = True) -> Optional[str]:
    if not metric_names:
        return None
    if candidate in metric_names:
        return candidate
    compact_candidate = candidate.replace("_", "")
    compact_matches = [m for m in metric_names if m.replace("_", "") == compact_candidate]
    if len(compact_matches) == 1:
        return compact_matches[0]
    if not allow_fuzzy:
        return None
    suffix_matches = [m for m in metric_names if m.endswith(f"_{candidate}") or m.startswith(f"{candidate}_")]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    contains_matches = [m for m in metric_names if candidate in m]
    if len(contains_matches) == 1:
        return contains_matches[0]
    return None


def quote_bare_metric_references(metric_sql: str, metric_names: Optional[set[str]]) -> str:
    if not metric_names:
        return metric_sql
    normalized = metric_sql
    for metric_name in sorted(metric_names, key=len, reverse=True):
        pattern = rf'(?<![\w\.\"])\b{re.escape(metric_name)}\b(?![\w\."])'
        normalized = re.sub(pattern, f'"{metric_name}"', normalized)
    return normalized


def rewrite_metric_aggregate_wrappers(metric_sql: str, metric_names: Optional[set[str]]) -> str:
    if not metric_names:
        return metric_sql
    normalized = metric_sql
    agg_pattern = r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)(?!\s+OVER\b)'

    def _replace(match: re.Match) -> str:
        metric_name = match.group(2)
        if metric_name in metric_names:
            return f'"{metric_name}"'
        return match.group(0)

    normalized = re.sub(agg_pattern, _replace, normalized)
    composite_agg_pattern = r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*((?:"[A-Z_][A-Z0-9_]*"\s*[+\-*/]\s*)+"[A-Z_][A-Z0-9_]*")\s*\)'

    def _replace_composite(match: re.Match) -> str:
        expr = match.group(2)
        metric_refs = set(re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr))
        if metric_refs and all(ref in metric_names for ref in metric_refs):
            return expr
        return match.group(0)

    return re.sub(composite_agg_pattern, _replace_composite, normalized)


def prune_unresolved_metric_lines(metrics_lines: list[str], metric_name_set: Optional[set[str]]) -> list[str]:
    if not metrics_lines or not metric_name_set:
        return metrics_lines

    current = list(metrics_lines)
    while True:
        defined: set[str] = set()
        window_metrics: set[str] = set()
        parsed: list[tuple[str, Optional[str], Optional[str], str]] = []
        expr_by_name: dict[str, str] = {}
        for line in current:
            m = re.search(r'([A-Z_][A-Z0-9_]*)\."([^\"]+)"\s+AS\s+(.+?)\s*$', line.strip().rstrip(','))
            if not m:
                parsed.append((line, None, None, ""))
                continue
            alias = m.group(1)
            name = m.group(2)
            expr = m.group(3)
            defined.add(name)
            expr_by_name[name] = expr
            if re.search(r'\bOVER\b', expr, flags=re.IGNORECASE):
                window_metrics.add(name)
            parsed.append((line, alias, name, expr))

        removed = False
        rewritten = False
        next_lines: list[str] = []
        for line, alias, name, expr in parsed:
            if not name:
                next_lines.append(line)
                continue

            refs = set(re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr))
            unresolved = [r for r in refs if r in metric_name_set and r not in defined and r != name]
            if unresolved:
                removed = True
                continue

            window_refs = [r for r in refs if r in window_metrics and r != name]
            if window_refs:
                expanded_expr = expr
                substituted = False
                for ref_name in sorted(set(window_refs)):
                    ref_expr = expr_by_name.get(ref_name)
                    if not ref_expr:
                        continue
                    expanded_expr = re.sub(rf'"{re.escape(ref_name)}"', f'({ref_expr})', expanded_expr)
                    substituted = True
                if substituted:
                    next_lines.append(f'  {alias}."{name}" AS {expanded_expr}')
                    rewritten = True
                    continue
                removed = True
                continue

            next_lines.append(line)

        current = next_lines
        if not removed and not rewritten:
            return current


def normalize_date_part_arguments(metric_sql: str) -> str:
    normalized = metric_sql

    def _is_date_like(identifier: str) -> bool:
        upper = identifier.upper()
        return upper.endswith('.DATE') or upper.endswith('_DATE')

    def _wrap_try_to_date(match: re.Match) -> str:
        fn = match.group(1)
        arg = match.group(2).strip()
        if _is_date_like(arg) and 'TRY_TO_DATE(' not in arg.upper():
            return f"{fn}(TRY_TO_DATE({arg}))"
        return match.group(0)

    normalized = re.sub(r'\b(YEAR|MONTH|DAY|WEEK|QUARTER)\s*\(\s*([^\)]+)\)', _wrap_try_to_date, normalized, flags=re.IGNORECASE)

    def _wrap_extract(match: re.Match) -> str:
        part = match.group(1)
        arg = match.group(2).strip()
        if _is_date_like(arg) and 'TRY_TO_DATE(' not in arg.upper():
            return f"EXTRACT({part} FROM TRY_TO_DATE({arg}))"
        return match.group(0)

    normalized = re.sub(r'\bEXTRACT\s*\(\s*([A-Z_]+)\s+FROM\s+([^\)]+)\)', _wrap_extract, normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bDATE_PART\s*\(\s*['\"]*([A-Za-z_]+)['\"]*\s*,", lambda m: "DATE_PART('" + m.group(1).strip("\"'").lower() + "', ", normalized, flags=re.IGNORECASE)
    return normalized


def normalize_rolling_monthindex_max_predicates(metric_sql: str) -> str:
    normalized = metric_sql
    pattern = (
        r'(?P<id>[A-Z_][A-Z0-9_\.]+)\s*<=\s*MAX\(\s*(?P=id)\s*\)\s*'
        r'AND\s*(?P=id)\s*>\s*MAX\(\s*(?P=id)\s*\)\s*-\s*12'
    )

    def _replace(match: re.Match) -> str:
        month_index_id = match.group('id')
        return f"{month_index_id} > ((YEAR(CURRENT_DATE()) * 12) + MONTH(CURRENT_DATE()) - 12)"

    return re.sub(pattern, _replace, normalized, flags=re.IGNORECASE)
