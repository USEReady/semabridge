#!/usr/bin/env python3
"""
run_pipeline_tests.py - the feature cases through the WHOLE SemaBridge pipeline, so a measure cannot be
lost anywhere unnoticed. For every case it reports the stage where it was lost, and checks the final value.

  TMSL (built from fixture + cases) -> S5 OSI -> S6 SML (translation, dependencies) -> S7 DDL -> S8 value

Stages per case:  OSI present | SML present | SML has SQL | DDL: REAL / NULL / MISSING | value correct
  uv run python phase_a\\feature_tests\\run_pipeline_tests.py
  uv run python phase_a\\feature_tests\\run_pipeline_tests.py --feature F2
Exit code 1 if any case that passed before now fails (results kept in feature_test_results\\pipeline.json).
(Extraction from a .pbix (S1-S4) is covered by measure_stage_diagnostic.py on real files.)
"""
import argparse, json, os, re, sys
from collections import OrderedDict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import pandas as pd
import pipeline_adapter as pa
from fixture import TABLES, RELATIONSHIPS
from cases import CASES, BASE
import mutate
import run_feature_tests as rft
import adapter
from relationship_inventory import sb_column
from measure_inventory import ddl_metrics
from run_feature_tests import build_db, evaluate, close

DT = {"int64": "int64", "float64": "double", "bool": "boolean", "object": "string"}

def mname(cid):
    return "Case " + cid.replace(".", "_")

def build_tmsl(cases):
    tables = []
    for t, df in TABLES.items():
        cols = []
        for c in df.columns:
            kind = str(df[c].dtype)
            if kind == "object" and df[c].dropna().map(lambda v: hasattr(v, "toordinal")).all():
                kind = "dateTime"
            cols.append({"name": c, "dataType": DT.get(kind, kind), "sourceColumn": c})
        tables.append({"name": t, "columns": cols, "measures": []})
    by = {t["name"]: t for t in tables}
    defined = set()
    for c in cases:
        for n, (home, dax) in {**(c.get("context") or {}), **BASE}.items():
            if n not in defined and (n in (c.get("context") or {})):
                by[home]["measures"].append({"name": n, "expression": dax}); defined.add(n)
        by[c["home"]]["measures"].append({"name": mname(c["id"]), "expression": c["dax"]})
    rels = [{"name": f"r{i}", "fromTable": ft, "fromColumn": fc, "toTable": tt, "toColumn": tc, "isActive": True,
             "fromCardinality": "many", "toCardinality": "one", "crossFilteringBehavior": "oneDirection"}
            for i, (ft, fc, tt, tc) in enumerate(RELATIONSHIPS)]
    for i, (ft, fc, tt, tc) in enumerate(getattr(rft, "INACTIVE", ())):
        rels.append({"name": f"rinact_{i}", "fromTable": ft, "fromColumn": fc, "toTable": tt, "toColumn": tc, "isActive": False,
                     "fromCardinality": "many", "toCardinality": "one", "crossFilteringBehavior": "oneDirection"})
    return {"name": "feature_tests", "model": {"tables": tables, "relationships": rels}}

def names(model):
    out = {}
    for mt in getattr(model, "metrics", None) or []:
        n = getattr(mt, "unique_name", None) or getattr(mt, "name", "")
        out[re.sub(r"[^a-z0-9]", "", str(n).lower())] = mt
    return out

