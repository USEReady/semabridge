#!/usr/bin/env python3
"""
sf_share_of_total_check.py - verifies IN SNOWFLAKE how share-of-total measures can be implemented.

PROBLEM
  DAX  DIVIDE([M], CALCULATE([M], ALL(<dim>)))  removes ONLY the filter on <dim>; every other filter/grouping
  (year, agent, region ...) stays in the denominator. SemaBridge currently divides by a whole-table total
  precomputed in the enriched view (COUNT(*) OVER ()), which is wrong as soon as another filter is applied.

WHAT IT TESTS (on your real tables; everything it creates lives in a SCRATCH schema and is dropped at the end)
  GROUND TRUTH  plain SQL on the base table:  COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY <other groupings>)
  A  current approach: scratch view with COUNT(*) OVER () AS ALL_COUNT + semantic view metric COUNT(*)/MAX(ALL_COUNT)
  B  window metric inside a semantic view: tries several syntax variants of
       SUM(<count metric>) OVER (PARTITION BY EXCLUDING <dim>)
     and reports for each: accepted by CREATE? query works? values exact?
  C  window on the semantic view result (what Cortex generates):
       SELECT ..., cnt / SUM(cnt) OVER (PARTITION BY <other groupings>) FROM SEMANTIC_VIEW(... METRICS cnt ...)

SCENARIOS
  S1  grouped by the share dimension, no filter
  S2  grouped by the share dimension, filtered to one year
  S3  grouped by a context column AND the share dimension
  Each approach PASSES a scenario only if every group equals the ground truth (and shares per context sum to 1).

SAFETY
  Source tables are only read. Creates/drops only: <scratch_schema>.SB_SOT_TEST_* objects.
  --dry-run prints every SQL statement without connecting.

USAGE (defaults = AI Sample "Cases % by Subject" and "Cases % by Product")
  uv run python sf_share_of_total_check.py --repo .                                   # SemaBridge connection settings
  uv run python sf_share_of_total_check.py --connection-name my_conn                 # ~/.snowflake/connections.toml
  uv run python sf_share_of_total_check.py --dry-run
  options: --db SEMABRIDGE --schema ARTIFICIAL_INTELLIGENCE --fact CASES --share-col SUBJECT
           --context-col AGENT --date-col CASE_CREATED_ON --scratch-schema SB_SCRATCH
           --dim-table PRODUCTS --dim-col PRODUCT --fk PRODUCTSEQ --pk PRODUCTSEQ   (share by a related table)
"""
import argparse, math, os, sys, traceback

P = "SB_SOT_TEST"


def connect(args):
    import snowflake.connector
    if args.connection_name:
        return snowflake.connector.connect(connection_name=args.connection_name)
    sys.path.insert(0, os.path.join(os.path.abspath(args.repo), "src"))
    import importlib
    found = {}
    for mod in ("semabridge.core.settings", "semabridge.config", "semabridge.config.settings", "semabridge.settings",
                "semabridge.connectors.connection_manager", "semabridge.utils.snowflake", "semabridge.connectors.snowflake_auth"):
        try:
            m = importlib.import_module(mod)
        except Exception:
            continue
        for fn in ("get_settings", "get_snowflake_connect_kwargs"):
            if fn not in found and hasattr(m, fn):
                found[fn] = getattr(m, fn)
    if len(found) < 2:
        sys.exit("Could not find SemaBridge's get_settings / get_snowflake_connect_kwargs - use --connection-name instead "
                 f"(found: {sorted(found)})")
    kw = found["get_snowflake_connect_kwargs"](found["get_settings"]().snowflake)
    return snowflake.connector.connect(**kw)


class Runner:
    def __init__(self, args):
        self.args, self.dry = args, args.dry_run
        self.con = None if self.dry else connect(args)

    def exec(self, sql, fetch=False):
        if self.dry:
            print(sql.strip() + ";\n")
            return []
        cur = self.con.cursor()
        try:
            cur.execute(sql)
            return cur.fetchall() if fetch else []
        finally:
            cur.close()


def build_sql(a):
    src = f"{a.db}.{a.schema}"
    scr = f"{a.db}.{a.scratch_schema}"
    fact = f"{src}.{a.fact}"
    share_by_dim = bool(a.dim_table)
    share_expr = f"d.{a.dim_col}" if share_by_dim else f"f.{a.share_col}"
    join = f"LEFT JOIN {src}.{a.dim_table} d ON f.{a.fk} = d.{a.pk}" if share_by_dim else ""
    share_dim = f"{a.dim_table}.{a.dim_col}" if share_by_dim else f"F.{a.share_col}"
    tables = f"F AS {fact}" + (f",\n    {a.dim_table} AS {src}.{a.dim_table} PRIMARY KEY ({a.pk})" if share_by_dim else "")
    rels = f"RELATIONSHIPS (F_TO_DIM AS F ({a.fk}) REFERENCES {a.dim_table} ({a.pk}))" if share_by_dim else ""
    dims = [f"F.CTX AS F.{a.context_col}", f"F.YR AS YEAR(F.{a.date_col})"]
    dims += [f"{a.dim_table}.SHARE AS {a.dim_table}.{a.dim_col}"] if share_by_dim else [f"F.SHARE AS F.{a.share_col}"]
    share_ref = f"{a.dim_table}.SHARE" if share_by_dim else "F.SHARE"
    return dict(src=src, scr=scr, fact=fact, share_expr=share_expr, join=join, share_dim=share_dim, tables=tables,
                rels=rels, dims=dims, share_ref=share_ref)


