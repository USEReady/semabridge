"""Semantic view DDL validator for Snowflake constraints."""
from __future__ import annotations

import re
from typing import Any, List, Optional


def _extract_block(ddl: str, header: str) -> List[str]:
    lines = ddl.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().upper().startswith(header):
            start = i
            break
    if start is None:
        return []
    end = len(lines)
    depth = 0
    for i in range(start, len(lines)):
        line = lines[i]
        depth += line.count("(") - line.count(")")
        if i > start and depth <= 0:
            end = i
            break
    return [l.strip() for l in lines[start + 1:end] if l.strip()]


def validate_logical_references(ddl: str) -> List[str]:
    """Validate that every logical column referenced in an expression
    is declared in its table (under FACTS or DIMENSIONS), or in a related
    table via relationships.
    """
    errors: List[str] = []
    if not ddl:
        return errors

    declared_tables: set[str] = set()
    for l in _extract_block(ddl, "TABLES ("):
        m = re.match(r'(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+)\s+AS\s+', l, re.IGNORECASE)
        if m:
            declared_tables.add(m.group(1).upper())

    relationships: dict[str, set[str]] = {}
    rel_aliases: dict[str, str] = {}
    for l in _extract_block(ddl, "RELATIONSHIPS ("):
        m = re.match(r'([A-Za-z0-9_]+)\s+AS\s+([A-Za-z0-9_]+)\s*\(.*?\)\s*REFERENCES\s*([A-Za-z0-9_]+)', l, re.IGNORECASE)
        if m:
            rel_name, from_tbl, to_tbl = m.group(1).upper(), m.group(2).upper(), m.group(3).upper()
            relationships.setdefault(from_tbl, set()).add(to_tbl)
            rel_aliases[rel_name] = to_tbl

    declared_columns: dict[str, set[str]] = {t: set() for t in declared_tables}
    col_pattern = re.compile(
        r'(?:(?:PRIVATE|PUBLIC)\s+)?([A-Za-z0-9_]+)\.("[^"]+"|\[[^\]]+\]|[A-Za-z0-9_]+)\s+AS\s+(?:[A-Za-z0-9_]+\.)?("[^"]+"|\[[^\]]+\]|[A-Za-z0-9_]+)',
        re.IGNORECASE,
    )
    for l in _extract_block(ddl, "FACTS ("):
        m = col_pattern.match(l)
        if m:
            tbl = m.group(1).upper()
            raw_log = m.group(2).strip('"\\[\\]')
            raw_phys = m.group(3).strip('"\\[\\]')
            for val in (raw_log, raw_phys, re.sub(r'[^A-Za-z0-9]', '', raw_log), re.sub(r'[^A-Za-z0-9]', '', raw_phys)):
                if val:
                    declared_columns.setdefault(tbl, set()).add(val.upper())

    for l in _extract_block(ddl, "DIMENSIONS ("):
        m = col_pattern.match(l)
        if m:
            tbl = m.group(1).upper()
            raw_log = m.group(2).strip('"\\[\\]')
            raw_phys = m.group(3).strip('"\\[\\]')
            for val in (raw_log, raw_phys, re.sub(r'[^A-Za-z0-9]', '', raw_log), re.sub(r'[^A-Za-z0-9]', '', raw_phys)):
                if val:
                    declared_columns.setdefault(tbl, set()).add(val.upper())

    declared_metrics: dict[str, set[str]] = {t: set() for t in declared_tables}
    metric_exprs: list[tuple[str, str, str]] = []
    metric_pattern = re.compile(
        r'([A-Za-z0-9_]+)\.("[^"]+"|\[[^\]]+\]|[A-Za-z0-9_]+)\s+AS\s+(.+)',
        re.IGNORECASE,
    )
    for l in _extract_block(ddl, "METRICS ("):
        m = metric_pattern.match(l)
        if m:
            tbl = m.group(1).upper()
            raw_col = m.group(2).strip('"\\[\\]').upper()
            expr = m.group(3)
            declared_metrics.setdefault(tbl, set()).add(raw_col)
            clean_m = re.sub(r'[^A-Za-z0-9]', '', raw_col)
            if clean_m:
                declared_metrics.setdefault(tbl, set()).add(clean_m)
            metric_exprs.append((tbl, raw_col, expr))

    def _get_reachable(tbl: str, visited: set | None = None) -> set:
        visited = visited or set()
        reachable = set()
        for target in relationships.get(tbl, set()):
            if target not in visited:
                visited.add(target)
                reachable.add(target)
                reachable.update(_get_reachable(target, visited))
        return reachable

    ref_pattern = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*|"[^"]+")', re.IGNORECASE)
    seen_errors: set[tuple[str, str, str]] = set()
    for tbl, metric_name, expr in metric_exprs:
        clean_expr = re.sub(r'(?i)\bWITH\s+SYNONYMS\s*=\s*\(.*?\)', '', expr)
        clean_expr = re.sub(r'(?i)\bWITH\s+SYNONYMS\b.*', '', clean_expr)
        for m in ref_pattern.finditer(clean_expr):
            ref_tbl = m.group(1).upper()
            ref_col = m.group(2).strip('"').upper()
            clean_ref_col = re.sub(r'[^A-Za-z0-9]', '', ref_col)

            if ref_tbl in declared_tables:
                tbl_cols = declared_columns.get(ref_tbl, set())
                tbl_mets = declared_metrics.get(ref_tbl, set())
                if ref_col in tbl_cols or clean_ref_col in tbl_cols:
                    continue
                if ref_col in tbl_mets or clean_ref_col in tbl_mets:
                    continue
                reachable = _get_reachable(ref_tbl)
                if any(ref_col in declared_columns.get(r, set()) or clean_ref_col in declared_columns.get(r, set())
                       or ref_col in declared_metrics.get(r, set()) or clean_ref_col in declared_metrics.get(r, set())
                       for r in reachable):
                    continue
                err_key = (metric_name, ref_tbl, ref_col)
                if err_key not in seen_errors:
                    seen_errors.add(err_key)
                    errors.append(
                        f"Calculation referred to undefined logical column name '{ref_col}' in table '{ref_tbl}' (in '{metric_name}')"
                    )
            elif ref_tbl in rel_aliases:
                target_tbl = rel_aliases[ref_tbl]
                target_cols = declared_columns.get(target_tbl, set())
                target_mets = declared_metrics.get(target_tbl, set())
                if (ref_col not in target_cols and clean_ref_col not in target_cols
                    and ref_col not in target_mets and clean_ref_col not in target_mets):
                    err_key = (metric_name, ref_tbl, ref_col)
                    if err_key not in seen_errors:
                        seen_errors.add(err_key)
                        errors.append(
                            f"Calculation referred to undefined logical column name '{ref_col}' via relationship '{ref_tbl}' (in '{metric_name}')"
                        )

    return errors


