#!/usr/bin/env python3
"""
run_snowflake_tests.py - the feature cases all the way into SNOWFLAKE. Nothing is guessed: the semantic
view is really created and every metric is really queried.

  1. load the synthetic fixture tables into a scratch schema   (default SEMABRIDGE.SB_FEATURE_TESTS)
  2. run SemaBridge's pipeline on the cases: TMSL -> OSI -> SML -> DDL   (pipeline_adapter.py)
     (or --ddl-file: use a DDL you generated with SemaBridge's own dry run against that schema)
  3. CREATE the semantic view in the scratch schema
  4. query every metric:  SELECT * FROM SEMANTIC_VIEW(<view> METRICS <table>.<metric>)
  5. compare each result with the value computed independently from the data

Per case it reports the LAST stage reached:
  OSI -> SML -> SML_SQL -> DDL (REAL / NULL / MISSING) -> VIEW_CREATED -> QUERY_OK -> VALUE_OK
so a measure cannot disappear anywhere without being reported.

  uv run python phase_a\\feature_tests\\run_snowflake_tests.py                      # full run
  uv run python phase_a\\feature_tests\\run_snowflake_tests.py --feature F3
  uv run python phase_a\\feature_tests\\run_snowflake_tests.py --dry-run            # print SQL, touch nothing
  uv run python phase_a\\feature_tests\\run_snowflake_tests.py --keep               # keep the scratch schema
Connection: snowflake_config.json next to phase_a (same as validate_semantic_view.py), key pair or browser SSO.
Exit code 1 on a regression vs the previous run (feature_test_results\\snowflake.json) or a failed view.
"""
import argparse, json, math, os, re, sys, datetime as dt
from collections import OrderedDict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import pandas as pd
import pipeline_adapter as pa
from fixture import TABLES
from cases import CASES
from relationship_inventory import sb_column
from measure_inventory import ddl_metrics
from run_pipeline_tests import build_tmsl, names, key, mname
from run_feature_tests import close

SF_TYPE = {"int64": "NUMBER(38,0)", "float64": "FLOAT", "bool": "BOOLEAN"}

def sf_type(s):
    k = str(s.dtype)
    if k in SF_TYPE: return SF_TYPE[k]
    if s.dropna().map(lambda v: isinstance(v, (dt.date, dt.datetime))).all() and s.notna().any(): return "DATE"
    return "VARCHAR"

def setup_sql(db, schema):
    stmts = [f'CREATE SCHEMA IF NOT EXISTS {db}.{schema}', f'USE SCHEMA {db}.{schema}']
    loads = []
    for t, df in TABLES.items():
        tn = sb_column(t).upper()
        cols = ", ".join(f'"{sb_column(c)}" {sf_type(df[c])}' for c in df.columns)
        stmts.append(f'CREATE OR REPLACE TABLE {db}.{schema}."{tn}" ({cols})')
        rows = [tuple(None if (isinstance(v, float) and math.isnan(v)) or v is None or v is pd.NaT else
                      (v.item() if hasattr(v, "item") else v) for v in r) for r in df.itertuples(index=False)]
        ph = ", ".join(["%s"] * len(df.columns))
        loads.append((f'INSERT INTO {db}.{schema}."{tn}" VALUES ({ph})', rows))
    return stmts, loads

def view_name_of(ddl):
    m = re.search(r"semantic\s+view\s+(?:if\s+not\s+exists\s+)?([\w\".$]+)", ddl, re.I)
    return m.group(1) if m else None

