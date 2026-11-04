#!/usr/bin/env python3
"""
feature_tests/./test_window_exclusions.py - checks window metrics in any semantic-view DDL.

  E1  every PARTITION BY EXCLUDING name is a declared dimension
  E2  it is NOT an enriched copy on another table (<OTHER_TABLE>_<COL>, e.g. SALES.STORE_STORE, INDICATORS.YEAR_YEAR)
      -> must be the dimension-table dimension (STORE.STORE, YEAR.YEAR)
  E3  the excluded dimension's table is reachable from the metric's table (relationships, many-to-one direction)
  E4  metrics that embed the same window use the same exclusion list (no mix of copy and real dimension)
  E5  COMMENT text is escaped once (no '''' sequences)
usage:
  uv run python test_window_exclusions.py <view1>.sql [<view2>.sql ...]            # offline
  uv run python test_window_exclusions.py <view>.sql --run --view DB.SCHEMA.VIEW [--connection-name X] [--repo .]
      -> also queries each window metric WITH its required dimensions in Snowflake (must not raise 010277/010259)
Exit 1 on FAIL.
"""
import argparse, importlib, os, re, sys
from collections import defaultdict

U = lambda s: s.strip().strip('"').upper()


def block(text, word):
    m = re.search(rf"\b{word}\s*\(", text, re.I)
    if not m:
        return ""
    d, i = 1, m.end()
    while i < len(text) and d:
        d += {"(": 1, ")": -1}.get(text[i], 0); i += 1
    return text[m.end():i - 1]


def split_top(s):
    out, d, cur, q = [], 0, [], False
    for ch in s:
        if ch == "'":
            q = not q
        if not q:
            d += {"(": 1, ")": -1}.get(ch, 0)
            if ch == "," and d == 0:
                out.append("".join(cur)); cur = []; continue
        cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def parse(text):
    aliases = set()
    for t in split_top(block(text, "tables")):
        m = re.match(r'(?:("?\w+"?)\s+as\s+)?([\w."]+)', t, re.I)
        if m:
            aliases.add(U(m.group(1) or m.group(2).split(".")[-1]))
    edges = defaultdict(set)                     # many -> one
    for r in split_top(block(text, "relationships")):
        m = re.search(r'("?\w+"?)\s*\([^)]*\)\s*references\s+("?\w+"?)', r, re.I)
        if m:
            edges[U(m.group(1))].add(U(m.group(2)))
    dims = {}                                    # ALIAS.NAME -> column
    for d in split_top(block(text, "dimensions")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?\w+"?)\s+as\s+("?\w+"?)\s*\.\s*("?[\w$]+"?)', d, re.I)
        if m:
            dims[f"{U(m.group(1))}.{U(m.group(2))}"] = U(m.group(4))
    metrics = {}
    for line in split_top(block(text, "metrics")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?\w+"?)\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
        if m:
            metrics[U(m.group(2))] = (U(m.group(1)), m.group(3))
    return aliases, edges, dims, metrics


def reachable(edges, src, dst):
    seen, stack = {src}, [src]
    while stack:
        n = stack.pop()
        if n == dst:
            return True
        for x in edges.get(n, ()):
            if x not in seen:
                seen.add(x); stack.append(x)
    return False


def exclusions(expr):
    out = []
    for m in re.finditer(r"PARTITION\s+BY\s+EXCLUDING\s+(.*?)\)", expr, re.I | re.S):
        out.append(tuple(sorted(f"{U(a)}.{U(b)}" for a, b in re.findall(r'"?(\w+)"?\s*\.\s*"?([\w$]+)"?', m.group(1)))))
    return out


def check(path):
    text = open(path, encoding="utf-8", errors="ignore").read()
    aliases, edges, dims, metrics = parse(text)
    dim_cols = defaultdict(set)
    for k, col in dims.items():
        dim_cols[k.split(".")[0]].add(col)
    fails = 0
    print(f"\n=== {os.path.basename(path)}")
    groups = defaultdict(set)
    plan = []
    for name, (table, body) in metrics.items():
        expr = re.sub(r"\s+comment\s*=\s*'.*$", "", body, flags=re.I | re.S)
        exs = exclusions(expr)
        for ex in exs:
            groups[tuple(sorted({e.split('.')[1] for e in ex}))].add((name, ex))
            for e in ex:
                a, n = e.split(".")
                if e not in dims:
                    print(f"  FAIL E1 {name}: {e} is not a declared dimension"); fails += 1; continue
                col = dims[e]
                copy_of = [o for o in aliases if o != a and col.startswith(o + "_") and col[len(o) + 1:] in dim_cols.get(o, set())]
                if copy_of:
                    print(f"  FAIL E2 {name}: {e} is an enriched copy of {copy_of[0]}.{col[len(copy_of[0]) + 1:]} - exclude the dimension-table dimension"); fails += 1
                if a != table and not reachable(edges, table, a):
                    print(f"  FAIL E3 {name}: {a} not reachable from {table}"); fails += 1
        if exs:
            plan.append((table, name, sorted({e for ex in exs for e in ex})))
        if "''''" in body:
            print(f"  FAIL E5 {name}: COMMENT escaped twice (''''')"); fails += 1
    # E4: same window column set on different dimension tables (copy vs real)
    by_col = defaultdict(set)
    for _, members in groups.items():
        for name, ex in members:
            for e in ex:
                a, n = e.split(".")
                col = dims.get(e, n)
                base = next((col[len(o) + 1:] for o in aliases if o != a and col.startswith(o + "_")), col)
                by_col[base].add(e)
    for base, variants in by_col.items():
        if len(variants) > 1:
            print(f"  FAIL E4 same column excluded under different names: {sorted(variants)}"); fails += 1
    print(f"  -> {len(plan)} window metric(s), {fails} FAIL")
    return fails, plan


def live(plan, a):
    import snowflake.connector
    if a.connection_name:
        con = snowflake.connector.connect(connection_name=a.connection_name)
    else:
        sys.path.insert(0, os.path.join(os.path.abspath(a.repo), "src"))
        found = {}
        for mod in ("semabridge.config", "semabridge.core.settings", "semabridge.settings", "semabridge.connectors.snowflake_connection"):
            try:
                mm = importlib.import_module(mod)
            except Exception:
                continue
            for fn in ("get_settings", "get_snowflake_connect_kwargs"):
                if fn not in found and hasattr(mm, fn):
                    found[fn] = getattr(mm, fn)
        if len(found) < 2:
            sys.exit("use --connection-name")
        con = snowflake.connector.connect(**found["get_snowflake_connect_kwargs"](found["get_settings"]().snowflake))
    bad = 0
    for table, name, req in plan:
        sql = f"SELECT * FROM SEMANTIC_VIEW({a.view} DIMENSIONS {', '.join(req)} METRICS {table}.{name}) LIMIT 5"
        try:
            cur = con.cursor(); cur.execute(sql); cur.fetchall()
            print(f"  PASS query {name} with {req}")
        except Exception as exc:
            bad += 1; print(f"  FAIL query {name}: {str(exc).splitlines()[0][:150]}")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ddl", nargs="+"); ap.add_argument("--run", action="store_true"); ap.add_argument("--view")
    ap.add_argument("--connection-name"); ap.add_argument("--repo", default=".")
    a = ap.parse_args()
    total = 0
    for p in a.ddl:
        f, plan = check(p)
        total += f
        if a.run:
            total += live(plan, a)
    print("\nALL PASS" if not total else f"\n{total} FAIL")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()

