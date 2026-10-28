#!/usr/bin/env python3
"""
trace_measures.py - for one model: where did each Power BI measure get dropped, and is the SQL that
reached the semantic view correct? Generic (no model names in code). Runs fully offline (no Snowflake).

Per measure it reports:
  1. STAGE   - the last stage the measure reached:
               PBIX -> OSI -> SML (with sql / sync flag / failure reason) -> DDL (REAL | NULL | MISSING)
               (OSI/SML columns need the SemaBridge repo; without it only PBIX vs DDL is compared)
  2. CHECKS  - generic pattern checks on the emitted SQL (ANY_VALUE for SELECTEDVALUE, ELSE 0 inside
               COUNT, bare NULL, reference to a table with no relationship path, metric-name references
               inside an aggregate)
  3. VALUE   - the emitted SQL is run on the .pbix data itself (DuckDB) and compared with expected values
               (optional --expected json: {"METRIC_NAME": value} from Power BI / a DAX query)
  4. FIX     - for SELECTEDVALUE measures: the correct SQL, also evaluated on the data

usage:
  uv run python phase_a\\measure_trace\\trace_measures.py --pbix "<model>.pbix" --ddl "<view ddl>.sql"
         [--expected expected.json] [--repo .] [--out measure_trace.csv]
--ddl = the CREATE SEMANTIC VIEW text (GET_DDL from Snowflake, or output/debug/debug_generated_sql_<model>.sql)
needs: pip install pbixray duckdb sqlglot pandas
"""
import argparse, csv, json, math, os, re, sys, warnings
from collections import defaultdict, deque
warnings.filterwarnings("ignore")

# ------------------------------------------------------------------ naming (same rules as SemaBridge)
_RESERVED = {"DATE", "VALUE", "YEAR", "MONTH", "DAY", "SEX", "ALL", "ORDER", "GROUP", "TABLE", "SELECT", "FROM",
             "WHERE", "USER", "TIME", "TIMESTAMP", "NUMBER", "COLUMN", "ROW", "ROWS", "START", "CURRENT"}
_REPL = {"%": "PCT", "$": "DOL", "@": "AT", "#": "NUM", "&": "AND", "+": "PLUS", "=": "EQ", "<": "LT", ">": "GT"}


def sanitize(name, reserved=True):
    try:
        from semabridge.utils.identifiers import IdentifierSanitizer
        return IdentifierSanitizer(force_uppercase=True, suppress_reserved=reserved).sanitize_column(name)
    except Exception:
        s = "".join(_REPL.get(c, c) for c in str(name))
        s = re.sub(r"[^A-Za-z0-9_]", "_", s)
        s = re.sub(r"_+", "_", s).strip("_").upper()
        if reserved and s in _RESERVED:
            s = "COL_" + s
        return s


