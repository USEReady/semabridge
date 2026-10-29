#!/usr/bin/env python3
"""
validate_complex_translation.py - checks SemaBridge's TRANSLATION of complex measures (before DDL emission):
dependency resolution, anchoring, VAR inlining, SELECTEDVALUE / what-if handling and values. Generic; offline.

It runs SemaBridge itself, then judges the result per measure:
  --stage sml         (default) full pipeline from the .pbix: TMSL -> OSI -> SML; uses each metric's
                      sql_expression, dataset (anchor), sync flag and failure reason
  --stage translator  DAXTranslator directly, measures translated in dependency order with the already
                      translated measures passed as metrics_context (isolates translator bugs from pipeline bugs)

Per measure:
  DAG             measure -> measure dependencies (incl. 'Table'[Measure] syntax), cycles, topological order
  STATUS          REAL / NULL (+ reason) versus what the DAX allows (generic expectation)
  ROOT CAUSE      CASCADE_FROM [X] (a dependency failed) vs OWN_FAILURE (all dependencies are REAL but this one
                  is still NULL -> not re-translated after dependencies resolved, or a real gap)
  DEPENDENCIES    every REAL dependency must be inlined (its SQL appears) or referenced by metric name;
                  emitting it as a column -> PHANTOM_MEASURE_COLUMN
  ANCHOR          expected anchor from lineage = the table of the measure's physical columns (after expanding
                  dependencies) that reaches all other used tables; compared with SemaBridge's dataset
  DISCONNECTED    a measure on a table with NO relationships (what-if / parameter table) used by another measure:
                  the dependent must use the default value, never reference that table
  SELECTEDVALUE   a SELECTEDVALUE measure must be IFF(COUNT(DISTINCT c) + MAX(IFF(c IS NULL,1,0)) = 1, MAX(c), alt)
  STRUCTURE       same checks as validate_complex_ddl.py (nested aggregate, bare column, DAX left-overs, blow-up ...)
  VALUE           SQL run on the .pbix data (DuckDB, joins over the model's active relationships) vs --expected

usage:
  uv run python validate_complex_translation.py --pbix "<model>.pbix" --repo . [--dax-query measures.dax]
         [--stage sml|translator] [--expected expected.json] [--measure "Rev Goal"] [--out complex_translation_report.csv]
needs: SemaBridge importable (--repo <repo root>), pip install duckdb sqlglot pandas pbixray
place next to validate_complex_ddl.py (shared helpers).
"""
import argparse, csv, os, re, sys
from collections import OrderedDict, defaultdict, deque
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_complex_ddl import (sanitize, norm, mkey, strip_strings, parse_dax_query, measures_from_pbix,  # noqa: E402
                                  is_null_placeholder, structural_issues, expected_status, load_expected, close)

SV = re.compile(r"^\s*SELECTEDVALUE\s*\(\s*(?:'([^']+)'|(\w[\w ]*))\s*\[([^\]]+)\]\s*(?:,\s*([^)]+?))?\s*\)\s*$", re.I)
SV_EXACT = re.compile(r"IFF\s*\(\s*COUNT\s*\(\s*DISTINCT\s+[^)]+\)\s*\+\s*MAX\s*\(\s*IFF\s*\([^)]*IS\s+NULL", re.I)


# ------------------------------------------------------------------ model metadata from the .pbix
class Model:
    def __init__(self, px):
        self.px = px
        sch = px.schema
        self.columns = defaultdict(set)                       # table -> {source column names}
        for _, r in sch.iterrows():
            self.columns[r["TableName"]].add(r["ColumnName"])
        rel = px.relationships
        self.rels = [(r.FromTableName, r.FromColumnName, r.ToTableName, r.ToColumnName, bool(r.IsActive)) for r in rel.itertuples()]
        self.related = {t for r in self.rels for t in (r[0], r[2])}
        self.tables = list(self.columns)

    def reaches(self, a, b):
        seen, dq = {a}, deque([a])
        while dq:
            cur = dq.popleft()
            if cur == b:
                return True
            for ft, _, tt, _, act in self.rels:
                if act and ft == cur and tt not in seen:
                    seen.add(tt); dq.append(tt)
        return a == b

    def column_table(self, ref_table, col):
        """Table of a DAX column reference; '' if it is not a column."""
        if ref_table and col in self.columns.get(ref_table, ()):
            return ref_table
        return ""


