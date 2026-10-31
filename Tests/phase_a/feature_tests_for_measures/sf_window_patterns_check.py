#!/usr/bin/env python3
"""
sf_window_patterns_check.py - proves in Snowflake which window-metric forms give the DAX result.

PART A  deployed view (no changes): queries an existing share-of-total metric the way Cortex would -
        with ONLY the share dimension, with NO dimension, with a parent attribute (e.g. product category),
        with a year filter - and compares with ground truth. Answers: "must every excluded dimension be in the query?"
PART B  scratch semantic view (created in a scratch schema, dropped at the end) with these metrics:
        P1 total ignoring one dimension      CALCULATE([Count], ALL(T[dim]))        SUM(CNT) OVER (PARTITION BY EXCLUDING dim)
        P2 AVERAGE ignoring one dimension    CALCULATE(AVERAGE(x), ALL(T[dim]))     SUM(SUM_X) OVER (..) / SUM(CNT_X) OVER (..)
        P2w the WRONG shortcut for AVERAGE   (expected to FAIL)                     AVG(AVG_X) OVER (..)
        P3 ratio ignoring one dimension      CALCULATE([Rate], ALL(T[dim]))         SUM(NUM) OVER (..) / SUM(CNT) OVER (..)
        P3w the WRONG shortcut for a ratio   (expected to FAIL)                     SUM(RATE) OVER (..)
        P4 share with a RELATED table        DIVIDE([Count], CALCULATE([Count], ALL(Dim)))  minimal exclusion (display column)
        P5 MAX ignoring one dimension        CALCULATE(MAX(x), ALL(T[dim]))         MAX(MAX_X) OVER (..)
        P6 filtered numerator share          DIVIDE(CALCULATE([Count], flag), CALCULATE([Count], ALL(T[dim])))
        P7 fixed filter inside the ALL part  CALCULATE([Count], ALL(T[dim]), flag)  SUM(NUM) OVER (..) (filtered base metric)
        'dimension not in query' / 'no grouping' -> EXPECTED ERROR 010277 (documented Snowflake limit)
        each in scenarios: grouped by dim / by dim + year filter / by context + dim / by context only / no grouping.
Ground truth = the DAX meaning computed in Python from the raw rows (filters kept, only the ALL() dimension removed).

Safe: source tables are only read; Part B creates/drops <db>.<scratch_schema>.SB_WP_TEST_* only.
usage:
  uv run python sf_window_patterns_check.py --repo .                 # A + B with AI defaults
  uv run python sf_window_patterns_check.py --repo . --part A
  uv run python sf_window_patterns_check.py --dry-run                # print SQL only
  options: --db --schema --fact CASES --dim-col SUBJECT --ctx-col AGENT --date-col CASE_CREATED_ON
           --num-col CSAT --flag-col IS_ESCALATED --rel-table PRODUCTS --rel-key PRODUCTSEQ --rel-cols PRODUCT,PRODUCT_CATEGORY
           --view ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC --view-metric CASES.CASES_PCT_BY_PRODUCT
           --view-dims PRODUCTS.PRODUCT,PRODUCTS.PRODUCT_CATEGORY --view-year CASE_CALENDAR.YEAR --scratch-schema SB_SCRATCH
"""
import argparse, importlib, math, os, sys, traceback

P = "SB_WP_TEST"


def connect(a):
    import snowflake.connector
    if a.connection_name:
        return snowflake.connector.connect(connection_name=a.connection_name)
    sys.path.insert(0, os.path.join(os.path.abspath(a.repo), "src"))
    try:
        from semabridge.repository.credential_manager import CredentialManager
        from semabridge.core.settings import reload_settings
        CredentialManager().inject_credentials_to_env("snowflake", user_id=1)
        reload_settings()
    except Exception:
        pass
    found = {}
    for mod in ("semabridge.config", "semabridge.core.settings", "semabridge.settings", "semabridge.connectors.connection_manager",
                "semabridge.connectors.snowflake_connection", "semabridge.utils.snowflake"):
        try:
            m = importlib.import_module(mod)
        except Exception:
            continue
        for fn in ("get_settings", "get_snowflake_connect_kwargs"):
            if fn not in found and hasattr(m, fn):
                found[fn] = getattr(m, fn)
    if len(found) < 2:
        sys.exit("Could not find SemaBridge connection helpers - use --connection-name")
    return snowflake.connector.connect(**found["get_snowflake_connect_kwargs"](found["get_settings"]().snowflake))


class SF:
    def __init__(self, a):
        self.dry = a.dry_run
        self.con = None if self.dry else connect(a)

    def run(self, sql, fetch=True):
        if self.dry:
            print(sql.strip() + ";\n"); return [], []
        cur = self.con.cursor()
        try:
            cur.execute(sql)
            if not fetch:
                return [], []
            cols = [c[0].upper().strip('"') for c in cur.description]
            return cols, cur.fetchall()
        finally:
            cur.close()