def truth_sql(s, a, scenario, year):
    where = f"WHERE YEAR(f.{a.date_col}) = {year}" if scenario == "S2" else ""
    ctx = [f"f.{a.context_col}"] if scenario == "S3" else []
    g = ", ".join(ctx + [s["share_expr"]])
    part = f"PARTITION BY {', '.join(ctx)}" if ctx else ""
    return (f"SELECT {', '.join([f'{c} AS CTX' for c in ctx] + [s['share_expr'] + ' AS SHARE'])}, "
            f"COUNT(*) / NULLIF(SUM(COUNT(*)) OVER ({part}), 0) AS V FROM {s['fact']} f {s['join']} {where} GROUP BY {g}")


def sv_query(sv, s, scenario, year, metric):
    dims = (["F.CTX"] if scenario == "S3" else []) + [s["share_ref"]]
    where = f" WHERE F.YR = {year}" if scenario == "S2" else ""
    return f"SELECT * FROM SEMANTIC_VIEW({sv} DIMENSIONS {', '.join(dims)} METRICS {metric}{where})"


def window_on_result(sv, s, scenario, year):
    inner = sv_query(sv, s, scenario, year, "F.CNT")
    part = "PARTITION BY CTX" if scenario == "S3" else ""
    return f"SELECT *, CNT / NULLIF(SUM(CNT) OVER ({part}), 0) AS V FROM ({inner})"


def compare(truth, got, scenario):
    """truth/got: dict key->value. Returns (ok, max_diff, sum_range)."""
    if not got:
        return False, None, None
    diffs = [abs((got.get(k) or 0) - v) for k, v in truth.items()]
    ok = all(d < 1e-9 for d in diffs) and len(got) == len(truth)
    sums = {}
    for k, v in got.items():
        ctx = k[0] if scenario == "S3" else "*"
        sums[ctx] = sums.get(ctx, 0) + (v or 0)
    return ok, max(diffs), (min(sums.values()), max(sums.values()))


def rows_to_dict(rows, cols, scenario):
    out = {}
    for r in rows:
        d = dict(zip(cols, r))
        key = ((d.get("CTX"),) if scenario == "S3" else ()) + (d.get("SHARE"),)
        out[key] = float(d["V"]) if d.get("V") is not None else None
    return out