REF = re.compile(r"(?:'([^']+)'|\b([A-Za-z_][\w ]*?))?\s*\[([^\]]+)\]")


def dax_refs(dax, measure_names, model, home):
    """-> (measure dependencies, {tables of physical columns})"""
    deps, tabs = set(), set()
    for m in REF.finditer(re.sub(r"//[^\n]*|--[^\n]*|/\*.*?\*/", "", dax or "", flags=re.S)):
        table = (m.group(1) or m.group(2) or "").strip()
        name = m.group(3).strip()
        if name in measure_names and not model.column_table(table, name):
            deps.add(name)
        else:
            t = model.column_table(table, name) or model.column_table(home, name)
            if t:
                tabs.add(t)
    for m in re.finditer(r"\b(?:COUNTROWS|SUMX|AVERAGEX|MINX|MAXX|COUNTAX)\s*\(\s*(?:'([^']+)'|([A-Za-z_][\w ]*?))\s*[,)]", dax or "", re.I):
        t = (m.group(1) or m.group(2) or "").strip()
        if t in model.columns:
            tabs.add(t)
    return deps, tabs


def topo(dag):
    order, state = [], {}
    cycles = set()

    def visit(n, stack):
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            cycles.update(stack[stack.index(n):]); return
        state[n] = 1
        for d in sorted(dag.get(n, ())):
            visit(d, stack + [d])
        state[n] = 2; order.append(n)
    for n in dag:
        visit(n, [n])
    return order, cycles


# ------------------------------------------------------------------ SemaBridge runs
def run_sml(pbix):
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    tm = LocalPBIXConnector({"pbix_path": pbix}).discover().get("raw_tmsl")
    osi = TMSLToOSIConverter().to_osi({"tmsl": tm, "dataset_id": os.path.basename(pbix)})
    sml = OSIToSMLConverter().from_osi(osi)
    out = {}
    for m in sml.metrics:
        out[mkey(getattr(m, "unique_name", "") or getattr(m, "name", ""))] = SimpleNamespace(
            sql=getattr(m, "sql_expression", None), dataset=getattr(m, "dataset", "") or "",
            enabled=getattr(m, "sync_enabled", True),
            reason=str(getattr(m, "sync_failure_reason", "") or getattr(m, "exclusion_reason", "") or ""))
    return out


def run_translator(measures, order):
    from semabridge.converter.dax_translator import DAXTranslator
    try:
        from semabridge.utils.naming import to_alias
    except Exception:
        to_alias = lambda t: re.sub(r"[^A-Za-z0-9_]", "_", t).lower()
    tr, out, ctx = DAXTranslator(), {}, []
    for name in order:
        home, dax = measures[name]
        try:
            try:
                res = tr.translate(dax, to_alias(home), home, metric_name=name, skip_tier5=True, metrics_context=list(ctx))
            except TypeError:
                res = tr.translate(dax, to_alias(home), home, metric_name=name, skip_tier5=True)
            sql = getattr(res, "sql", None)
            ok = bool(getattr(res, "is_success", False) and sql and not is_null_placeholder(sql))
            reason = " ".join(str(getattr(res, a, "") or "") for a in ("failure_reason", "reason")).strip()
        except Exception as exc:
            sql, ok, reason = None, False, f"{type(exc).__name__}: {exc}"
        out[mkey(name)] = SimpleNamespace(sql=sql if ok else None, dataset=home, enabled=ok, reason=reason)
        ctx.append(SimpleNamespace(unique_name=name, name=name, label=name, expression=dax, dataset=home,
                                   sql_expression=sql if ok else None, tier=1, sync_enabled=ok))
    return out