def close(a, b, tol=1e-6):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)


def compare(name, got, truth):
    """got/truth: dict key -> value"""
    if not got and truth:
        print(f"  {name:62} FAIL  no rows returned"); return False
    bad = [(k, got.get(k), v) for k, v in truth.items() if not close(got.get(k), v)]
    extra = [k for k in got if k not in truth]
    ok = not bad and not extra
    md = max((abs((g or 0) - (t or 0)) for _, g, t in bad), default=0.0)
    print(f"  {name:62} {'PASS' if ok else 'FAIL'}" + ("" if ok else f"  max diff {md:.4f}  e.g. {bad[:1] or extra[:1]}"))
    return ok


# ------------------------------------------------------------------ data + ground truth
def load_rows(sf, a):
    src = f"{a.db}.{a.schema}"
    rc = ", ".join(f"d.{c}" for c in a.rel_cols.split(","))
    sql = (f"SELECT f.{a.dim_col} AS DIM, f.{a.ctx_col} AS CTX, YEAR(f.{a.date_col}) AS YR, f.{a.num_col}::FLOAT AS X, "
           f"IFF(f.{a.flag_col}, 1, 0) AS FLAG, {rc} "
           f"FROM {src}.{a.fact} f LEFT JOIN {src}.{a.rel_table} d ON f.{a.rel_key} = d.{a.rel_key}")
    cols, rows = sf.run(sql)
    import pandas as pd
    return pd.DataFrame(rows, columns=cols)


def truth(df, keys, exclude, year, kind):
    """DAX meaning: aggregate per group; for the ALL() part, remove `exclude` from the group keys (filters stay)."""
    d = df[df.YR == year] if year else df
    ctx = [k for k in keys if k not in exclude]
    def agg(g, what):
        if what == "cnt": return float(len(g))
        if what == "avg": return float(g.X.mean()) if g.X.notna().any() else None
        if what == "rate": return float(g.FLAG.sum()) / len(g) if len(g) else None
        if what == "max": return float(g.X.max()) if g.X.notna().any() else None
        if what == "fnum": return float(g.FLAG.sum())
    out = {}
    if kind == "share":
        num = d.groupby(keys, dropna=False).size() if keys else None
        den = d.groupby(ctx, dropna=False).size() if ctx else float(len(d))
        if not keys:
            return {(): 1.0}
        for k, n in num.items():
            k = k if isinstance(k, tuple) else (k,)
            kk = dict(zip(keys, k))
            dv = den if not ctx else den[tuple(kk[c] for c in ctx) if len(ctx) > 1 else kk[ctx[0]]]
            out[k] = n / dv
        return out
    if kind == "fshare":      # filtered numerator / total ignoring the dim
        out = {}
        groups = d.groupby(keys, dropna=False) if keys else [((), d)]
        for k, g in groups:
            k = k if isinstance(k, tuple) else (k,)
            kk = dict(zip(keys, k)); sub = d
            for c in ctx:
                sub = sub[sub[c] == kk[c]]
            out[k] = float(g.FLAG.sum()) / len(sub) if len(sub) else None
        return out
    what = {"total": "cnt", "avg": "avg", "rate": "rate", "max": "max", "fnum": "fnum"}[kind]
    groups = d.groupby(keys, dropna=False) if keys else [((), d)]
    for k, g in groups:
        k = k if isinstance(k, tuple) else (k,)
        kk = dict(zip(keys, k))
        sub = d
        for c in ctx:
            sub = sub[sub[c] == kk[c]]
        out[k] = agg(sub, what)
    return out


def result_dict(cols, rows, key_cols, val_col):
    out = {}
    for r in rows:
        rec = dict(zip(cols, r))
        k = tuple(rec.get(c) for c in key_cols)
        v = rec.get(val_col)
        out[k] = None if v is None else float(v)
    return out