def query_dict(run, sql, scenario):
    if run.dry:
        run.exec(sql)
        return {}
    cur = run.con.cursor()
    try:
        cur.execute(sql)
        cols = [c[0].upper() for c in cur.description]
        rows = cur.fetchall()
    finally:
        cur.close()
    # normalise column names coming from SEMANTIC_VIEW (CTX/SHARE/metric names)
    norm = []
    for c in cols:
        if c.endswith("CTX"):
            norm.append("CTX")
        elif c.endswith("SHARE"):
            norm.append("SHARE")
        elif c in ("V", "PCT", "PCT_W") or c.startswith("PCT"):
            norm.append("V")
        else:
            norm.append(c)
    return rows_to_dict(rows, norm, scenario)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="."); ap.add_argument("--connection-name"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default="SEMABRIDGE"); ap.add_argument("--schema", default="ARTIFICIAL_INTELLIGENCE")
    ap.add_argument("--fact", default="CASES"); ap.add_argument("--share-col", default="SUBJECT")
    ap.add_argument("--context-col", default="AGENT"); ap.add_argument("--date-col", default="CASE_CREATED_ON")
    ap.add_argument("--dim-table"); ap.add_argument("--dim-col"); ap.add_argument("--fk"); ap.add_argument("--pk")
    ap.add_argument("--scratch-schema", default="SB_SCRATCH"); ap.add_argument("--year", type=int)
    ap.add_argument("--keep", action="store_true", help="do not drop the scratch objects")
    a = ap.parse_args()
    s = build_sql(a)
    run = Runner(a)
    scr = s["scr"]
    created = []

    def try_create(name, sql):
        try:
            run.exec(sql); created.append(name); return None
        except Exception as exc:
            return str(exc).splitlines()[0][:220]

    run.exec(f"CREATE SCHEMA IF NOT EXISTS {scr}")
    year = a.year
    if year is None and not run.dry:
        year = run.exec(f"SELECT MAX(YEAR({a.date_col})) FROM {s['fact']}", fetch=True)[0][0]
    year = year or 2021
    print(f"share dimension: {s['share_dim']}   context: {a.context_col}   year filter: {year}\n")

    # ---------------- A: precomputed whole-table total in a view
    view_a = f"{scr}.{P}_ENRICHED"
    err_view = try_create(view_a, f"CREATE OR REPLACE VIEW {view_a} AS SELECT f.*, COUNT(*) OVER () AS ALL_COUNT FROM {s['fact']} f")
    base_dims = ",\n    ".join(s["dims"])
    sv_a = f"{scr}.{P}_SV_A"
    err_a = err_view or try_create(sv_a, f"""CREATE OR REPLACE SEMANTIC VIEW {sv_a}
  TABLES ({s['tables'].replace(s['fact'], view_a, 1)})
  {s['rels']}
  DIMENSIONS ({base_dims})
  METRICS (F.CNT AS COUNT(*), F.PCT AS COUNT(*) / NULLIF(MAX(F.ALL_COUNT), 0))""")

    # ---------------- B: window metric inside the semantic view (syntax variants)
    excl = s["share_ref"]
    variants = {
        "B1 one metric: CNT / SUM(CNT) OVER (PARTITION BY EXCLUDING dim)":
            f"F.CNT AS COUNT(*), F.PCT AS F.CNT / NULLIF(SUM(F.CNT) OVER (PARTITION BY EXCLUDING {excl}), 0)",
        "B2 two metrics: TOT = SUM(CNT) OVER (...); PCT = CNT / TOT":
            f"F.CNT AS COUNT(*), F.TOT AS SUM(F.CNT) OVER (PARTITION BY EXCLUDING {excl}), F.PCT AS F.CNT / NULLIF(F.TOT, 0)",
        "B3 unqualified metric reference":
            f"F.CNT AS COUNT(*), F.TOT AS SUM(CNT) OVER (PARTITION BY EXCLUDING {excl}), F.PCT AS F.CNT / NULLIF(F.TOT, 0)",
        "B4 view-level derived metric (no table prefix)":
            f"F.CNT AS COUNT(*), F.TOT AS SUM(F.CNT) OVER (PARTITION BY EXCLUDING {excl}), PCT AS F.CNT / NULLIF(F.TOT, 0)",
        "B5 direct: COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY EXCLUDING dim)":
            f"F.PCT AS COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY EXCLUDING {excl}), 0)",
    }
    sv_b = {}
    for i, (label, metrics) in enumerate(variants.items(), 1):
        name = f"{scr}.{P}_SV_B{i}"
        err = try_create(name, f"""CREATE OR REPLACE SEMANTIC VIEW {name}
  TABLES ({s['tables']})
  {s['rels']}
  DIMENSIONS ({base_dims})
  METRICS ({metrics})""")
        sv_b[label] = (name, err, "PCT" if "B4" in label else "F.PCT")

    # ---------------- C: base semantic view (count only) + window on the result
    sv_c = f"{scr}.{P}_SV_C"
    err_c = try_create(sv_c, f"""CREATE OR REPLACE SEMANTIC VIEW {sv_c}
  TABLES ({s['tables']})
  {s['rels']}
  DIMENSIONS ({base_dims})
  METRICS (F.CNT AS COUNT(*))""")

    # ---------------- run scenarios
    results = {}
    for scenario, title in (("S1", "no filter"), ("S2", f"year = {year}"), ("S3", f"by {a.context_col} and share dim")):
        print(f"=== {scenario}: {title} ===")
        truth = query_dict(run, truth_sql(s, a, scenario, year), scenario)

        def report(label, fn):
            try:
                got = fn()
                if run.dry:
                    return
                ok, md, sr = compare(truth, got, scenario)
                results.setdefault(label, []).append(ok)
                print(f"  {label:66} {'PASS' if ok else 'FAIL'}   max diff {md:.4f}   shares per context sum {sr[0]:.3f}..{sr[1]:.3f}")
            except Exception as exc:
                results.setdefault(label, []).append(False)
                print(f"  {label:66} ERROR {str(exc).splitlines()[0][:150]}")

        if err_a:
            print(f"  A precomputed OVER () in view: could not create: {err_a}")
        else:
            report("A precomputed OVER () in view (current SemaBridge)", lambda: query_dict(run, sv_query(sv_a, s, scenario, year, "F.PCT"), scenario))
        for label, (name, err, metric) in sv_b.items():
            if err:
                if scenario == "S1":
                    print(f"  {label:66} NOT ACCEPTED: {err}")
                results.setdefault(label, []).append(False)
            else:
                report(label, lambda n=name, m=metric: query_dict(run, sv_query(n, s, scenario, year, m), scenario))
        if err_c:
            print(f"  C window on semantic-view result: could not create base view: {err_c}")
        else:
            report("C window on the semantic-view result (Cortex-style)", lambda: query_dict(run, window_on_result(sv_c, s, scenario, year), scenario))
        print()

    if not run.dry:
        print("=== SUMMARY (PASS = exact in all 3 scenarios) ===")
        for label, oks in results.items():
            print(f"  {label:66} {'PASS' if oks and all(oks) else 'FAIL'}  ({sum(oks)}/{len(oks)})")
    if not a.keep:
        for name in reversed(created):
            kind = "SEMANTIC VIEW" if "_SV_" in name else "VIEW"
            try:
                run.exec(f"DROP {kind} IF EXISTS {name}")
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc(); sys.exit(2)