def retarget(ddl, db, schema, view):
    """Point the DDL at the scratch schema and the test view name (only db.schema prefixes are changed)."""
    ddl = re.sub(r"(semantic\s+view\s+(?:if\s+not\s+exists\s+)?)[\w\".$]+", rf"\g<1>{db}.{schema}.{view}", ddl, count=1, flags=re.I)
    return re.sub(r'"?[A-Za-z_][\w$]*"?\s*\.\s*"?[A-Za-z_][\w$]*"?\s*\.\s*(?="?[A-Za-z_])', f"{db}.{schema}.", ddl)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feature"); ap.add_argument("--database", default="SEMABRIDGE"); ap.add_argument("--schema", default="SB_FEATURE_TESTS")
    ap.add_argument("--view", default="SB_FEATURE_TESTS_SEMANTIC"); ap.add_argument("--ddl-file")
    ap.add_argument("--config"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--keep", action="store_true")
    ap.add_argument("--results", default=os.path.join("feature_test_results", "snowflake.json"))
    args = ap.parse_args()
    db, sc = args.database, args.schema
    cases = [c for c in CASES if not args.feature or c["id"].split(".")[0] == args.feature.upper()]

    # ---- 1. scratch tables
    stmts, loads = setup_sql(db, sc)
    cur = None
    if args.dry_run:
        print("\n-- setup (dry run)"); [print(s + ";") for s in stmts]; [print(f"{q};  -- {len(r)} rows") for q, r in loads]
    else:
        from validate_semantic_view import connect
        conn = connect(argparse.Namespace(config=args.config)); cur = conn.cursor()
        for s in stmts: cur.execute(s)
        for q, rows in loads: cur.executemany(q, rows)
        print(f"Loaded {len(loads)} tables into {db}.{sc}")

    # ---- 2. pipeline -> DDL
    stage_err, osi = {}, None
    live = {sb_column(t).upper(): {sb_column(c) for c in df.columns} for t, df in TABLES.items()}
    if args.ddl_file:
        ddl, sml = open(args.ddl_file, encoding="utf-8").read(), None
    else:
        try: osi = pa.to_osi(build_tmsl(cases), "feature_tests")
        except Exception as e: stage_err["OSI"] = str(e)
        try: sml = pa.to_sml(osi) if osi is not None else None
        except Exception as e: sml, stage_err["SML"] = None, str(e)
        if sml is not None: pa.normalize(sml)
        try: ddl = pa.generate_ddl(sml, live, db, sc) if sml is not None else ""
        except Exception as e: ddl, stage_err["DDL"] = "", str(e)
    for s, e in stage_err.items():
        print(f"STAGE {s} FAILED: {e[:300]}  -> wire pipeline_adapter.py")
    ddl = retarget(ddl, db, sc, args.view) if ddl else ""
    o_names, s_names = (names(osi) if osi is not None else {}), (names(sml) if sml is not None else {})
    d_mets = {key(v["name"]): v for v in ddl_metrics(ddl).values()} if ddl else {}

    # ---- 3. create the view
    view_ok, view_err = False, ""
    if ddl and args.dry_run:
        print("\n-- semantic view (dry run)\n" + ddl[:3000] + ("\n..." if len(ddl) > 3000 else ""))
    elif ddl:
        try: cur.execute(ddl); view_ok = True
        except Exception as e: view_err = str(e).splitlines()[0][:300]; print(f"\nVIEW NOT CREATED: {view_err}")

    # ---- 4/5. query each metric
    rows, board = OrderedDict(), OrderedDict()
    for c in cases:
        k = key(mname(c["id"])); kind = c["expect"][0]; d = d_mets.get(k)
        st = OrderedDict(OSI=(k in o_names) if osi is not None else None, SML=(k in s_names) if s_names else None,
                         DDL=None if not ddl else ("MISSING" if not d else ("NULL" if d["null"] else "REAL")),
                         VIEW_CREATED=view_ok, QUERY_OK=None, VALUE=None)
        status, msg = "PASS", ""
        if kind == "value":
            if st["DDL"] != "REAL":
                status, msg = "FAIL", f"lost before Snowflake: OSI={st['OSI']} SML={st['SML']} DDL={st['DDL']}"
            elif args.dry_run:
                status, msg = "SKIP", "dry run"
            elif not view_ok:
                status, msg = "FAIL", f"view not created: {view_err}"
            else:
                q = f"SELECT * FROM SEMANTIC_VIEW({db}.{sc}.{args.view} METRICS {d['table']}.{d['name']})"
                try:
                    cur.execute(q); got = cur.fetchone()[0]; st["QUERY_OK"] = True
                    got = float(got) if got is not None else None; st["VALUE"] = got
                    exp = float(c["expect"][1]())
                    if not close(got, exp): status, msg = "FAIL", f"Snowflake value {got}, expected {exp}"
                except Exception as e:
                    st["QUERY_OK"] = False; status, msg = "FAIL", f"query failed: {str(e).splitlines()[0][:200]}"
        elif kind == "not_applicable":
            if st["DDL"] in ("REAL", "NULL"): status, msg = "FAIL", f"emitted as a {st['DDL']} metric"
        else:
            if st["DDL"] == "REAL": status, msg = "FAIL", "malformed DAX emitted as REAL SQL"
        rows[c["id"]] = {"status": status, "msg": msg, "stages": st, "title": c["title"]}
        b = board.setdefault(c["feature"], [0, 0]); b[0] += status == "PASS"; b[1] += 1

    print(f"\n{'FEATURE':28} PASS / TOTAL   (OSI -> SML -> DDL -> Snowflake view -> query -> value)")
    for f, (p, t) in board.items(): print(f"{f:28} {p:>4} / {t}")
    for cid, r in rows.items():
        if r["status"] != "PASS":
            print(f"\n{r['status']} {cid} {r['title']}\n     {r['msg']}\n     stages: " +
                  " | ".join(f"{s}={v}" for s, v in r["stages"].items()))

    if cur is not None and not args.keep:
        cur.execute(f"DROP SCHEMA IF EXISTS {db}.{sc}"); print(f"\nDropped scratch schema {db}.{sc} (use --keep to inspect)")
    if args.dry_run: return
    os.makedirs(os.path.dirname(args.results) or ".", exist_ok=True)
    prev = json.load(open(args.results)) if os.path.exists(args.results) else {}
    regress = [k for k, r in rows.items() if prev.get(k, {}).get("status") == "PASS" and r["status"] != "PASS"]
    fixed = [k for k, r in rows.items() if r["status"] == "PASS" and prev.get(k, {}).get("status") not in (None, "PASS")]
    json.dump({**prev, **rows}, open(args.results, "w"), indent=1, default=str)
    print(f"\nvs previous run: {len(fixed)} newly passing {fixed}\n                 {len(regress)} REGRESSED {regress}")
    sys.exit(1 if regress or stage_err or (ddl and not view_ok) else 0)

if __name__ == "__main__":
    main()