# ------------------------------------------------------------------ PART A
def part_a(sf, a, df):
    print("\n=== PART A: deployed view, queried like Cortex would ===")
    view = f"{a.db}.{a.schema}.{a.view}"
    vdims = a.view_dims.split(",")
    rel_cols = a.rel_cols.split(",")
    metric_col = a.view_metric.split(".")[-1].upper()
    scenarios = [   # (title, dims, year, expectation) - expectation 'value' or 'error-ok' (010277 is the documented limit)
        ("only the share dimension", [vdims[0]], None, "value"),
        ("only the share dimension, year filter", [vdims[0]], a.year, "value"),
        ("parent attribute only (e.g. category)", [vdims[1]] if len(vdims) > 1 else [vdims[0]], None, "error-ok"),
        ("no dimension", [], None, "error-ok"),
    ]
    ok = True
    for title, dims, yr, expect in scenarios:
        where = f" WHERE {a.view_year} = {yr}" if yr else ""
        dpart = f"DIMENSIONS {', '.join(dims)} " if dims else ""
        sql = f"SELECT * FROM SEMANTIC_VIEW({view} {dpart}METRICS {a.view_metric}{where})"
        try:
            cols, rows = sf.run(sql)
        except Exception as exc:
            msg = str(exc)
            if expect == "error-ok" and "010277" in msg:
                print(f"  {title:62} EXPECTED ERROR (required dimension not in query - documented limit)"); continue
            ok = False
            print(f"  {title:62} ERROR {msg.splitlines()[0][:140]}"); continue
        if sf.dry:
            continue
        keys = [rel_cols[vdims.index(d)] for d in dims]
        kcols = [d.split(".")[-1].upper() for d in dims]
        got = result_dict(cols, rows, kcols, metric_col)
        t = truth(df.rename(columns={c: c for c in df.columns}), [c.upper() for c in keys], [c.upper() for c in rel_cols], yr, "share")
        ok &= compare(title, got, t)
    return ok


