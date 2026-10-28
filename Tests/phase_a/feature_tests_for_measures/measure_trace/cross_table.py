"""
cross_table.py - DuckDB support for cross-table cases (used by both runners). Generic.

  * enriched copies: for every ACTIVE relationship fact -> dim, the fact table also gets the dim's columns
    as <DIM>_<COLUMN> (SemaBridge's enriched-view convention), so either form of a dimension filter runs:
    a join (dim.COL) or an enriched copy (fact.DIM_COL).
  * inactive relationships: an alias view per inactive relationship named alias("<Dim> <FK column>")
    (e.g. calendar_ship_date), joined on that FK column. A USERELATIONSHIP measure must use this alias.
  * joins: FROM = the referenced table that reaches every other referenced table through relationships.
"""
import re
from collections import deque


def build(con, TABLES, RELATIONSHIPS, INACTIVE, alias, sb_column):
    for t, df in TABLES.items():
        d = df.copy(); d.columns = [sb_column(c) for c in d.columns]
        con.register("_tmp", d)
        con.execute(f'CREATE TABLE "{alias(t)}" AS SELECT * FROM _tmp')
        con.unregister("_tmp")
    # enriched copies
    for ft, fc, tt, tc in RELATIONSHIPS:
        fa, ta = alias(ft), alias(tt)
        existing = {c.upper() for c in TABLES[ft].columns.map(sb_column)}
        extra = []
        for col in TABLES[tt].columns:
            if col == tc:
                continue
            new = f"{sb_column(tt)}_{sb_column(col)}"
            if new.upper() not in existing:
                extra.append(f'd."{sb_column(col)}" AS "{new}"')
                existing.add(new.upper())
        if extra:
            con.execute(f'CREATE OR REPLACE TABLE "{fa}" AS SELECT f.*, {", ".join(extra)} FROM "{fa}" f '
                        f'LEFT JOIN "{ta}" d ON f."{sb_column(fc)}" = d."{sb_column(tc)}"')
    edges = [(alias(ft), sb_column(fc), alias(tt), sb_column(tc)) for ft, fc, tt, tc in RELATIONSHIPS]
    for ft, fc, tt, tc in INACTIVE:
        a = inactive_alias(alias, tt, fc)
        con.execute(f'CREATE VIEW "{a}" AS SELECT * FROM "{alias(tt)}"')
        edges.append((alias(ft), sb_column(fc), a, sb_column(tc)))
    return edges


def inactive_alias(alias, dim_table, fk_column):
    return alias(f"{dim_table} {fk_column}")


def _path(edges, start, target):
    prev, dq = {start: None}, deque([start])
    while dq:
        cur = dq.popleft()
        for e in edges:
            if e[0] == cur and e[2] not in prev:
                prev[e[2]] = e; dq.append(e[2])
    if target not in prev:
        return None
    out, n = [], target
    while prev[n]:
        out.append(prev[n]); n = prev[n][0]
    return list(reversed(out))


def from_clause(sql, all_aliases, edges, default, force=()):
    used = [a for a in all_aliases if re.search(rf'(?<![\w"]){re.escape(a)}"?\s*\.', sql, re.I)]
    for f in force:
        if f not in used:
            used.append(f)
    if not used:
        return f'"{default}"', used
    base = next((u for u in used if all(_path(edges, u, o) is not None for o in used if o != u)), used[0])
    frm, joined = f'"{base}"', {base}
    for o in used:
        if o in joined:
            continue
        p = _path(edges, base, o)
        if p is None:
            raise ValueError(f"no relationship path from {base} to {o}")
        for fa, fc, ta, tc in p:
            if ta not in joined:
                frm += f' LEFT JOIN "{ta}" ON "{fa}"."{fc}" = "{ta}"."{tc}"'
                joined.add(ta)
    return frm, used
