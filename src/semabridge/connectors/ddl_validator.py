"""Semantic view DDL validator for Snowflake constraints."""
from __future__ import annotations

import re
from typing import List


def _extract_block(ddl: str, header: str) -> List[str]:
    word = header.split("(")[0].strip()
    m = re.search(rf"\b{re.escape(word)}\s*\(", ddl, re.I)
    if not m:
        return []
    d, i, q = 1, m.end(), False
    while i < len(ddl) and d:
        ch = ddl[i]
        if ch == "'":
            q = not q
        elif not q:
            d += {"(": 1, ")": -1}.get(ch, 0)
        i += 1
    inner = ddl[m.end():i - 1]
    return [l.strip() for l in inner.splitlines() if l.strip()]


def validate_semantic_ddl(ddl: str) -> List[str]:
    errors: List[str] = []
    if not ddl:
        errors.append("DDL is empty")
        return errors

    # Basic parentheses balance (ignoring string literals and comments)
    clean_ddl = re.sub(r"'(?:[^']|'')*'", "''", ddl)
    clean_ddl = re.sub(r"--.*$", "", clean_ddl, flags=re.MULTILINE)
    clean_ddl = re.sub(r"/\*.*?\*/", "", clean_ddl, flags=re.DOTALL)
    if clean_ddl.count("(") != clean_ddl.count(")"):
        errors.append("Unbalanced parentheses in DDL")

    metrics = _extract_block(ddl, "METRICS (")
    forbidden = (" SELECT ", " FROM ", " JOIN ", " WITH ", " UNION ", ";")
    for line in metrics:
        # Strip COMMENT and string literals to avoid matching words like 'with' in comments
        clean_line = re.sub(r"(?i)\bCOMMENT\s*=\s*'(?:[^']|'')*'", '', line)
        clean_line = re.sub(r"'(?:[^']|'')*'", "''", clean_line)
        # Strip WITH SYNONYMS to avoid matching forbidden ' WITH ' CTE keyword
        clean_line = re.sub(r"(?i)\bWITH\s+SYNONYMS\s*=\s*\(.*?\)", '', clean_line)
        clean_line = re.sub(r"(?i)\bWITH\s+SYNONYMS\b.*", '', clean_line)
        upper = f" {clean_line.upper()} "
        if any(tok in upper for tok in forbidden):
            errors.append(f"Non-scalar SQL detected in METRICS: {line[:160]}")
            break

    # Granularity / reachability pre-deploy check (Snowflake 010219):
    # A metric's anchor table must reach (many -> one via RELATIONSHIPS) every table whose columns it aggregates.
    tables_block = _extract_block(ddl, "TABLES (")
    known_tables = set()
    for line in tables_block:
        line_clean = line.strip().rstrip(",")
        as_m = re.match(r'^\s*([A-Za-z0-9_]+)\s+AS\s+', line_clean, re.I)
        if as_m:
            known_tables.add(as_m.group(1).upper())
        else:
            first = line_clean.split()[0].split(".")[-1].strip('"').upper() if line_clean else ""
            if first:
                known_tables.add(first)

    rels_block = _extract_block(ddl, "RELATIONSHIPS (")
    reach_graph: dict[str, set[str]] = {}
    for line in rels_block:
        m = re.search(r'AS\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s+REFERENCES\s+([A-Za-z0-9_]+)\s*\(', line, re.I)
        if m:
            from_tbl, to_tbl = m.group(1).upper(), m.group(2).upper()
            reach_graph.setdefault(from_tbl, set()).add(to_tbl)

    for line in metrics:
        m = re.match(r'^\s*([A-Za-z0-9_]+)\.(?:"([^"]+)"|([A-Za-z0-9_]+))(?:\s+NON\s+ADDITIVE\s+BY\s*\([^)]*\))?\s+AS\s+(.*)$', line, re.I | re.S)
        if not m:
            continue
        anchor_alias = m.group(1).upper()
        metric_name = m.group(2) or m.group(3)
        expr = m.group(4)
        expr_clean = re.sub(r"(?i)\bCOMMENT\s*=\s*'(?:[^']|'')*'", '', expr)
        expr_clean = re.sub(r"'(?:[^']|'')*'", "''", expr_clean)
        if re.match(r'^\s*(?:CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*$', expr_clean.strip().rstrip(","), re.I):
            continue

        agg_tables = set()
        for agg_m in re.finditer(r'\b(?:SUM|AVG|COUNT|MIN|MAX|MEDIAN)\s*\((.*?)\)', expr_clean, re.I):
            agg_body = agg_m.group(1)
            for t_ref in re.findall(r'([A-Za-z0-9_]+)\."?[A-Za-z0-9_]+"?', agg_body):
                t_ref_u = t_ref.upper()
                if t_ref_u in known_tables and t_ref_u != anchor_alias:
                    agg_tables.add(t_ref_u)

        if agg_tables:
            reachable = {anchor_alias}
            stack = [anchor_alias]
            while stack:
                curr = stack.pop()
                for nxt in reach_graph.get(curr, ()):
                    if nxt not in reachable:
                        reachable.add(nxt)
                        stack.append(nxt)
            unreachable = sorted(t for t in agg_tables if t not in reachable)
            if unreachable:
                errors.append(
                    f"Metric '{anchor_alias}.{metric_name}': aggregates columns from {unreachable}, not reachable (many -> one) from '{anchor_alias}'. "
                    f"Snowflake 010219: metric must sit on the 'many' side table that reaches all aggregated tables."
                )

    return errors