# ------------------------------------------------------------------ PART B
def part_b(sf, a, df):
    print("\n=== PART B: window forms in a scratch semantic view ===")
    src, scr = f"{a.db}.{a.schema}", f"{a.db}.{a.scratch_schema}"
    rel_cols = a.rel_cols.split(",")
    sv = f"{scr}.{P}_SV"
    E = "F.D_DIM"
    ER = ", ".join(f"R.D_{c}" for c in rel_cols)
    base = ("F.CNT AS COUNT(*), F.SUM_X AS SUM(F.X_COL::FLOAT), F.CNT_X AS COUNT(F.X_COL), "
            "F.AVG_X AS AVG(F.X_COL::FLOAT), F.MAX_X AS MAX(F.X_COL::FLOAT), F.NUM AS SUM(IFF(F.FLAG_COL, 1, 0)), F.RATE AS SUM(IFF(F.FLAG_COL, 1, 0)) / NULLIF(COUNT(*), 0)")
    variants = {
        "P1": f"F.P1 AS SUM(F.CNT) OVER (PARTITION BY EXCLUDING {E})",
        "P2": f"F.P2 AS SUM(F.SUM_X) OVER (PARTITION BY EXCLUDING {E}) / NULLIF(SUM(F.CNT_X) OVER (PARTITION BY EXCLUDING {E}), 0)",
        "P2W": f"F.P2W AS AVG(F.AVG_X) OVER (PARTITION BY EXCLUDING {E})",
        "P3": f"F.P3 AS SUM(F.NUM) OVER (PARTITION BY EXCLUDING {E}) / NULLIF(SUM(F.CNT) OVER (PARTITION BY EXCLUDING {E}), 0)",
        "P3W": f"F.P3W AS SUM(F.RATE) OVER (PARTITION BY EXCLUDING {E})",
        "P4": f"F.P4 AS F.CNT / NULLIF(SUM(F.CNT) OVER (PARTITION BY EXCLUDING R.D_{rel_cols[0]}), 0)",
        "P5": f"F.P5 AS MAX(F.MAX_X) OVER (PARTITION BY EXCLUDING {E})",
        "P6": f"F.P6 AS F.NUM::FLOAT / NULLIF(SUM(F.CNT::FLOAT) OVER (PARTITION BY EXCLUDING {E}), 0)",
        "P7": f"F.P7 AS SUM(F.NUM) OVER (PARTITION BY EXCLUDING {E})",
    }
    dims = (f"F.D_DIM AS F.{a.dim_col}, F.D_CTX AS F.{a.ctx_col}, F.D_YR AS YEAR(F.{a.date_col}), "
            + ", ".join(f"R.D_{c} AS R.{c}" for c in rel_cols))
    ddl_head = (f"CREATE OR REPLACE SEMANTIC VIEW {sv}\n  TABLES (F AS {src}.{a.fact}, R AS {src}.{a.rel_table} PRIMARY KEY ({a.rel_key}))\n"
                f"  RELATIONSHIPS (F_R AS F ({a.rel_key}) REFERENCES R ({a.rel_key}))\n  DIMENSIONS ({dims})\n")
    base = base.replace("X_COL", a.num_col).replace("FLAG_COL", a.flag_col)
    sf.run(f"CREATE SCHEMA IF NOT EXISTS {scr}", fetch=False)
    # create one view per variant so one rejected form does not block the others
    accepted = {}
    for name, m in variants.items():
        try:
            sf.run(ddl_head.replace(f"{P}_SV", f"{P}_SV_{name}") + f"  METRICS ({base}, {m})", fetch=False)
            accepted[name] = f"{scr}.{P}_SV_{name}"
        except Exception as exc:
            print(f"  {name:5} NOT ACCEPTED by Snowflake: {str(exc).splitlines()[0][:150]}")
    kinds = {"P1": "total", "P2": "avg", "P2W": "avg", "P3": "rate", "P3W": "rate", "P4": "share",
             "P5": "max", "P6": "fshare", "P7": "fnum"}
    expect_fail = {"P2W", "P3W"}
    scen = [("by dim", ["D_DIM"], None), ("by dim, year filter", ["D_DIM"], a.year),
            ("by context + dim", ["D_CTX", "D_DIM"], None), ("by context only (dim not in query)", ["D_CTX"], None),
            ("no grouping", [], None)]
    colmap = {"D_DIM": "DIM", "D_CTX": "CTX"}
    summary = {}
    for name, view in accepted.items():
        res = []
        for title, keys, yr in scen:
            if name == "P4":
                keys = [k if k != "D_DIM" else f"D_{rel_cols[0]}" for k in keys]
            where = f" WHERE F.D_YR = {yr}" if yr else ""
            dpart = ("DIMENSIONS " + ", ".join(("R." if k.startswith("D_") and k[2:] in rel_cols else "F.") + k for k in keys) + " ") if keys else ""
            sql = f"SELECT * FROM SEMANTIC_VIEW({view} {dpart}METRICS F.{name}{where})"
            try:
                cols, rows = sf.run(sql)
            except Exception as exc:
                msg = str(exc)
                if "010277" in msg and ("dim not in query" in title or "no grouping" in title):
                    print(f"  {name:4} {title:57} EXPECTED ERROR (required dimension)"); continue
                print(f"  {name:4} {title:57} ERROR {msg.splitlines()[0][:120]}"); res.append(False); continue
            if sf.dry:
                continue
            got = result_dict(cols, rows, keys, name)
            tkeys = [colmap.get(k, k[2:]) for k in keys]
            excl = [r for r in rel_cols] if name == "P4" else ["DIM"]
            t = truth(df, tkeys, excl, yr, kinds[name])
            res.append(compare(f"{name:4} {title}", got, t))
        summary[name] = res
    if not sf.dry:
        print("\n  SUMMARY")
        for name in variants:
            r = summary.get(name)
            if r is None:
                verdict = "not accepted"
            else:
                passed = all(r) and r
                verdict = "PASS (all scenarios)" if passed else f"FAIL ({sum(r)}/{len(r)})"
                if name in expect_fail:
                    verdict += "  <- expected to FAIL (wrong shortcut)" if not passed else "  <- unexpected PASS, check data"
            print(f"  {name:5} {verdict}")
    for name in accepted:
        try:
            sf.run(f"DROP SEMANTIC VIEW IF EXISTS {scr}.{P}_SV_{name}", fetch=False)
        except Exception:
            pass
    return all(all(v) for k, v in summary.items() if k not in expect_fail)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="."); ap.add_argument("--connection-name"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--part", choices=["A", "B", "all"], default="all")
    ap.add_argument("--db", default="SEMABRIDGE"); ap.add_argument("--schema", default="ARTIFICIAL_INTELLIGENCE")
    ap.add_argument("--fact", default="CASES"); ap.add_argument("--dim-col", default="SUBJECT"); ap.add_argument("--ctx-col", default="AGENT")
    ap.add_argument("--date-col", default="CASE_CREATED_ON"); ap.add_argument("--num-col", default="CSAT"); ap.add_argument("--flag-col", default="IS_ESCALATED")
    ap.add_argument("--rel-table", default="PRODUCTS"); ap.add_argument("--rel-key", default="PRODUCTSEQ"); ap.add_argument("--rel-cols", default="PRODUCT,PRODUCT_CATEGORY")
    ap.add_argument("--view", default="ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC"); ap.add_argument("--view-metric", default="CASES.CASES_PCT_BY_PRODUCT")
    ap.add_argument("--view-dims", default="PRODUCTS.PRODUCT,PRODUCTS.PRODUCT_CATEGORY"); ap.add_argument("--view-year", default="CASE_CALENDAR.YEAR")
    ap.add_argument("--year", type=int, default=2021); ap.add_argument("--scratch-schema", default="SB_SCRATCH")
    a = ap.parse_args()
    sf = SF(a)
    df = None if a.dry_run else load_rows(sf, a)
    if df is not None:
        df.columns = [c.upper() for c in df.columns]
    ok = True
    if a.part in ("A", "all"):
        ok &= part_a(sf, a, df)
    if a.part in ("B", "all"):
        ok &= part_b(sf, a, df)
    print(f"\n{'ALL REQUIRED CHECKS PASS' if ok else 'SOME CHECKS FAILED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(); sys.exit(2)
