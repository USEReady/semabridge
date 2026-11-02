#!/usr/bin/env python3
"""
test_row_counts.py - issue 1: a DAX ROW count (COUNTROWS, COUNTX/COUNTAX(..., TRUE())) must be SQL COUNT(*),
COUNT_IF(cond) or COUNT(CASE WHEN cond THEN 1 END) - never COUNT(CASE WHEN cond THEN <column> END).
A DAX COLUMN count (COUNT/COUNTA/COUNTAX/COUNTX with a column) may count that same column.

MODE ddl (offline, any model):
  uv run python test_row_counts.py ddl --pbix "<model>.pbix" --ddl "<view>.sql"   [repeat pairs]
  R1  metric whose DAX counts rows emits THEN <column>                                    -> FAIL
  R2  THEN <column> where the DAX counts a column, but a different one                     -> FAIL
  R3  THEN <column> where the DAX counts that column (e.g. COUNT([Opportunity ID]))        -> PASS (expected)
MODE sf (live, read-only):
  uv run python test_row_counts.py sf --pbix "<model>.pbix" --ddl "<view>.sql" --view DB.SCHEMA.VIEW [--repo .]
  - impact: for every column counted in place of rows: COUNT(*) vs COUNT(column) on the base table
  - value : metric from SEMANTIC_VIEW vs the same expression with THEN <column> -> THEN 1 on the base table
Exit 1 on any FAIL.
"""
import argparse, importlib, os, re, sys, warnings
warnings.filterwarnings("ignore")

_REP = {"%": "PCT", "$": "DOL", "&": "AND", "#": "NUM"}
norm = lambda s: re.sub(r"[^A-Z0-9]", "", "".join(_REP.get(c, c) for c in str(s)).upper())
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