def validate_semantic_ddl(ddl: str) -> List[str]:
    errors: List[str] = []
    if not ddl:
        errors.append("DDL is empty")
        return errors

    # Basic parentheses balance
    if ddl.count("(") != ddl.count(")"):
        errors.append("Unbalanced parentheses in DDL")

    metrics = _extract_block(ddl, "METRICS (")
    forbidden = (" SELECT ", " FROM ", " JOIN ", " WITH ", " UNION ", ";")
    for line in metrics:
        # Strip WITH SYNONYMS to avoid matching forbidden " WITH " CTE keyword
        clean_line = re.sub(r'(?i)\bWITH\s+SYNONYMS\s*=\s*\(.*?\)', '', line)
        clean_line = re.sub(r'(?i)\bWITH\s+SYNONYMS\b.*', '', clean_line)
        upper = f" {clean_line.upper()} "
        if any(tok in upper for tok in forbidden):
            errors.append(f"Non-scalar SQL detected in METRICS: {line[:160]}")
            break

    # Static check: every column referenced in any expression must be declared
    logical_ref_errors = validate_logical_references(ddl)
    errors.extend(logical_ref_errors)

    return errors


def validate_cortex_analyst(
    connection: Any,
    host: str,
    semantic_view: str,
    question: str = "What are total units?",
) -> tuple[bool, Optional[str]]:
    """Test step: send a simple question through Cortex Analyst API to verify the view.

    Returns (True, None) if successful, or (False, error_message) if Cortex Analyst rejects it.
    """
    import requests

    token = getattr(getattr(connection, "_rest", None), "token", None)
    if not token:
        return False, "Snowflake connection does not contain an active REST token"

    url = f"https://{host.replace('https://', '').strip('/')}/api/v2/cortex/analyst/message"
    headers = {
        "Authorization": f'Snowflake Token="{token}"',
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": question}]}
        ],
        "semantic_view": semantic_view,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            return False, f"Cortex Analyst HTTP {resp.status_code}: {resp.text}"
        data = resp.json()
        if "error_code" in data:
            return False, f"Cortex Analyst error {data.get('error_code')}: {data.get('message')}"
        return True, None
    except Exception as exc:
        return False, f"Cortex Analyst request exception: {exc}"

