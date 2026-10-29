#!/usr/bin/env python3
"""
validate_complex_ddl.py - END-TO-END check of complex measures in a generated semantic-view DDL.
Generic: works for any model; no model/table/column/measure names in the checks. Offline (DuckDB), no Snowflake.

For every measure of the model (from a DAX query file "DEFINE MEASURE ... EVALUATE ..." or from the .pbix):
  1. PRESENCE   REAL / NULL (+ reason?) / MISSING (silent drop)
  2. STRUCTURE  what Snowflake would reject or compute wrongly:
                PHANTOM_MEASURE_COLUMN  a measure reference emitted as a column, e.g. OWNERS.REVENUE_WON
                UNKNOWN_COLUMN          alias.column not present in that table (or its enriched view) -> 000904
                NOT_AGGREGATED          column outside any aggregate -> "metric must be an aggregate"
                NESTED_AGGREGATE        aggregate inside aggregate
                WINDOW_IN_METRIC        OVER (...) inside the METRICS clause
                DAX_LEFTOVER            CALCULATE/SUMX/FILTER/SELECTEDVALUE/... left in the SQL
                WRONG_ANCHOR            metric declared on a table it does not use (data comes from another table)
                UNREACHABLE_TABLE       referenced table has no relationship path from the metric's table (010211)
                ANY_VALUE / COUNT_ELSE_0 / UNSAFE_DIVISION
                EXPRESSION_BLOWUP       very long SQL / same aggregate repeated many times (VAR inlining)
  3. ENRICHED   which columns come only from an *_ENRICHED view; with --enriched-sql the views are BUILT in
                DuckDB from your CREATE VIEW statements (transpiled), so window columns (… OVER ()) and copied
                dimension columns are really computed and verified
  4. VALUE      the metric SQL is run on the .pbix data and compared with --expected (Power BI results)

usage:
  uv run python validate_complex_ddl.py --pbix "<model>.pbix" --ddl "<view ddl>.sql"
         [--dax-query measures.dax] [--enriched-sql enriched_views.sql] [--expected expected.json]
         [--measure "Rev Goal" --measure "Forecast Adjustment Value"] [--out complex_ddl_report.csv]
--expected: {"<measure name or METRIC_NAME>": value, ...}  (values of an EVALUATE SUMMARIZECOLUMNS in Power BI)
--enriched-sql: file with the CREATE [OR REPLACE] VIEW ..._ENRICHED AS SELECT ... statements SemaBridge ran
                (database/schema prefixes are stripped). Without it, enriched views are SIMULATED as
                base table + <DIM>_<COLUMN> copies over the DDL relationships (window columns then show n/a).
needs: pip install duckdb sqlglot pandas pbixray
"""
import argparse, csv, json, math, os, re, sys, warnings
from collections import OrderedDict, defaultdict, deque
warnings.filterwarnings("ignore")
import logging
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# ------------------------------------------------------------------ shared helpers (also used by script 2)
_RES = {"DATE", "VALUE", "YEAR", "MONTH", "DAY", "ALL", "ORDER", "GROUP", "TABLE", "SELECT", "FROM", "WHERE", "USER",
        "TIME", "TIMESTAMP", "NUMBER", "COLUMN", "ROW", "ROWS", "START", "CURRENT", "SEX"}
_REP = {"%": "PCT", "$": "DOL", "@": "AT", "#": "NUM", "&": "AND", "+": "PLUS", "=": "EQ", "<": "LT", ">": "GT"}
DAX_ONLY = re.compile(r"\b(CALCULATE|CALCULATETABLE|SUMX|AVERAGEX|MINX|MAXX|COUNTAX|COUNTROWS|FILTER|SELECTEDVALUE|"
                      r"KEEPFILTERS|EARLIER|ALLEXCEPT|ALLSELECTED|VALUES|RELATED|RANKX|ISINSCOPE|TREATAS|USERELATIONSHIP|"
                      r"SAMEPERIODLASTYEAR|DATEADD|TOTALYTD|DATESYTD|LASTNONBLANK|BLANK)\s*\(", re.I)
UNSUPPORTED_DAX = re.compile(r"\b(ALL|ALLEXCEPT|ALLSELECTED|RANKX|ISINSCOPE|TREATAS|LASTNONBLANK|DATEADD|PARALLELPERIOD|"
                             r"DATESINPERIOD|EARLIER|PATH\w*)\s*\(", re.I)