def key(n):
    return re.sub(r"[^a-z0-9]", "", str(n).lower())

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feature"); ap.add_argument("--results", default=os.path.join("feature_test_results", "pipeline.json"))
    ap.add_argument("--mutate", type=int, metavar="SEED", help="rename every table/column (no-hardcoding check)")
    args = ap.parse_args()
    global TABLES, RELATIONSHIPS, CASES, BASE
    measure_table = "Measures"
    if args.mutate is not None:
        TABLES, fact, RELATIONSHIPS, CASES, BASE, measure_table, nm = mutate.apply(
            args.mutate, TABLES, rft.FACT, RELATIONSHIPS, CASES, BASE, rft.INACTIVE)
        rft.TABLES, rft.FACT, rft.RELATIONSHIPS, rft.MEASURE_TABLE = TABLES, fact, RELATIONSHIPS, measure_table
        rft.INACTIVE = mutate.MUTATED_INACTIVE
        print("Renamed (seed %d): %s" % (args.mutate, ", ".join(f"{k} -> {v}" for k, v in nm.items())))
        args.results = args.results.replace("pipeline.json", f"pipeline_mutated_{args.mutate}.json")
    cases = [c for c in CASES if not args.feature or c["id"].split(".")[0] == args.feature.upper()]
    tmsl = build_tmsl(cases)
    live = {sb_column(t).upper(): {sb_column(c) for c in df.columns} for t, df in TABLES.items()}

    stage_err = {}
    try: osi = pa.to_osi(tmsl, "feature_tests")
    except Exception as e: osi, stage_err["OSI"] = None, f"{type(e).__name__}: {e}"
    try: sml = pa.to_sml(osi) if osi is not None else None
    except Exception as e: sml, stage_err["SML"] = None, f"{type(e).__name__}: {e}"
    if sml is not None: pa.normalize(sml)
    try: ddl = pa.generate_ddl(sml, live) if sml is not None else ""
    except Exception as e: ddl, stage_err["DDL"] = "", f"{type(e).__name__}: {e}"
    for s, e in stage_err.items():
        print(f"STAGE {s} FAILED: {e[:300]}\n  -> wire pipeline_adapter.py for your repo")

    o_names, s_names = names(osi) if osi else {}, names(sml) if sml else {}
    d_mets = {key(v["name"]): v for v in ddl_metrics(ddl).values()} if ddl else {}
    con = build_db()
    rows, board = OrderedDict(), OrderedDict()
    for c in cases:
        k = key(mname(c["id"])); kind = c["expect"][0]
        st = {"OSI": k in o_names if osi else None, "SML": k in s_names if sml else None}
        smt = s_names.get(k)
        st["SML_SQL"] = bool(smt is not None and getattr(smt, "sql_expression", None)
                              and not re.match(r"^\(?\s*CAST\s*\(\s*NULL", str(smt.sql_expression), re.I))
        d = d_mets.get(k)
        st["DDL"] = None if not ddl else ("MISSING" if not d else ("NULL" if d["null"] else "REAL"))
        lost = next((s for s in ("OSI", "SML") if st[s] is False), "")
        if kind == "value":
            if not lost and not st["SML_SQL"]: lost = "SML (no SQL: translation)"
            if not lost and st["DDL"] in ("NULL", "MISSING"): lost = f"DDL ({st['DDL']})"
            status, msg = ("FAIL", f"lost at {lost}") if lost else ("PASS", "")
            if status == "PASS" and st["DDL"] == "REAL":
                try:
                    got, used = evaluate(con, d["expr"], c.get("last_period")); exp = float(c["expect"][1]())
                    want_anchor = adapter.alias(c["anchor"]).upper() if c.get("anchor") else None
                    line = next((l for l in ddl.splitlines() if re.search(rf'\.\s*"?{re.escape(d["name"])}"?\s', l, re.I)), "")
                    if measure_table in used: status, msg = "FAIL", "DDL references the empty measure table"
                    elif want_anchor and d["table"].upper().strip('"') != want_anchor and re.sub(r"[^A-Z0-9]", "", d["table"].upper()) != re.sub(r"[^A-Z0-9]", "", want_anchor):
                        status, msg = "FAIL", f"anchored on {d['table']}, expected {want_anchor} (table of its columns)"
                    elif c.get("ddl_contains") and c["ddl_contains"].upper() not in " ".join(line.upper().split()):
                        status, msg = "FAIL", f"DDL line lacks '{c['ddl_contains']}': {line.strip()[:120]}"
                    elif not close(got, exp): status, msg = "FAIL", f"DDL value {got}, expected {exp}"
                except Exception as e:
                    status, msg = "FAIL", f"DDL SQL does not run: {str(e).splitlines()[0][:150]}"
            elif status == "PASS":
                status, msg = "SKIP", "no DDL generated"
        elif kind == "not_applicable":
            status, msg = (("FAIL", "emitted as REAL metric") if st["DDL"] == "REAL" else ("PASS", "emitted as NULL placeholder" if st["DDL"] == "NULL" else "not emitted"))
        else:
            status, msg = ("FAIL", "malformed DAX emitted as REAL SQL") if st["DDL"] == "REAL" else ("PASS", "not emitted")
        rows[c["id"]] = {"status": status, "msg": msg, "stages": st, "title": c["title"], "ddl_sql": d["expr"][:300] if d else None}
        b = board.setdefault(c["feature"], [0, 0]); b[0] += status == "PASS"; b[1] += 1

    print(f"\n{'FEATURE':28} PASS / TOTAL   (through OSI -> SML -> DDL -> value)")
    for f, (p, t) in board.items():
        print(f"{f:28} {p:>4} / {t}")
    lost_at = OrderedDict()
    for cid, r in rows.items():
        if r["status"] == "FAIL":
            lost_at.setdefault(r["msg"][:45], []).append(cid)
    print("\nWhere failing cases are lost:")
    for where, ids in lost_at.items():
        print(f"  {where:45} {len(ids):3}  {', '.join(ids)}")
    for cid, r in rows.items():
        if r["status"] != "PASS":
            s = r["stages"]
            print(f"\n{r['status']} {cid} {r['title']}: {r['msg']}\n     OSI={s['OSI']} SML={s['SML']} SML_SQL={s['SML_SQL']} DDL={s['DDL']}"
                  + (f"\n     ddl: {r['ddl_sql']}" if r["ddl_sql"] else ""))

    os.makedirs(os.path.dirname(args.results) or ".", exist_ok=True)
    prev = json.load(open(args.results)) if os.path.exists(args.results) else {}
    regress = [k for k, r in rows.items() if prev.get(k, {}).get("status") == "PASS" and r["status"] != "PASS"]
    fixed = [k for k, r in rows.items() if r["status"] == "PASS" and prev.get(k, {}).get("status") not in (None, "PASS")]
    json.dump({**prev, **rows}, open(args.results, "w"), indent=1)
    print(f"\nvs previous run: {len(fixed)} newly passing {fixed}\n                 {len(regress)} REGRESSED {regress}")
    sys.exit(1 if regress or stage_err else 0)

if __name__ == "__main__":
    main()
