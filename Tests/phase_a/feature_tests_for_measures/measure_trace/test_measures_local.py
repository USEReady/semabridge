#!/usr/bin/env python3
"""
test_measures_local.py - check generated metric SQL against the DAX meaning, fully LOCAL (no Snowflake).

How: the tables inside the .pbix are loaded into DuckDB under the view's table aliases and sanitized column names;
each metric expression from the DDL is evaluated there (grand total) and compared with the expected value computed
from the same data with the DAX meaning (pandas). Window metrics (OVER ... EXCLUDING) are skipped - check those in
Snowflake.

usage:
  pip install duckdb pbixray
  uv run python test_measures_local.py --pbix "Regional Sales Sample.pbix" --ddl <regional sales view>.sql
  uv run python test_measures_local.py --pbix ... --ddl ... --show-sql      # print the SQL that was run
Expected values exist for: Regional Sales (EXPECTED_REGIONAL). Add a dict for another model the same way.
Exit 1 on any FAIL.
"""
import argparse, math, re, sys, warnings
warnings.filterwarnings("ignore")

_REP = {"%": "PCT", "$": "DOL", "&": "AND", "#": "NUM"}
norm = lambda s: re.sub(r"[^A-Z0-9]", "", "".join(_REP.get(c, c) for c in str(s)).upper())
U = lambda s: s.strip().strip('"').upper()


# ---------------------------------------------------------------- expected values (DAX meaning, slider = default)
def mround(x, m):
    return math.copysign(math.floor(abs(x) / m + 0.5) * m, x)


def EXPECTED_REGIONAL(t):
    o = t["Opportunities"]
    st, val = o["Status"], o["Value"].astype("float")
    stage = o["Sales Stage"].astype("string").str[:1].astype("float")
    adj = 0.0                                                  # SELECTEDVALUE(..., 0) with nothing selected
    won = float(val[st == "Won"].sum())
    rip_raw = float(val[(st == "Open") & (stage >= 2)].sum())
    rip = rip_raw + rip_raw * adj / 100
    ropen_raw = float(val[st == "Open"].sum()); ropen = ropen_raw + ropen_raw * adj / 100
    base = mround(won + rip_raw * 0.6, 1_000_000)
    goal = base if base > 0 else mround(won + rip_raw * 0.6, 100_000)
    n_won = float(o.loc[st == "Won", "OpportunitySeq"].notna().sum())
    n_lost = float(o.loc[st == "Lost", "OpportunitySeq"].notna().sum())
    close = n_won / (n_won + n_lost)
    return {
        "Revenue Won": won, "Revenue In Pipeline": rip, "Revenue Open": ropen,
        "Forecast": won + rip, "Rev Goal": goal, "Forecast %": (won + rip) / goal,
        "Count of Won": n_won, "Count of Lost": n_lost, "Close %": close,
        "Forecast by Win/Loss Ratio": ropen * close,
        "Opportunity Count": float(len(o)),
        "Opportunity Count In Pipeline": float(o.loc[st == "Open", "Value"].notna().sum()),
        "Forecast Adjustment Value": adj,
        "Revenue Won Average Deal Size": float(val[st == "Won"].mean()),
        "Forecast Adjustment Slicer Alt Text": "TEXT",          # text measure -> NULL with reason is correct
    }


EXPECTED = {"REGIONAL": EXPECTED_REGIONAL}