def sanitize(name, reserved=True):
    try:
        from semabridge.utils.identifiers import IdentifierSanitizer
        return IdentifierSanitizer(force_uppercase=True, suppress_reserved=reserved).sanitize_column(name)
    except Exception:
        s = "".join(_REP.get(c, c) for c in str(name))
        s = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", s)).strip("_").upper()
        return "COL_" + s if reserved and s in _RES else s


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def strip_strings(sql):
    return re.sub(r"'(?:[^']|'')*'", "''", sql or "")


def parse_dax_query(text):
    """DEFINE MEASURE 'T'[Name] = expr ... EVALUATE  ->  OrderedDict name -> (table, expression)."""
    body = re.split(r"\bEVALUATE\b", text, flags=re.I)[0]
    parts = re.split(r"\bMEASURE\s+(?:'([^']+)'|([\w ]+?))\s*\[([^\]]+)\]\s*=", body, flags=re.I)
    out = OrderedDict()
    for i in range(1, len(parts) - 3, 4):
        table = (parts[i] or parts[i + 1] or "").strip()
        out[parts[i + 2].strip()] = (table, parts[i + 3].strip())
    return out


def measures_from_pbix(px):
    m = px.dax_measures
    return OrderedDict((r["Name"], (r["TableName"], str(r.get("Expression") or ""))) for _, r in m.iterrows())


def is_null_placeholder(expr):
    return bool(re.fullmatch(r"\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?", expr or "", re.I))


def unsafe_divisions(sql):
    s, bad = strip_strings(sql), []
    for m in re.finditer(r"/", s):
        rest = s[m.end():].lstrip()
        if re.match(r"(\(\s*)*(NULLIF\s*\(|[-+]?\d)", rest, re.I):
            continue
        depth, i, guarded = 0, m.start() - 1, False
        while i >= 0 and not guarded:
            ch = s[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                if depth == 0:
                    head = re.search(r"(\w+)\s*$", s[:i])
                    if head and head.group(1).upper() in ("IFF", "IF") and re.search(r"=\s*0\b", s[i:m.start()]):
                        guarded = True
                else:
                    depth -= 1
            i -= 1
        if not guarded:
            bad.append(s[max(0, m.start() - 25):m.end() + 25].strip())
    return bad


def structural_issues(sql, own_alias, table_columns, measure_names, reachable=None):
    """Generic checks of ONE metric expression.
    table_columns: {ALIAS: set(COLUMNS)} of everything the view can see; measure_names: sanitized measure names."""
    import sqlglot
    from sqlglot import exp
    issues = []
    if not sql or is_null_placeholder(sql):
        return issues
    if DAX_ONLY.search(strip_strings(sql)):
        issues.append(("DAX_LEFTOVER", DAX_ONLY.search(strip_strings(sql)).group(1).upper() + "( left in SQL"))
    if re.search(r"\bANY_VALUE\s*\(", sql, re.I):
        issues.append(("ANY_VALUE", "SELECTEDVALUE -> IFF(COUNT(DISTINCT c)+MAX(IFF(c IS NULL,1,0))=1, MAX(c), alt)"))
    if re.search(r"\bCOUNT\s*\(\s*(?:DISTINCT\s+)?CASE\b(?:(?!\bEND\b).)*\bELSE\s+0\b", sql, re.I | re.S):
        issues.append(("COUNT_ELSE_0", "COUNT(CASE ... ELSE 0) counts every row"))
    if unsafe_divisions(sql):
        issues.append(("UNSAFE_DIVISION", "use NULLIF(denominator, 0): ..." + unsafe_divisions(sql)[0]))
    if len(sql) > 3000:
        issues.append(("EXPRESSION_BLOWUP", f"{len(sql)} characters"))
    try:
        tree = sqlglot.parse_one(sql, read="snowflake")
    except Exception as exc:
        issues.append(("UNPARSEABLE", str(exc).splitlines()[0][:100]))
        return issues
    aggs = list(tree.find_all(exp.AggFunc))
    counts = defaultdict(int)
    for a in aggs:
        counts[a.sql()] += 1
    rep = max(counts.values()) if counts else 0
    if rep > 6 and not any(i[0] == "EXPRESSION_BLOWUP" for i in issues):
        issues.append(("EXPRESSION_BLOWUP", f"same aggregate repeated {rep}x (VAR/measure inlining)"))
    for a in aggs:
        if a.find_ancestor(exp.AggFunc):
            issues.append(("NESTED_AGGREGATE", a.sql()[:80])); break
    if list(tree.find_all(exp.Window)):
        issues.append(("WINDOW_IN_METRIC", "OVER (...) is not allowed in METRICS - precompute it in a view"))
    used_tables = set()
    for col in tree.find_all(exp.Column):
        t, c = (col.table or "").upper(), (col.name or "").upper()
        if not t:
            continue
        used_tables.add(t)
        if not col.find_ancestor(exp.AggFunc) and not col.find_ancestor(exp.Window):
            issues.append(("NOT_AGGREGATED", f"{t}.{c} outside an aggregate"))
        cols = table_columns.get(t)
        if cols is None:
            issues.append(("UNKNOWN_TABLE", t))
        elif c not in cols:
            kind = "PHANTOM_MEASURE_COLUMN" if c in measure_names else "UNKNOWN_COLUMN"
            issues.append((kind, f"{t}.{c} does not exist" + (" (it is a measure, not a column)" if kind.startswith("PHANTOM") else "")))
    if own_alias and used_tables and own_alias.upper() not in used_tables:
        issues.append(("WRONG_ANCHOR", f"declared on {own_alias} but uses only {', '.join(sorted(used_tables))}"))
    if reachable is not None and own_alias:
        for t in sorted(used_tables - reachable.get(own_alias.upper(), {own_alias.upper()})):
            if t in table_columns:
                issues.append(("UNREACHABLE_TABLE", f"{t} not reachable from {own_alias}"))
    seen, out = set(), []
    for i in issues:
        if i not in seen:
            seen.add(i); out.append(i)
    return out


def expected_status(dax):
    """Generic expectation from the DAX alone: REAL, or NULL-with-reason acceptable."""
    if not (dax or "").strip():
        return "NULL_OK (empty)"
    if UNSUPPORTED_DAX.search(dax):
        return "NULL_OK (" + UNSUPPORTED_DAX.search(dax).group(1).upper() + ")"
    if re.search(r'^\s*"[^"]*"\s*$|\bFORMAT\s*\(|\bCONCATENATE\s*\(', dax, re.I):
        return "NULL_OK (text)"
    return "REAL"


def mkey(name):
    """Key that keeps '%', '$' etc. distinct ('Forecast %' != 'Forecast'): normalized sanitized name."""
    return norm(sanitize(name, False))


def load_expected(path):
    if not path:
        return {}
    return {mkey(k): v for k, v in json.load(open(path)).items()}


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-9)


