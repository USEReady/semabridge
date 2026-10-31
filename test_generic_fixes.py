#!/usr/bin/env python3
"""
test_generic_fixes.py - checks the generic fixes G1-G10 on ANY model (Sales & Returns is just the first test case).

MODE ddl   (offline, read-only)
  uv run python test_generic_fixes.py ddl --pbix "<model>.pbix" --ddl "<view>.sql" [--log "<deploy log>.txt"]
  repeat --pbix/--ddl pairs for several models.
  G1  metric anchored on the table whose columns it aggregates; no mix of a fact column and an enriched copy
      (<OTHER_TABLE>_<COL> on another table)
  G2  metrics used inside OVER(...) are on the same table as the window metric           (Snowflake 010263)
  G3  CALCULATE([X], ALL/REMOVEFILTERS ...) where [X] already contains a window -> must be NULL
  G4  what-if parameter without default: no metric NULLed as 'not reachable'; '* (0)' substitution flagged
  G5  no raw DAX left (DIVIDE( / [..]), no IFF(COALESCE(<number>, 0) = 0 ...) and no / NULLIF(<number>, 0)
  G6  window over a wrapped ratio keeps the outer ROUND / arithmetic
  G7  every NULL placeholder has a COMMENT reason
  G8  (with --log) metrics NULLed by deploy remediation ('pass N: fixing ...') -> FAIL in test mode
  G9  ALLSELECTED(column) measures should be windows (WARN while NULL)
  G10 CALCULATE(DISTINCTCOUNT(T[c]), ALL(T)) should be a precompute (WARN while NULL)
  +   keys: every REFERENCES target column is in that table's PRIMARY KEY / UNIQUE list
MODE sf    (live Snowflake, read-only; values vs ground truth computed from the base tables)
  uv run python test_generic_fixes.py sf --repo . --view <DB.SCHEMA.VIEW> --config sr_checks.json
  (sr_checks.json is written by 'init-sr' with the Sales & Returns checks; edit names for another model)
Exit code 1 on any FAIL.
"""
import argparse, json, os, re, sys, warnings
from collections import defaultdict
warnings.filterwarnings("ignore")

_REP = {"%": "PCT", "$": "DOL", "&": "AND", "#": "NUM"}
def norm(s): return re.sub(r"[^A-Z0-9]", "", "".join(_REP.get(c, c) for c in str(s)).upper())
U = lambda s: s.strip().strip('"').upper()
NULLRE = re.compile(r"^\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?$", re.I)


def split_args(s):
    out, depth, cur, q = [], 0, [], None
    for ch in s:
        if q:
            cur.append(ch); q = None if ch == q else q; continue
        if ch in "\"'":
            q = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip()); cur = []; continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def block(text, word):
    m = re.search(rf"\b{word}\s*\(", text, re.I)
    if not m:
        return ""
    d, i = 1, m.end()
    while i < len(text) and d:
        d += {"(": 1, ")": -1}.get(text[i], 0); i += 1
    return text[m.end():i - 1]


def calls(expr, fname):
    for m in re.finditer(rf"\b{fname}\s*\(", expr, re.I):
        d, i = 1, m.end()
        while i < len(expr) and d:
            d += {"(": 1, ")": -1}.get(expr[i], 0); i += 1
        yield expr[m.end():i - 1], expr[i:]