# ------------------------------------------------------------------ DuckDB on the .pbix data
class Data:
    def __init__(self, model):
        import duckdb
        self.con, self.model = duckdb.connect(), model
        self.name = {}                                     # pbix table -> duckdb table
        self.variant = {}                                  # normalized alias variant -> duckdb table
        for t in model.tables:
            try:
                df = model.px.get_table(t).copy()
            except Exception:
                continue
            df.columns = [sanitize(c) for c in df.columns]
            dn = sanitize(t, False).upper() or "T"
            self.con.register("_t", df); self.con.execute(f'CREATE TABLE "{dn}" AS SELECT * FROM _t'); self.con.unregister("_t")
            self.name[t] = dn
            alias_forms = {t, dn, sanitize(t), re.sub(r"[^A-Za-z0-9]", "", t)}
            try:
                from semabridge.utils.naming import to_alias
                alias_forms.add(to_alias(t))
            except Exception:
                pass
            for v in alias_forms:
                self.variant[norm(v)] = dn
        self.cols = {dn: {r[0].upper() for r in self.con.execute(f'DESCRIBE "{dn}"').fetchall()} for dn in self.name.values()}
        self.edges = [(self.name[f], sanitize(fc), self.name[t], sanitize(tc)) for f, fc, t, tc, act in model.rels
                      if act and f in self.name and t in self.name]

    def canonical(self, sql):
        return re.sub(r'(?<![\w."])("?[A-Za-z_][\w$]*"?)(\s*\.)',
                      lambda m: '"' + self.variant[norm(m.group(1))] + '"' + m.group(2) if norm(m.group(1)) in self.variant else m.group(0), sql)

    def evaluate(self, sql, anchor_table):
        import sqlglot
        sql = self.canonical(sql)
        used = {u for u in self.cols if re.search(rf'"{re.escape(u)}"\s*\.', sql)}
        base = self.name.get(anchor_table) or (sorted(used)[0] if used else None)
        if base is None:
            raise ValueError("no table")
        frm, joined = f'"{base}"', {base}
        for u in sorted(used - {base}):
            prev, dq = {base: None}, deque([base])
            while dq:
                cur = dq.popleft()
                for e in self.edges:
                    if e[0] == cur and e[2] not in prev:
                        prev[e[2]] = e; dq.append(e[2])
            if u not in prev:
                raise ValueError(f"no relationship path {base} -> {u}")
            path, n = [], u
            while prev[n]:
                path.append(prev[n]); n = prev[n][0]
            for fa, fc, ta, tc in reversed(path):
                if ta not in joined:
                    frm += f' LEFT JOIN "{ta}" ON "{fa}"."{fc}" = "{ta}"."{tc}"'; joined.add(ta)
        q = sqlglot.transpile(f"SELECT {sql} FROM {frm}", read="snowflake", write="duckdb")[0]
        v = self.con.execute(q).fetchone()[0]
        return None if v is None else float(v)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbix", required=True); ap.add_argument("--repo", default=".")
    ap.add_argument("--dax-query"); ap.add_argument("--stage", choices=["sml", "translator"], default="sml")
    ap.add_argument("--expected"); ap.add_argument("--measure", action="append")
    ap.add_argument("--out", default="complex_translation_report.csv")
    args = ap.parse_args()
    sys.path.insert(0, os.path.join(os.path.abspath(args.repo), "src"))
    import logging, warnings
    warnings.filterwarnings("ignore"); logging.getLogger("sqlglot").setLevel(logging.ERROR)
    from pbixray import PBIXRay

    px = PBIXRay(args.pbix)
    model = Model(px)
    all_measures = measures_from_pbix(px)
    if args.dax_query:
        all_measures.update(parse_dax_query(open(args.dax_query, encoding="utf-8").read()))
    names = set(all_measures)
    dag, phys = {}, {}
    for n, (home, dax) in all_measures.items():
        dag[n], phys[n] = dax_refs(dax, names, model, home)
    order, cycles = topo(dag)

    def lineage(n, seen=None):
        seen = seen or set()
        if n in seen:
            return set()
        seen.add(n)
        out = set(phys.get(n, ()))
        for d in dag.get(n, ()):
            out |= lineage(d, seen)
        return out

    expected = load_expected(args.expected)
    print(f"measures: {len(all_measures)}  dependency edges: {sum(len(v) for v in dag.values())}  cycles: {sorted(cycles) or 'none'}")
    print(f"running SemaBridge ({args.stage}) ...")
    res = run_sml(args.pbix) if args.stage == "sml" else run_translator(all_measures, order)
    data = Data(model)
    known_cols = {dn: cols for dn, cols in data.cols.items()}
    measure_cols = {sanitize(n, False) for n in names} | {sanitize(n) for n in names}

    broken = set()   # REAL but with blocking issues -> dependents are reported as cascades
    selected = [n for n in order if not args.measure or mkey(n) in {mkey(m) for m in args.measure}]
    rows, status = [], {}
    for n in order:
        r = res.get(mkey(n))
        status[n] = "REAL" if (r and r.sql and not is_null_placeholder(r.sql)) else ("NULL" if r else "MISSING")

    for n in selected:
        home, dax = all_measures[n]
        r = res.get(mkey(n))
        st = status[n]
        exp_st = expected_status(dax)
        used_tabs = lineage(n)
        related_tabs = {t for t in used_tabs if t in model.related}
        used_tabs = related_tabs or used_tabs          # parameter tables (no relationships) never anchor a metric
        candidates = [t for t in used_tabs if all(model.reaches(t, o) for o in used_tabs)]
        exp_anchor = candidates[0] if len(candidates) == 1 else (" / ".join(sorted(candidates)) if candidates else "")
        row = OrderedDict(measure=n, dax_table=home, depends_on=", ".join(sorted(dag[n])), expected_status=exp_st,
                          status=st, anchor=(r.dataset if r else ""), expected_anchor=exp_anchor, root_cause="",
                          reason=(r.reason[:200] if r else ""), issues="", value="", expected="", value_check="",
                          sql=((r.sql or "")[:600] if r else ""))
        iss = []
        if n in cycles:
            iss.append("CYCLE")
        failed_deps = [d for d in dag[n] if status.get(d) != "REAL" or d in broken]
        if st != "REAL":
            if failed_deps:
                row["root_cause"] = "CASCADE_FROM " + ", ".join(sorted(failed_deps)) + (
                    " (dependency has wrong SQL)" if any(d in broken for d in failed_deps) else "")
            elif exp_st == "REAL":
                row["root_cause"] = "OWN_FAILURE"
                iss.append("NULL_BUT_TRANSLATABLE" + (" (all dependencies REAL - not re-translated?)" if dag[n] else ""))
            if st == "NULL" and not row["reason"]:
                iss.append("NO_REASON")
        else:
            sql = r.sql
            canon = data.canonical(sql)
            for k, v in structural_issues(canon, None, known_cols, measure_cols):
                iss.append(f"{k}: {v}")
            # anchor: SemaBridge's dataset vs lineage
            if exp_anchor and " / " not in exp_anchor and r.dataset and norm(r.dataset) not in {norm(exp_anchor), norm(sanitize(exp_anchor, False))}:
                iss.append(f"WRONG_ANCHOR: on {r.dataset}, data comes from {exp_anchor}")
            # dependencies: inlined or referenced by name; disconnected tables must not leak into dependents
            for d in sorted(dag[n]):
                dr = res.get(mkey(d))
                dhome = all_measures[d][0]
                if dhome not in model.related and dhome in data.name and re.search(rf'"{re.escape(data.name[dhome])}"\s*\.', canon):
                    iss.append(f"DISCONNECTED_TABLE_IN_DEPENDENT: uses {dhome} (no relationships) - use the parameter default")
                if dr and dr.sql and status.get(d) == "REAL":
                    inlined = re.sub(r"\s+", "", strip_strings(dr.sql).upper())[:60] in re.sub(r"\s+", "", strip_strings(sql).upper())
                    by_name = re.search(rf"\b{re.escape(sanitize(d, False))}\b", sql, re.I)
                    if not inlined and not by_name:
                        iss.append(f"DEPENDENCY_NOT_VISIBLE: [{d}] neither inlined nor referenced")
            sv = SV.match(dax or "")
            if sv and not SV_EXACT.search(sql):
                iss.append("SELECTEDVALUE_NOT_EXACT: expected IFF(COUNT(DISTINCT c) + MAX(IFF(c IS NULL,1,0)) = 1, MAX(c), alt)")
            if exp_st != "REAL":
                iss.append(f"CHECK_SEMANTICS: DAX uses {exp_st[9:-1]} - translated anyway, verify it is exact")
            try:
                row["value"] = data.evaluate(sql, r.dataset if r.dataset in data.name else (exp_anchor if exp_anchor in data.name else home))
            except Exception as exc:
                row["value"] = "error: " + str(exc).splitlines()[0][:120]
            if mkey(n) in expected and (isinstance(row["value"], float) or row["value"] is None):
                row["expected"] = expected[mkey(n)]
                row["value_check"] = "OK" if close(row["value"], expected[mkey(n)]) else "WRONG"
        row["issues"] = "; ".join(iss)
        if st == "REAL" and (row["value_check"] == "WRONG" or re.search(
                r"PHANTOM|UNKNOWN_COLUMN|NOT_AGGREGATED|NESTED|DAX_LEFTOVER|WRONG_ANCHOR|DISCONNECTED|ANY_VALUE", row["issues"])):
            broken.add(n)
        rows.append(row)

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    blocking = [r for r in rows if r["value_check"] == "WRONG" or r["root_cause"] == "OWN_FAILURE"
                or re.search(r"PHANTOM|UNKNOWN_COLUMN|NOT_AGGREGATED|NESTED|DAX_LEFTOVER|WRONG_ANCHOR|DISCONNECTED|ANY_VALUE|UNSAFE|SELECTEDVALUE_NOT|CYCLE", r["issues"])]
    cnt = lambda s: sum(r["status"] == s for r in rows)
    print(f"\nchecked: {len(rows)}  REAL: {cnt('REAL')}  NULL: {cnt('NULL')}  MISSING: {cnt('MISSING')}  "
          f"value OK: {sum(r['value_check'] == 'OK' for r in rows)}  WRONG: {sum(r['value_check'] == 'WRONG' for r in rows)}  blocking: {len(blocking)}")
    print(f"\n{'MEASURE':30} {'STATUS':7} {'ANCHOR':16} {'EXPECTED ANCHOR':16} {'VALUE':>16} {'CHECK':6} ROOT CAUSE / ISSUES")
    for r in rows:
        v = r["value"]; vs = f"{v:,.4f}" if isinstance(v, float) else str(v)[:16]
        print(f"{r['measure'][:30]:30} {r['status']:7} {str(r['anchor'])[:16]:16} {r['expected_anchor'][:16]:16} {vs:>16} "
              f"{r['value_check']:6} {(r['root_cause'] + ' ' + r['issues']).strip()[:130]}")
        if r["status"] != "REAL" and r["reason"]:
            print(f"{'':30}   reason: {r['reason'][:150]}")
    print(f"\nFull detail: {os.path.abspath(args.out)}")
    sys.exit(1 if blocking else 0)


if __name__ == "__main__":
    main()