# ---------------------------------------------------------------- DDL parsing
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
    tables = {}
    for t in split_top(block(text, "tables")):
        m = re.match(r'(?:("?\w+"?)\s+as\s+)?([\w."]+)', t, re.I)
        if m:
            tables[U(m.group(1) or m.group(2).split(".")[-1])] = U(m.group(2).split(".")[-1])
    rels = []
    for r in split_top(block(text, "relationships")):
        m = re.search(r'("?\w+"?)\s*\(([^)]*)\)\s*references\s+("?\w+"?)\s*\(([^)]*)\)', r, re.I)
        if m:
            rels.append((U(m.group(1)), U(m.group(2)), U(m.group(3)), U(m.group(4))))
    metrics = {}
    for line in split_top(block(text, "metrics")):
        m = re.match(r'(?:PRIVATE\s+)?("?\w+"?)\s*\.\s*("?\w+"?)\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
        if m:
            expr = re.sub(r"\s+comment\s*=\s*'(?:[^']|'')*'\s*$", "", m.group(3), flags=re.I | re.S).strip()
            metrics[U(m.group(2))] = (U(m.group(1)), expr)
    return tables, rels, metrics


# ---------------------------------------------------------------- Snowflake -> DuckDB
def call_args(s, start):
    d, i = 1, start
    while i < len(s) and d:
        d += {"(": 1, ")": -1}.get(s[i], 0); i += 1
    return s[start:i - 1], i


def rewrite_fn(sql, name, fn):
    out, pos = [], 0
    for m in re.finditer(rf"\b{name}\s*\(", sql, re.I):
        if m.start() < pos:
            continue
        inner, end = call_args(sql, m.end())
        out.append(sql[pos:m.start()]); out.append(fn(rewrite_fn(inner, name, fn))); pos = end
    out.append(sql[pos:])
    return "".join(out)


def to_duckdb(sql):
    sql = rewrite_fn(sql, "TRY_TO_NUMBER", lambda a: f"TRY_CAST({a} AS DOUBLE)")
    sql = rewrite_fn(sql, "IFF", lambda a: f"(CASE WHEN {split_top(a)[0]} THEN {split_top(a)[1]} ELSE {split_top(a)[2]} END)")
    sql = rewrite_fn(sql, "DIV0NULL", lambda a: f"(CASE WHEN COALESCE({split_top(a)[1]}, 0) = 0 THEN 0 ELSE ({split_top(a)[0]}) / ({split_top(a)[1]}) END)")
    sql = rewrite_fn(sql, "DIV0", lambda a: f"(CASE WHEN ({split_top(a)[1]}) = 0 THEN 0 ELSE ({split_top(a)[0]}) / ({split_top(a)[1]}) END)")
    sql = re.sub(r"::\s*FLOAT\b", "::DOUBLE", sql, flags=re.I)
    return sql


def inline_metrics(expr, metrics, depth=0):
    if depth > 6:
        return expr
    def rep(m):
        name = U(m.group(2))
        if name in metrics and U(m.group(1)) == metrics[name][0]:
            return "(" + inline_metrics(metrics[name][1], metrics, depth + 1) + ")"
        return m.group(0)
    return re.sub(r'"?(\w+)"?\s*\.\s*("\w+"|\b\w+\b)', rep, expr)


def sanitize(col):
    s = re.sub(r"[^A-Z0-9]+", "_", "".join(_REP.get(c, c) for c in str(col)).upper()).strip("_")
    return s or "COL"


def load_pbix(con, pbix, tables):
    from pbixray import PBIXRay
    px = PBIXRay(pbix)
    data, by_norm = {}, {}
    for t in px.tables:
        try:
            df = px.get_table(t)
        except Exception:
            continue
        data[t] = df; by_norm[norm(t)] = t
    for alias, phys in tables.items():
        key = norm(re.sub(r"_ENRICHED$", "", phys))
        src = by_norm.get(key) or by_norm.get(norm(alias))
        if not src:
            continue
        df = data[src].copy()
        cols = {}
        for c in df.columns:
            s = sanitize(c)
            cols.setdefault(s, c); cols.setdefault("COL_" + s, c)
        out = df[[cols[k] for k in cols]].copy(); out.columns = list(cols)
        for c in out.columns:                                   # pandas nullable -> plain for duckdb
            if str(out[c].dtype) in ("Int64", "Float64", "boolean"):
                out[c] = out[c].astype("float64" if str(out[c].dtype) != "boolean" else "object")
            elif str(out[c].dtype) == "string":
                out[c] = out[c].astype("object")
        con.register(f"_src_{alias}", out)
        con.execute(f'CREATE OR REPLACE VIEW "{alias}" AS SELECT * FROM "_src_{alias}"')
    return data


def from_clause(anchor, expr, rels):
    used = {U(a) for a in re.findall(r'"?(\w+)"?\s*\.\s*"?\w+"?', expr)}
    joins, have, changed = [], {anchor}, True
    while changed:
        changed = False
        for a, ac, b, bc in rels:
            if a in have and b not in have and b in used:
                joins.append(f'LEFT JOIN "{b}" ON "{a}"."{ac}" = "{b}"."{bc}"'); have.add(b); changed = True
    return f'"{anchor}" ' + " ".join(joins)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbix", required=True); ap.add_argument("--ddl", required=True)
    ap.add_argument("--model", default="REGIONAL", choices=sorted(EXPECTED)); ap.add_argument("--show-sql", action="store_true")
    ap.add_argument("--tol", type=float, default=1e-6)
    a = ap.parse_args()
    import duckdb
    tables, rels, metrics = parse_ddl(open(a.ddl, encoding="utf-8", errors="ignore").read())
    con = duckdb.connect()
    data = load_pbix(con, a.pbix, tables)
    expected = EXPECTED[a.model](data)
    by_norm = {norm(k): k for k in expected}
    fails = 0
    print(f"{'measure':34} {'status':8} {'view':>18} {'expected':>18}  note")
    for mname, (table, expr) in metrics.items():
        dax = by_norm.get(norm(mname))
        if not dax:
            continue
        exp = expected[dax]
        is_null = re.fullmatch(r"\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?", expr, re.I)
        if exp == "TEXT":
            ok = bool(is_null); fails += not ok
            print(f"{dax[:34]:34} {'PASS' if ok else 'FAIL':8} {'NULL' if is_null else 'value':>18} {'NULL (text)':>18}"); continue
        if is_null:
            fails += 1
            print(f"{dax[:34]:34} {'FAIL':8} {'NULL':>18} {exp:>18,.4f}  translatable but emitted as NULL"); continue
        if re.search(r"\bOVER\s*\(", expr, re.I):
            print(f"{dax[:34]:34} {'SKIP':8} {'window':>18} {exp:>18,.4f}  window metric - check in Snowflake"); continue
        sql = f"SELECT {to_duckdb(inline_metrics(expr, metrics))} FROM {from_clause(table, inline_metrics(expr, metrics), rels)}"
        if a.show_sql:
            print("   SQL:", sql[:400])
        try:
            got = con.execute(sql).fetchone()[0]
        except Exception as exc:
            fails += 1
            print(f"{dax[:34]:34} {'ERROR':8} {'':>18} {exp:>18,.4f}  {str(exc).splitlines()[0][:90]}"); continue
        ok = got is not None and abs(float(got) - exp) <= a.tol * max(1.0, abs(exp))
        fails += not ok
        note = "" if ok else f"differs (metric is on {table}; check filters, ELSE branches, anchoring)"
        print(f"{dax[:34]:34} {'PASS' if ok else 'FAIL':8} {('NULL' if got is None else f'{float(got):,.4f}'):>18} {exp:>18,.4f}  {note}")
    missing = [k for k in expected if norm(k) not in {norm(m) for m in metrics}]
    for k in missing:
        fails += 1; print(f"{k[:34]:34} {'MISSING':8}")
    print("\nALL PASS" if not fails else f"\n{fails} FAIL")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