# ------------------------------------------------------------------ DDL model
class DDL:
    def __init__(self, text):
        self.keys, self.rels, self.dims, self.metrics, self.alias_phys = defaultdict(set), [], {}, {}, {}
        for t in split_args(block(text, "tables")):
            m = re.match(r'(?:("?[\w$]+"?)\s+as\s+)?([\w.\"$]+)(.*)$', t, re.I | re.S)
            if not m:
                continue
            phys = U(m.group(2).split(".")[-1]); al = U(m.group(1) or phys)
            self.alias_phys[al] = phys
            for km in re.finditer(r"(?:primary\s+key|unique)\s*\(([^)]*)\)", m.group(3), re.I):
                self.keys[al] |= {U(c) for c in km.group(1).split(",")}
        for r in split_args(block(text, "relationships")):
            m = re.search(r'("?\w+"?)\s*\(([^)]*)\)\s*references\s+("?\w+"?)\s*\(([^)]*)\)', r, re.I)
            if m:
                self.rels.append((U(m.group(1)), [U(c) for c in m.group(2).split(",")], U(m.group(3)), [U(c) for c in m.group(4).split(",")]))
        for line in split_args(block(text, "metrics")):
            m = re.match(r'("?\w+"?)\s*\.\s*("?\w+"?)\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
            if m:
                body = m.group(3)
                cm = re.search(r"\bCOMMENT\s*=\s*'((?:[^']|'')*)'", body, re.I)
                expr = re.sub(r"\s+COMMENT\s*=\s*'(?:[^']|'')*'", "", body, flags=re.I)
                expr = re.sub(r"\s+WITH\s+SYNONYMS\s*=?\s*\([^)]*\)", "", expr, flags=re.I)
                self.metrics[U(m.group(2))] = dict(table=U(m.group(1)), expr=" ".join(expr.split()), comment=cm.group(1) if cm else "")
        self.related = {a for a, _, b, _ in self.rels} | {b for _, _, b, _ in self.rels}

    def is_null(self, name):
        return bool(NULLRE.match(self.metrics[name]["expr"]))

    def col_refs(self, expr):
        """(alias, column) pairs that are column references (not metric references)"""
        out = []
        for a, c in re.findall(r'\b"?([A-Z_][A-Z0-9_$]*)"?\s*\.\s*"?([A-Z_][A-Z0-9_$]*)"?', re.sub(r"'(?:[^']|'')*'", "''", expr.upper())):
            if a in self.alias_phys and c not in self.metrics:
                out.append((a, c))
        return out


def windows(expr):
    s = re.sub(r"'(?:[^']|'')*'", "''", expr)
    for m in re.finditer(r"\b(SUM|AVG|COUNT|MIN|MAX)\s*\(", s, re.I):
        d, i = 1, m.end()
        while i < len(s) and d:
            d += {"(": 1, ")": -1}.get(s[i], 0); i += 1
        if re.match(r"\s*OVER\s*\(", s[i:], re.I):
            yield s[m.end():i - 1].strip()


# ------------------------------------------------------------------ DAX side
def load_measures(pbix):
    from pbixray import PBIXRay
    px = PBIXRay(pbix)
    md = px.dax_measures
    return {r["Name"]: str(r["Expression"] or "") for _, r in md.iterrows()}, {r["Name"]: r["TableName"] for _, r in md.iterrows()}


def inline(expr, measures, depth=0):
    if depth > 6:
        return expr
    return re.sub(r"(?<![\w'\]])\[([^\]]+)\]",
                  lambda m: "(" + inline(measures[m.group(1)], measures, depth + 1) + ")" if m.group(1) in measures else m.group(0), expr)


REMOVERS = r"(ALL|ALLEXCEPT|REMOVEFILTERS|ALLNOBLANKROW|ALLSELECTED)"


def has_window_dax(full):
    for body, _ in calls(full, "CALCULATE"):
        if any(re.match(rf"\s*{REMOVERS}\s*\(", a, re.I) for a in split_args(body)[1:]):
            return True
    return False


def outer_window_base(expr, measures):
    """CALCULATE([X], ALL..) at the top: return inlined X, else None"""
    for body, _ in calls(expr, "CALCULATE"):
        args = split_args(body)
        if len(args) > 1 and any(re.match(rf"\s*{REMOVERS}\s*\(", a, re.I) for a in args[1:]):
            return inline(args[0], measures)
    return None


def parameter_measures(measures, home, ddl):
    """measures that are SELECTEDVALUE over a table with no relationships and no default"""
    out = set()
    rel_tables = {norm(ddl.alias_phys.get(a, a)) for a in ddl.related} | {norm(a) for a in ddl.related}
    for n, e in measures.items():
        for body, _ in calls(e, "SELECTEDVALUE"):
            args = split_args(body)
            t = re.match(r"\s*'?([^'\[]+)'?\s*\[", args[0])
            if t and len(args) == 1 and norm(t.group(1)) not in rel_tables:
                out.add(n)
    return out


# ------------------------------------------------------------------ checks
def check_model(pbix, ddl_path, log_path=None):
    measures, home = load_measures(pbix)
    d = DDL(open(ddl_path, encoding="utf-8", errors="ignore").read())
    mn = {norm(k): k for k in d.metrics}
    fails = warns = 0
    print(f"\n=== {os.path.basename(pbix)}  vs  {os.path.basename(ddl_path)}")

    def report(level, rule, metric, msg):
        nonlocal fails, warns
        fails += level == "FAIL"; warns += level == "WARN"
        print(f"  {level:4} {rule:4} {metric[:34]:34} {msg}")

    # keys
    for a, ac, b, bc in d.rels:
        miss = [c for c in bc if c not in d.keys.get(b, set())]
        if miss:
            report("FAIL", "KEY", f"{a}->{b}", f"referenced column(s) {miss} not PRIMARY KEY/UNIQUE on {b}")

    params = parameter_measures(measures, home, d)
    for name, dax in measures.items():
        key = mn.get(norm(name))
        if not key:
            report("FAIL", "MISS", name, "measure missing from the view"); continue
        m = d.metrics[key]; expr = m["expr"]; null = d.is_null(key)
        full = inline(dax, measures)
        # G7
        if null and not m["comment"].strip():
            report("FAIL", "G7", key, "NULL without a reason COMMENT")
        if null:
            reason = m["comment"].lower()
            if re.search(r"ALLSELECTED\s*\(\s*'?[^)]*\[", dax, re.I):
                report("WARN", "G9", key, "ALLSELECTED(column) -> should be a window excluding that column")
            if re.search(r"CALCULATE\s*\(\s*DISTINCTCOUNT\s*\([^)]*\)\s*,\s*ALL\s*\(\s*'?[^)\[]+'?\s*\)\s*\)", full, re.I):
                report("WARN", "G10", key, "DISTINCTCOUNT over ALL(<table>) -> whole-table precompute")
            if any(p for p in params if f"[{p}]" in dax or f"'[{p}]" in dax or p in full) and ("reachable" in reason or "disconnected" in reason):
                report("FAIL", "G4", key, "uses a no-default parameter: should translate (parameter = NULL/BLANK), not 'not reachable'")
            # G3: expected NULL - fine
            continue
        # ---- real metrics
        # G3
        base = outer_window_base(dax, measures)
        if base and has_window_dax(base) and windows(expr):
            report("FAIL", "G3", key, "window over a measure that already contains a window -> must be NULL")
        # G5
        if re.search(r"\bDIVIDE\s*\(|\[[^\]]+\]", expr):
            report("FAIL", "G5", key, "raw DAX left in SQL")
        if re.search(r"IFF\s*\(\s*COALESCE\s*\(\s*\(?\s*-?\d+(\.\d+)?\s*\)?\s*,\s*0\s*\)\s*=\s*0", expr, re.I):
            report("WARN", "G5", key, "zero guard on a constant denominator (IFF(COALESCE(<number>,0)=0 ...))")
        if re.search(r"/\s*NULLIF\s*\(\s*-?\d+(\.\d+)?\s*,\s*0\s*\)", expr, re.I):
            report("WARN", "G5", key, "/ NULLIF(<number>, 0) on a constant")
        # G4
        if any(p in full for p in params) and re.search(r"\*\s*\(\s*0\s*\)", expr):
            report("WARN", "G4", key, "no-default parameter substituted by 0 in a product (DAX gives BLANK)")
        # G1
        refs = d.col_refs(expr)
        tables = {a for a, _ in refs}
        copies = [(a, c) for a, c in refs if any(c.startswith(o + "_") and o in tables and o != a for o in d.alias_phys)]
        if copies:
            report("FAIL", "G1", key, f"mixes fact columns with enriched copies {copies[:2]} - anchor on the fact")
        agg_tables = {a for a, _ in refs}
        if agg_tables and m["table"] not in agg_tables and not windows(expr):
            report("FAIL", "G1", key, f"anchored on {m['table']} but aggregates only {sorted(agg_tables)}")
        # G2 + G6
        for arg in windows(expr):
            r = re.fullmatch(r'(?:\(\s*)*"?(\w+)"?\s*\.\s*"?(\w+)"?(?:\s*\))*(?:\s*::\s*\w+)?', arg)
            if r and U(r.group(2)) in d.metrics:
                inner = d.metrics[U(r.group(2))]
                if inner["table"] != m["table"]:
                    report("FAIL", "G2", key, f"window uses {U(r.group(2))} on {inner['table']} but metric is on {m['table']} (010263)")
                inner_tables = {a for a, _ in d.col_refs(inner["expr"])}
                dim_only = inner_tables and all(t in {b for _, _, b, _ in d.rels} and t not in {a for a, _, _, _ in d.rels} for t in inner_tables)
                if dim_only and re.search(r"\bSUM\s*\(", inner["expr"], re.I):
                    report("FAIL", "G3", key, f"helper {U(r.group(2))} sums a dimension column ({inner['expr'][:50]})")
        if base and re.search(r"\bROUND\s*\(", base, re.I) and windows(expr) and not re.search(r"\bROUND\s*\(", expr, re.I):
            report("FAIL", "G6", key, "window over a ROUNDed ratio lost the outer ROUND")

    # G8
    if log_path and os.path.exists(log_path):
        log = open(log_path, encoding="utf-8", errors="ignore").read()
        for metric in sorted(set(re.findall(r"pass \d+: fixing [^']*'([^']+)'", log))):
            report("FAIL", "G8", metric, "NULLed by deploy remediation - fix the generator (see Snowflake error in the log)")
    print(f"  -> {fails} FAIL, {warns} WARN")
    return fails == 0


# ------------------------------------------------------------------ Snowflake values
SR_CHECKS = {
    "_doc": "Sales & Returns value checks. Each check compares a semantic-view query with ground truth SQL on base tables. Edit names for another model.",
    "checks": [
        {"name": "Net Sales by month",
         "view_query": "SELECT * FROM SEMANTIC_VIEW({view} DIMENSIONS CALENDAR.MONTH METRICS SALES.NET_SALES)",
         "truth_sql": "SELECT c.MONTH AS K, SUM(IFF(s.STATUS = 'Sold', s.AMOUNT, 0)) AS V FROM {db}.{schema}.SALES s JOIN {db}.{schema}.CALENDAR c ON s.COL_DATE = c.COL_DATE GROUP BY 1",
         "key": "MONTH", "value": "NET_SALES"},
        {"name": "Return Rate by month",
         "view_query": "SELECT * FROM SEMANTIC_VIEW({view} DIMENSIONS CALENDAR.MONTH METRICS SALES.RETURN_RATE)",
         "truth_sql": "SELECT c.MONTH AS K, ROUND(SUM(IFF(s.STATUS = 'Returned', s.AMOUNT, 0)) / NULLIF(SUM(s.AMOUNT), 0) * 100, 0) / 100 AS V FROM {db}.{schema}.SALES s JOIN {db}.{schema}.CALENDAR c ON s.COL_DATE = c.COL_DATE GROUP BY 1",
         "key": "MONTH", "value": "RETURN_RATE"},
        {"name": "Total Return Rate by month (same value every month)",
         "view_query": "SELECT * FROM SEMANTIC_VIEW({view} DIMENSIONS CALENDAR.MONTH METRICS SALES.TOTAL_RETURN_RATE)",
         "truth_sql": "SELECT c.MONTH AS K, (SELECT ROUND(SUM(IFF(STATUS = 'Returned', AMOUNT, 0)) / NULLIF(SUM(AMOUNT), 0) * 100, 0) / 100 FROM {db}.{schema}.SALES) AS V FROM {db}.{schema}.CALENDAR c WHERE c.COL_DATE IN (SELECT COL_DATE FROM {db}.{schema}.SALES) GROUP BY 1",
         "key": "MONTH", "value": "TOTAL_RETURN_RATE"},
        {"name": "WIF Profit with no slider selection = 0",
         "view_query": "SELECT * FROM SEMANTIC_VIEW({view} DIMENSIONS STORE.TYPE, STORE.STORE METRICS SALES.WIF_PROFIT)",
         "truth_const": 0, "value": "WIF_PROFIT"},
    ],
}


def mode_sf(a):
    sys.path.insert(0, os.path.join(os.path.abspath(a.repo), "src"))
    import importlib, snowflake.connector
    if a.connection_name:
        con = snowflake.connector.connect(connection_name=a.connection_name)
    else:
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
            sys.exit("no SemaBridge connection helpers - use --connection-name")
        con = snowflake.connector.connect(**found["get_snowflake_connect_kwargs"](found["get_settings"]().snowflake))
    cfg = json.load(open(a.config)) if a.config else SR_CHECKS
    db, schema, _ = a.view.split(".")
    ok = True
    for c in cfg["checks"]:
        cur = con.cursor()
        try:
            cur.execute(c["view_query"].format(view=a.view, db=db, schema=schema))
            cols = [x[0].upper() for x in cur.description]
            got = [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception as exc:
            ok = False; print(f"  FAIL {c['name']}: query error {str(exc).splitlines()[0][:140]}"); continue
        if "truth_const" in c:
            bad = [r for r in got if r.get(c["value"]) is None or abs(float(r[c["value"]]) - c["truth_const"]) > 1e-9]
        else:
            cur.execute(c["truth_sql"].format(db=db, schema=schema))
            truth = {r[0]: r[1] for r in cur.fetchall()}
            bad = [r for r in got if r.get(c["key"]) in truth and (r.get(c["value"]) is None or abs(float(r[c["value"]]) - float(truth[r[c["key"]]] or 0)) > 1e-6)]
        ok &= not bad
        print(f"  {'PASS' if not bad else 'FAIL'} {c['name']}" + ("" if not bad else f"   e.g. {bad[:2]}"))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["ddl", "sf", "init-sr"])
    ap.add_argument("--pbix", action="append", default=[]); ap.add_argument("--ddl", action="append", default=[])
    ap.add_argument("--log"); ap.add_argument("--repo", default="."); ap.add_argument("--view"); ap.add_argument("--config")
    ap.add_argument("--connection-name")
    a = ap.parse_args()
    if a.mode == "init-sr":
        json.dump(SR_CHECKS, open("sr_checks.json", "w"), indent=2); print("wrote sr_checks.json"); return
    if a.mode == "sf":
        return mode_sf(a)
    if len(a.pbix) != len(a.ddl) or not a.pbix:
        sys.exit("give one --ddl per --pbix")
    ok = all([check_model(p, d, a.log) for p, d in zip(a.pbix, a.ddl)])
    print("\nALL MODELS PASS" if ok else "\nSOME CHECKS FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