# ------------------------------------------------------------------ DDL parsing
def _block(text, word):
    m = re.search(rf"\b{word}\s*\(", text, re.I)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0); i += 1
    return text[m.end():i - 1]


def _split(s):
    out, depth, cur, q = [], 0, [], None
    for ch in s:
        if q:
            cur.append(ch); q = None if ch == q else q; continue
        if ch in "'\"":
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip()); cur = []; continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def parse_ddl(text):
    tables = OrderedDict()
    for t in _split(_block(text, "tables")):
        m = re.match(r"(?:(\"?\w+\"?)\s+as\s+)?([\w.\"$]+)", t, re.I)
        if m:
            phys = m.group(2).split(".")[-1].strip('"').upper()
            tables[(m.group(1) or phys).strip('"').upper()] = phys
    rels = []
    for r in _split(_block(text, "relationships")):
        m = re.search(r"as\s+(\w+)\s*\(([^)]*)\)\s*references\s+(\w+)\s*\(([^)]*)\)", r, re.I)
        if m:
            rels.append((m.group(1).upper(), [c.strip().strip('"').upper() for c in m.group(2).split(",")],
                         m.group(3).upper(), [c.strip().strip('"').upper() for c in m.group(4).split(",")]))
    metrics = OrderedDict()
    for line in _split(_block(text, "metrics")):
        m = re.match(r"(\"?\w+\"?)\.\"?(\w+)\"?\s+(non\s+additive\s+by\s*\(([^)]*)\)\s+)?as\s+(.*)$", line, re.I | re.S)
        if m:
            body = m.group(5).strip()
            reason = " ".join(re.findall(r"/\*(.*?)\*/", body, re.S) + re.findall(r"COMMENT\s*=\s*'((?:[^']|'')*)'", body, re.I))
            expr = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
            expr = re.sub(r"\s+COMMENT\s*=\s*'(?:[^']|'')*'\s*$", "", expr, flags=re.I | re.S)
            metrics[m.group(2).upper()] = {"table": m.group(1).strip('"').upper(), "expr": " ".join(expr.split()),
                                           "non_additive": (m.group(4) or "").strip(), "reason": reason.strip()}
    return tables, rels, metrics