def key(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# ------------------------------------------------------------------ DDL parsing
def parse_ddl(text):
    def block(word):
        m = re.search(rf"\b{word}\s*\(", text, re.I)
        if not m:
            return ""
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        return text[m.end():i - 1]

    def split_top(s):
        out, depth, cur, q = [], 0, [], None
        for ch in s:
            if q:
                cur.append(ch)
                if ch == q:
                    q = None
                continue
            if ch in "'\"":
                q = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append("".join(cur).strip()); cur = []
                continue
            cur.append(ch)
        if "".join(cur).strip():
            out.append("".join(cur).strip())
        return out

    tables = {}
    for t in split_top(block("tables")):
        m = re.match(r"(?:(\w+)\s+as\s+)?([\w.\"]+)", t, re.I)
        phys = m.group(2).split(".")[-1].strip('"')
        tables[(m.group(1) or phys).upper()] = phys.upper()
    rels = []
    for r in split_top(block("relationships")):
        m = re.search(r"as\s+(\w+)\s*\(([^)]*)\)\s*references\s+(\w+)\s*\(([^)]*)\)", r, re.I)
        if m:
            rels.append((m.group(1).upper(), [c.strip().strip('"').upper() for c in m.group(2).split(",")],
                         m.group(3).upper(), [c.strip().strip('"').upper() for c in m.group(4).split(",")]))
    metrics = {}
    for line in split_top(block("metrics")):
        m = re.match(r"(\w+)\.\"?(\w+)\"?\s+(?:NON\s+ADDITIVE\s+BY\s*\([^)]*\)\s+)?as\s+(.*)$", line, re.I | re.S)
        if m:
            expr = re.sub(r"\s+COMMENT\s*=\s*'.*'$", "", m.group(3).strip(), flags=re.I | re.S)
            metrics[m.group(2).upper()] = (m.group(1).upper(), " ".join(expr.split()))
    return tables, rels, metrics


def is_null_placeholder(expr):
    return bool(re.fullmatch(r"\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?", expr, re.I))


# ------------------------------------------------------------------ relationship graph
def reachable(rels, start):
    adj = defaultdict(set)
    for ft, _, tt, _ in rels:
        adj[ft].add(tt)          # many-to-one direction (fact -> dimension)
    seen, dq = {start}, deque([start])
    while dq:
        for n in adj[dq.popleft()]:
            if n not in seen:
                seen.add(n); dq.append(n)
    return seen


def join_path(rels, start, target):
    prev, dq = {start: None}, deque([start])
    while dq:
        cur = dq.popleft()
        if cur == target:
            break
        for r in rels:
            if r[0] == cur and r[2] not in prev:
                prev[r[2]] = (cur, r); dq.append(r[2])
    if target not in prev:
        return None
    path, n = [], target
    while prev[n]:
        path.append(prev[n][1]); n = prev[n][0]
    return list(reversed(path))


# ------------------------------------------------------------------ generic SQL checks
def check_sql(alias, expr, metric_names, aliases, rels):
    issues = []
    if is_null_placeholder(expr):
        if re.fullmatch(r"\(?\s*NULL\s*\)?", expr, re.I):
            issues.append("bare NULL (use CAST(NULL AS DOUBLE) + reason)")
        return issues
    if re.search(r"\bANY_VALUE\s*\(", expr, re.I):
        issues.append("ANY_VALUE returns an arbitrary row value; for SELECTEDVALUE use "
                      "IFF(COUNT(DISTINCT c) + MAX(IFF(c IS NULL, 1, 0)) = 1, MAX(c), <alternate>)")
    if re.search(r"\bCOUNT\s*\(\s*CASE\b(?:(?!\bEND\b).)*\bELSE\s+0\b", expr, re.I):
        issues.append("COUNT(CASE ... ELSE 0) counts every row; drop the ELSE")
    used = {a.upper() for a in re.findall(r"\b([A-Za-z_]\w*)\s*\.\s*\"?[A-Za-z_]", expr)} & set(aliases)
    reach = reachable(rels, alias)
    for u in sorted(used - reach):
        issues.append(f"references {u}, which has no relationship path from {alias} (Snowflake 010211)")
    for ref in re.findall(r"\b([A-Za-z_]\w*)\s*\.\s*\"?(\w+)\"?", expr):
        if ref[1].upper() in metric_names and ref[1].upper() != "COL_VALUE":
            issues.append(f"references metric {ref[0]}.{ref[1]} by name inside an expression")
            break
    return issues


# ------------------------------------------------------------------ data (DuckDB from the .pbix)
def load_data(pbix, ddl_tables):
    import duckdb
    from pbixray import PBIXRay
    model = PBIXRay(pbix)
    con = duckdb.connect()
    loaded = {}
    by_key = {key(alias): alias for alias in ddl_tables}
    by_key.update({key(phys): alias for alias, phys in ddl_tables.items()})
    for tname in model.tables:
        alias = by_key.get(key(tname)) or by_key.get(key(sanitize(tname, reserved=False)))
        if not alias or alias in loaded:
            continue
        try:
            df = model.get_table(tname)
        except Exception:
            continue
        df = df.copy()
        df.columns = [sanitize(c) for c in df.columns]
        con.register("_t", df)
        con.execute(f'CREATE TABLE "{alias}" AS SELECT * FROM _t')
        con.unregister("_t")
        loaded[alias] = len(df)
    return con, loaded, model


def evaluate(con, alias, expr, rels, aliases):
    import sqlglot
    used = {a.upper() for a in re.findall(r"\b([A-Za-z_]\w*)\s*\.", expr)} & set(aliases)
    frm, joined = f'"{alias}"', {alias}
    for u in sorted(used - {alias}):
        path = join_path(rels, alias, u)
        if path is None:
            raise ValueError(f"no join path {alias} -> {u}")
        for ft, fc, tt, tc in path:
            if tt in joined:
                continue
            cond = " AND ".join(f'"{ft}"."{a}" = "{tt}"."{b}"' for a, b in zip(fc, tc))
            frm += f' LEFT JOIN "{tt}" ON {cond}'
            joined.add(tt)
    q = sqlglot.transpile(f"SELECT {expr} FROM {frm}", read="snowflake", write="duckdb")[0]
    v = con.execute(q).fetchone()[0]
    return None if v is None else float(v)


# ------------------------------------------------------------------ SemaBridge stages (optional)
def semabridge_stages(pbix):
    try:
        from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
    except Exception as exc:
        print(f"(SemaBridge not importable - OSI/SML stages skipped: {exc})")
        return None, None
    tmsl = LocalPBIXConnector({"pbix_path": pbix}).discover().get("raw_tmsl")
    osi = TMSLToOSIConverter().to_osi({"tmsl": tmsl, "dataset_id": os.path.basename(pbix)})
    sml = OSIToSMLConverter().from_osi(osi)
    o = {key(m.unique_name): m for m in osi.metrics}
    s = {key(m.unique_name): m for m in sml.metrics}
    return o, s


# ------------------------------------------------------------------ SELECTEDVALUE fix
_SV = re.compile(r"^\s*SELECTEDVALUE\s*\(\s*(?:'([^']+)'|(\w[\w ]*))\s*\[([^\]]+)\]\s*(?:,\s*([^)]+?))?\s*\)\s*$", re.I)


def selectedvalue_fix(dax, ddl_tables):
    m = _SV.match(dax or "")
    if not m:
        return None
    table, col, alt = (m.group(1) or m.group(2)).strip(), m.group(3).strip(), (m.group(4) or "NULL").strip()
    alias = next((a for a in ddl_tables if key(a) == key(table) or key(ddl_tables[a]) == key(table)), sanitize(table, False))
    c = f"{alias}.{sanitize(col)}"
    alt_sql = "'" + alt[1:-1].replace("'", "''") + "'" if alt.startswith('"') else alt
    # exact DAX semantics: a blank counts as one more distinct value (VALUES() includes BLANK)
    return alias, (f"IFF(COUNT(DISTINCT {c}) + MAX(IFF({c} IS NULL, 1, 0)) = 1, MAX({c}), {alt_sql})")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbix", required=True); ap.add_argument("--ddl", required=True)
    ap.add_argument("--expected"); ap.add_argument("--repo")
    ap.add_argument("--out", default="measure_trace.csv")
    args = ap.parse_args()
    if args.repo:
        sys.path.insert(0, os.path.join(os.path.abspath(args.repo), "src"))

    tables, rels, metrics = parse_ddl(open(args.ddl, encoding="utf-8").read())
    aliases = set(tables)
    metric_names = set(metrics)
    expected = {k.upper(): v for k, v in json.load(open(args.expected)).items()} if args.expected else {}

    con, loaded, model = load_data(args.pbix, tables)
    measures = model.dax_measures
    osi, sml = semabridge_stages(args.pbix)
    ddl_by_key = {key(n): n for n in metrics}

    rows = []
    for _, mrow in measures.iterrows():
        name, home, dax = mrow["Name"], mrow["TableName"], mrow.get("Expression")
        k = key(name)
        r = {"measure": name, "pbix_table": home, "dax": " ".join(str(dax or "").split())[:300],
             "osi": "", "sml_sql": "", "sml_reason": "", "ddl_table": "", "ddl_status": "", "ddl_sql": "",
             "lost_at": "", "issues": "", "value": "", "expected": "", "value_ok": "",
             "fix_sql": "", "fix_value": ""}
        if osi is not None:
            r["osi"] = "yes" if k in osi else "NO"
            sm = sml.get(k) if sml else None
            r["sml_sql"] = "yes" if sm is not None and getattr(sm, "sql_expression", None) and not is_null_placeholder(sm.sql_expression) else ("NO" if sm is not None else "not in SML")
            r["sml_reason"] = str(getattr(sm, "sync_failure_reason", "") or "")[:200] if sm is not None else ""
        dn = ddl_by_key.get(key(sanitize(name, reserved=False))) or ddl_by_key.get(k)
        if not dax or not str(dax).strip():
            r["lost_at"] = "PBIX (empty measure)"
        elif dn is None:
            r["ddl_status"] = "MISSING"
            r["lost_at"] = ("OSI" if r["osi"] == "NO" else "SML" if r["sml_sql"] == "not in SML" else "DDL emission") if osi is not None else "before/at DDL (run with --repo to see the stage)"
        else:
            alias, expr = metrics[dn]
            r.update(ddl_table=alias, ddl_sql=expr[:400])
            if is_null_placeholder(expr):
                r["ddl_status"] = "NULL"
                r["lost_at"] = "translation (SML)" if r["sml_sql"] in ("NO", "") else "DDL emission (SML had SQL)"
            else:
                r["ddl_status"] = "REAL"
            r["issues"] = "; ".join(check_sql(alias, expr, metric_names, aliases, rels))
            if r["ddl_status"] == "REAL" and alias in loaded:
                try:
                    v = evaluate(con, alias, expr, rels, aliases)
                    r["value"] = v
                    if dn in expected:
                        e = expected[dn]
                        r["expected"] = e
                        r["value_ok"] = "OK" if (v is not None and math.isclose(v, e, rel_tol=1e-6, abs_tol=1e-9)) else "WRONG"
                except Exception as exc:
                    r["value"] = f"error: {str(exc).splitlines()[0][:120]}"
        fix = selectedvalue_fix(dax, tables)
        if fix:
            falias, fsql = fix
            r["fix_sql"] = fsql
            if falias in loaded:
                try:
                    r["fix_value"] = evaluate(con, falias, fsql, rels, aliases)
                except Exception as exc:
                    r["fix_value"] = f"error: {exc}"
        rows.append(r)

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    real = [r for r in rows if r["ddl_status"] == "REAL"]
    print(f"\nTables loaded from .pbix into DuckDB: {', '.join(f'{a}({n})' for a, n in loaded.items())}")
    print(f"Measures: {len(rows)}  REAL: {len(real)}  NULL: {sum(r['ddl_status'] == 'NULL' for r in rows)}  "
          f"MISSING: {sum(r['ddl_status'] == 'MISSING' for r in rows)}  with issues: {sum(bool(r['issues']) for r in rows)}")
    print(f"\n{'MEASURE':34} {'DDL':8} {'LOST AT':28} {'VALUE':>16} {'CHECK':6}  ISSUES")
    for r in rows:
        v = r["value"]
        vs = f"{v:,.4f}" if isinstance(v, float) else str(v)[:16]
        print(f"{r['measure'][:34]:34} {r['ddl_status'][:8]:8} {r['lost_at'][:28]:28} {vs:>16} {r['value_ok']:6}  {r['issues'][:110]}")
        if r["sml_reason"] and r["ddl_status"] != "REAL":
            print(f"{'':34}   reason: {r['sml_reason'][:150]}")
        if r["fix_sql"]:
            print(f"{'':34}   FIX SQL: {r['fix_sql']}   -> value on data: {r['fix_value']}")
    print(f"\nFull detail: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