def parse_ddl(text):
    phys = {}
    for t in split_top(block(text, "tables")):
        m = re.match(r'(?:("?\w+"?)\s+as\s+)?([\w."]+)', t, re.I)
        if m:
            phys[U(m.group(1) or m.group(2).split(".")[-1])] = m.group(2)
    metrics = {}
    for line in split_top(block(text, "metrics")):
        m = re.match(r'(?:PRIVATE\s+)?("?\w+"?)\s*\.\s*("?\w+"?)\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
        if m:
            expr = re.sub(r"\s+comment\s*=\s*'(?:[^']|'')*'\s*$", "", m.group(3), flags=re.I | re.S)
            metrics[U(m.group(2))] = (U(m.group(1)), expr.strip())
    return phys, metrics


def load_dax(pbix):
    from pbixray import PBIXRay
    md = PBIXRay(pbix).dax_measures
    return {r["Name"]: str(r["Expression"] or "") for _, r in md.iterrows()}


def inline(expr, measures, depth=0):
    if depth > 6:
        return expr
    return re.sub(r"(?<![\w'\]])\[([^\]]+)\]",
                  lambda m: "(" + inline(measures[m.group(1)], measures, depth + 1) + ")" if m.group(1) in measures else m.group(0), expr)


def dax_counts(full):
    """returns (counts_rows: bool, counted_columns: set of normalised column names)"""
    rows = bool(re.search(r"\bCOUNTROWS\s*\(", full, re.I)) or bool(re.search(r"\bCOUNTA?X\s*\([^,]+,\s*TRUE\s*\(\s*\)\s*\)", full, re.I))
    cols = set()
    for m in re.finditer(r"\b(COUNT|COUNTA)\s*\(\s*(?:'?[^'\[\(]*'?)?\[([^\]]+)\]\s*\)", full, re.I):
        cols.add(norm(m.group(2)))
    for m in re.finditer(r"\bCOUNTA?X\s*\(.*?,\s*(?:'?[^'\[\(,]*'?)?\[([^\]]+)\]\s*\)", full, re.I | re.S):
        cols.add(norm(m.group(1)))
    return rows, cols


THEN_COL = re.compile(r'\bCOUNT\s*\(\s*CASE\s+WHEN\s+(.*?)\s+THEN\s+("?\w+"?\s*\.\s*"?(\w+)"?)\s+(?:ELSE\s+NULL\s+)?END\s*\)', re.I | re.S)


def check(pbix, ddl_path):
    dax = load_dax(pbix)
    _, metrics = parse_ddl(open(ddl_path, encoding="utf-8", errors="ignore").read())
    by_norm = {norm(k): k for k in dax}
    fails, flagged = 0, []
    print(f"\n=== {os.path.basename(pbix)}  vs  {os.path.basename(ddl_path)}")
    for mname, (table, expr) in metrics.items():
        hits = THEN_COL.findall(expr)
        if not hits:
            continue
        dname = by_norm.get(norm(mname))
        full = inline(dax[dname], dax) if dname else ""
        rows, cols = dax_counts(full)
        seen = set()
        for cond, ref, col in hits:
            if (col, cond) in seen:
                continue
            seen.add((col, cond))
            c = norm(col)
            # SemaBridge prefixes reserved-word columns with COL_ (Value -> COL_VALUE)
            if c in cols or (c.startswith("COL") and c[3:] in cols):
                print(f"  ok   R3 {mname:30} THEN {col}: the DAX counts this column")
                continue
            if rows or not dname:
                print(f"  FAIL R1 {mname:30} DAX counts ROWS but SQL counts column {col} (rows with empty {col} are lost)")
            else:
                print(f"  FAIL R2 {mname:30} SQL counts {col}, DAX counts {sorted(cols) or 'something else'}")
            fails += 1
            flagged.append((table, mname, expr, ref, col))
    print(f"  -> {fails} FAIL")
    return fails, flagged


def connect(a):
    import snowflake.connector
    if a.connection_name:
        return snowflake.connector.connect(connection_name=a.connection_name)
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
    return snowflake.connector.connect(**found["get_snowflake_connect_kwargs"](found["get_settings"]().snowflake))


def live(a, ddl_path, flagged):
    phys, _ = parse_ddl(open(ddl_path, encoding="utf-8", errors="ignore").read())
    con = connect(a); cur = con.cursor(); bad = 0
    for table, mname, expr, ref, col in flagged:
        base = phys.get(table, table)
        cur.execute(f'SELECT COUNT(*), COUNT("{col}") FROM {base}')
        n_all, n_col = cur.fetchone()
        print(f"  impact {mname}: {table} rows={n_all}, rows with {col}={n_col}" + ("  (equal - correct today by luck)" if n_all == n_col else "  (DIFFERENT - metric is too low now)"))
        truth_expr = THEN_COL.sub(lambda m: f"COUNT(CASE WHEN {m.group(1)} THEN 1 END)", expr)
        try:
            cur.execute(f"SELECT {truth_expr} FROM {base} AS {table}")
            truth = cur.fetchone()[0]
            cur.execute(f"SELECT * FROM SEMANTIC_VIEW({a.view} METRICS {table}.{mname})")
            got = cur.fetchone()[0]
        except Exception as exc:
            print(f"  skip value check {mname}: {str(exc).splitlines()[0][:120]}"); continue
        ok = got is not None and truth is not None and abs(float(got) - float(truth)) < 1e-9
        bad += not ok
        print(f"  {'PASS' if ok else 'FAIL'} value {mname}: view={got}  rows-based truth={truth}")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["ddl", "sf"])
    ap.add_argument("--pbix", action="append", default=[]); ap.add_argument("--ddl", action="append", default=[])
    ap.add_argument("--view"); ap.add_argument("--repo", default="."); ap.add_argument("--connection-name")
    a = ap.parse_args()
    if len(a.pbix) != len(a.ddl) or not a.pbix:
        sys.exit("give one --ddl per --pbix")
    total = 0
    for p, d in zip(a.pbix, a.ddl):
        f, flagged = check(p, d)
        total += f
        if a.mode == "sf":
            if not a.view:
                sys.exit("--view required for sf")
            total += live(a, d, flagged)
    print("\nALL PASS" if not total else f"\n{total} FAIL")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