def reach_map(rels, tables):
    adj = defaultdict(set)
    for ft, _, tt, _ in rels:
        adj[ft].add(tt)
    out = {}
    for t in tables:
        seen, dq = {t}, deque([t])
        while dq:
            for n in adj[dq.popleft()]:
                if n not in seen:
                    seen.add(n); dq.append(n)
        out[t] = seen
    return out


# ------------------------------------------------------------------ DuckDB data
def load_pbix_tables(con, px, wanted_physical):
    """Load .pbix tables whose sanitized name matches a wanted physical name (or its _ENRICHED base)."""
    want = {}
    for p in wanted_physical:
        want.setdefault(norm(p), p)
        want.setdefault(norm(re.sub(r"_ENRICHED$", "", p)), re.sub(r"_ENRICHED$", "", p))
    loaded = {}
    for t in px.tables:
        target = want.get(norm(t)) or want.get(norm(sanitize(t, False)))
        if not target or target in loaded:
            continue
        try:
            df = px.get_table(t).copy()
        except Exception as exc:
            print(f"  (could not read {t}: {str(exc)[:80]})"); continue
        df.columns = [sanitize(c) for c in df.columns]
        con.register("_t", df); con.execute(f'CREATE TABLE src."{target}" AS SELECT * FROM _t'); con.unregister("_t")
        loaded[target] = len(df)
    return loaded


def build_enriched(con, tables, rels, enriched_sql, loaded):
    """Create *_ENRICHED physical views: from the user's CREATE VIEW SQL if given, else simulated."""
    import sqlglot
    built, notes = set(), {}
    if enriched_sql:
        text = open(enriched_sql, encoding="utf-8").read()
        for stmt in [s for s in re.split(r";\s*\n", text) if re.search(r"\bCREATE\b.*\bVIEW\b", s, re.I | re.S)]:
            m = re.search(r"VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"$]+)\s+(?:\([^)]*\)\s+)?AS\s+(.*)$", stmt, re.I | re.S)
            if not m:
                continue
            name = m.group(1).split(".")[-1].strip('"').upper()
            body = re.sub(r'(?:"?\w+"?\.){1,2}("?\w+"?)(?=\s|$|\))', lambda x: x.group(1), m.group(2))   # drop db/schema prefixes
            for b in loaded:   # base tables live in schema src (aliases may reuse their names)
                body = re.sub(rf'\b(FROM|JOIN)\s+"?{re.escape(b)}"?(?=\s|$|\))', rf'\1 src."{b}"', body, flags=re.I)
            try:
                q = sqlglot.transpile(body, read="snowflake", write="duckdb")[0]
                con.execute(f'CREATE OR REPLACE VIEW "{name}" AS {q}')
                built.add(name); notes[name] = "built from --enriched-sql"
            except Exception as exc:
                notes[name] = f"could not build from --enriched-sql: {str(exc).splitlines()[0][:100]}"
    alias_of = {v: k for k, v in tables.items()}
    for alias, phys in tables.items():
        if phys in loaded or phys in built or not phys.endswith("_ENRICHED"):
            continue
        base = re.sub(r"_ENRICHED$", "", phys)
        if base not in loaded:
            continue
        cols = {r[0].upper() for r in con.execute(f'DESCRIBE src."{base}"').fetchall()}
        extra, joins = [], []
        for i, (ft, fc, tt, tc) in enumerate(r for r in rels if r[0] == alias):
            tphys = tables.get(tt)
            if tphys not in loaded:
                continue
            joins.append(f'LEFT JOIN src."{tphys}" d{i} ON ' + " AND ".join(f'f."{a}" = d{i}."{b}"' for a, b in zip(fc, tc)))
            for (c,) in con.execute(f'SELECT column_name FROM (DESCRIBE src."{tphys}")').fetchall():
                new = f"{sanitize(tt, False)}_{c.upper()}"
                if new not in cols:
                    extra.append(f'd{i}."{c}" AS "{new}"'); cols.add(new)
        con.execute(f'CREATE OR REPLACE VIEW "{phys}" AS SELECT f.*{"".join(", " + e for e in extra)} FROM src."{base}" f {" ".join(joins)}')
        built.add(phys); notes[phys] = "SIMULATED (base + related-dimension copies; no window columns)"
    for alias, phys in tables.items():
        if phys in built:
            if alias != phys:
                con.execute(f'CREATE OR REPLACE VIEW "{alias}" AS SELECT * FROM "{phys}"')
        elif phys in loaded:
            con.execute(f'CREATE OR REPLACE VIEW "{alias}" AS SELECT * FROM src."{phys}"')
    return built, notes


def table_columns(con, tables):
    out = {}
    for alias in tables:
        try:
            out[alias] = {r[0].upper() for r in con.execute(f'DESCRIBE "{alias}"').fetchall()}
        except Exception:
            pass
    return out


def evaluate(con, alias, expr, rels, aliases, non_additive=""):
    import sqlglot
    used = {u.upper() for u in re.findall(r"\b([A-Za-z_]\w*)\s*\.", strip_strings(expr))} & set(aliases)
    if non_additive:
        used.add(non_additive.split()[0].split(".")[0].strip('"').upper())
    frm, joined = f'"{alias}"', {alias}
    for u in sorted(used - {alias}):
        prev, dq = {alias: None}, deque([alias])
        while dq:
            cur = dq.popleft()
            for r in rels:
                if r[0] == cur and r[2] not in prev:
                    prev[r[2]] = (cur, r); dq.append(r[2])
        if u not in prev:
            raise ValueError(f"no relationship path {alias} -> {u}")
        path, n = [], u
        while prev[n]:
            path.append(prev[n][1]); n = prev[n][0]
        for ft, fc, tt, tc in reversed(path):
            if tt not in joined:
                frm += f' LEFT JOIN "{tt}" ON ' + " AND ".join(f'"{ft}"."{a}" = "{tt}"."{b}"' for a, b in zip(fc, tc)); joined.add(tt)
    where = ""
    if non_additive:
        nt, nc = [p.strip().strip('"').upper() for p in non_additive.split()[0].split(".")]
        where = f' WHERE "{nt}"."{nc}" = (SELECT MAX("{nt}"."{nc}") FROM {frm})'
    q = sqlglot.transpile(f"SELECT {expr} FROM {frm}{where}", read="snowflake", write="duckdb")[0]
    v = con.execute(q).fetchone()[0]
    return None if v is None else float(v)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbix", required=True); ap.add_argument("--ddl", required=True)
    ap.add_argument("--dax-query"); ap.add_argument("--enriched-sql"); ap.add_argument("--expected")
    ap.add_argument("--measure", action="append", help="only these measures (repeatable)")
    ap.add_argument("--out", default="complex_ddl_report.csv")
    args = ap.parse_args()
    import duckdb
    from pbixray import PBIXRay

    px = PBIXRay(args.pbix)
    measures = parse_dax_query(open(args.dax_query, encoding="utf-8").read()) if args.dax_query else measures_from_pbix(px)
    if args.measure:
        keep = {mkey(m) for m in args.measure}
        measures = OrderedDict((k, v) for k, v in measures.items() if mkey(k) in keep)
    tables, rels, metrics = parse_ddl(open(args.ddl, encoding="utf-8").read())
    reach = reach_map(rels, tables)
    expected = load_expected(args.expected)

    con = duckdb.connect()
    con.execute("CREATE SCHEMA src")
    loaded = load_pbix_tables(con, px, set(tables.values()))
    built, notes = build_enriched(con, tables, rels, args.enriched_sql, loaded)
    tcols = table_columns(con, tables)
    base_cols = {}
    for alias, phys in tables.items():
        base = re.sub(r"_ENRICHED$", "", phys)
        if base in loaded:
            base_cols[alias] = {r[0].upper() for r in con.execute(f'DESCRIBE src."{base}"').fetchall()}
    measure_cols = {sanitize(n, False) for n in measures} | {sanitize(n) for n in measures}
    print(f"tables: {len(tables)}  loaded from .pbix: {len(loaded)}  enriched views: "
          + (", ".join(f"{k} [{v}]" for k, v in notes.items()) or "none"))
    missing_tables = [a for a, p in tables.items() if a not in tcols]
    if missing_tables:
        print(f"  not available locally (their metrics are not value-checked): {', '.join(missing_tables)}")

    ddl_by = {mkey(n): n for n in metrics}
    rows = []
    for name, (home, dax) in measures.items():
        dn = ddl_by.get(mkey(name))
        exp_status = expected_status(dax)
        r = OrderedDict(measure=name, dax_table=home, expected_status=exp_status, status="", ddl_table="", reason="",
                        issues="", enriched_columns="", value="", expected="", value_check="", ddl_sql="")
        if dn is None:
            r.update(status="MISSING", issues="SILENT_DROP")
        else:
            md = metrics[dn]
            r.update(ddl_table=md["table"], ddl_sql=md["expr"][:500], reason=md["reason"][:200])
            if is_null_placeholder(md["expr"]):
                r["status"] = "NULL"
                iss = []
                if exp_status == "REAL":
                    iss.append("NULL_BUT_TRANSLATABLE")
                if not md["reason"]:
                    iss.append("NO_REASON_IN_DDL")
                if re.fullmatch(r"\(?\s*NULL\s*\)?", md["expr"], re.I):
                    iss.append("BARE_NULL")
                r["issues"] = "; ".join(iss)
            else:
                r["status"] = "REAL"
                iss = structural_issues(md["expr"], md["table"], tcols, measure_cols, reach)
                r["issues"] = "; ".join(f"{k}: {v}" for k, v in iss)
                used = re.findall(r"\b([A-Za-z_]\w*)\s*\.\s*\"?(\w+)\"?", strip_strings(md["expr"]))
                enr = sorted({f"{t.upper()}.{c.upper()}" for t, c in used
                              if t.upper() in base_cols and c.upper() not in base_cols[t.upper()] and c.upper() in tcols.get(t.upper(), set())})
                r["enriched_columns"] = ", ".join(enr)
                if exp_status.startswith("NULL_OK (ALL") and enr:
                    r["issues"] = "; ".join(x for x in [r["issues"], "WINDOW_APPROXIMATION: whole-table total from the enriched view - ignores other query filters"] if x)
                if md["table"] in tcols and not any(k in ("UNKNOWN_COLUMN", "PHANTOM_MEASURE_COLUMN", "UNKNOWN_TABLE", "NOT_AGGREGATED", "DAX_LEFTOVER", "UNPARSEABLE") for k, _ in iss):
                    try:
                        r["value"] = evaluate(con, md["table"], md["expr"], rels, set(tcols), md["non_additive"])
                    except Exception as exc:
                        r["value"] = "error: " + str(exc).splitlines()[0][:120]
                key = mkey(name) if mkey(name) in expected else mkey(dn)
                if key in expected and (isinstance(r["value"], float) or r["value"] is None):
                    r["expected"] = expected[key]
                    r["value_check"] = "OK" if close(r["value"], expected[key]) else "WRONG"
        rows.append(r)

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    bad = [r for r in rows if r["status"] == "MISSING" or r["value_check"] == "WRONG"
           or any(k in r["issues"] for k in ("PHANTOM", "UNKNOWN_COLUMN", "NOT_AGGREGATED", "NESTED", "WINDOW_IN",
                                             "DAX_LEFTOVER", "WRONG_ANCHOR", "UNREACHABLE", "ANY_VALUE", "UNSAFE", "NULL_BUT"))]
    cnt = lambda s: sum(r["status"] == s for r in rows)
    print(f"\nmeasures: {len(rows)}  REAL: {cnt('REAL')}  NULL: {cnt('NULL')}  MISSING: {cnt('MISSING')}  "
          f"value OK: {sum(r['value_check'] == 'OK' for r in rows)}  WRONG: {sum(r['value_check'] == 'WRONG' for r in rows)}  "
          f"with blocking issues: {len(bad)}")
    print(f"\n{'MEASURE':32} {'EXPECT':22} {'DDL':7} {'TABLE':16} {'VALUE':>16} {'CHECK':6} ISSUES")
    for r in rows:
        v = r["value"]; vs = f"{v:,.4f}" if isinstance(v, float) else ("NULL" if v is None and r["value_check"] else str(v)[:16])
        print(f"{r['measure'][:32]:32} {r['expected_status'][:22]:22} {r['status']:7} {r['ddl_table'][:16]:16} {vs:>16} "
              f"{r['value_check']:6} {r['issues'][:120]}")
        if r["enriched_columns"]:
            print(f"{'':32}   uses enriched-view columns: {r['enriched_columns'][:140]}")
        if r["value_check"] == "WRONG":
            print(f"{'':32}   expected {r['expected']}")
    print(f"\nFull detail: {os.path.abspath(args.out)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
