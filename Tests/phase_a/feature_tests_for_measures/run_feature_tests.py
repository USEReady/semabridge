#!/usr/bin/env python3
"""
run_feature_tests.py - run after EVERY feature you implement. Generic: synthetic data, no model names.

For each DAX pattern (cases.py) it calls SemaBridge's translator (adapter.py), runs the returned SQL on the
synthetic data (DuckDB, Snowflake SQL transpiled with sqlglot) and compares the RESULT with a value computed
independently from the data. Then it compares with the previous run and fails on any regression.

  uv run python phase_a\\feature_tests\\run_feature_tests.py                 # all features
  uv run python phase_a\\feature_tests\\run_feature_tests.py --feature F3     # one feature
  uv run python phase_a\\feature_tests\\run_feature_tests.py --selftest       # checks the harness itself
needs: pip install duckdb sqlglot pandas
Exit code 1 if any case that passed in the previous run now fails (regression).
"""
import argparse, json, math, os, re, sys
from collections import OrderedDict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import duckdb, sqlglot
from fixture import TABLES, FACT, RELATIONSHIPS
try:
    from fixture import INACTIVE
except ImportError:
    INACTIVE = []
import cross_table
EDGES = []
from cases import CASES, BASE
import mutate
MEASURE_TABLE = "Measures"
from relationship_inventory import sb_column          # SemaBridge naming rules (uppercase, specials -> _, reserved -> COL_)
import adapter

def build_db():
    global EDGES
    con = duckdb.connect()
    EDGES = cross_table.build(con, TABLES, RELATIONSHIPS, INACTIVE, adapter.alias, sb_column)
    return con

def _aliases():
    return [adapter.alias(t) for t in TABLES] + [e[2] for e in EDGES if e[2] not in {adapter.alias(t) for t in TABLES}]

def evaluate(con, sql, last_period=None):
    """Run a metric expression on the synthetic data. last_period=(table, column) simulates a
    NON ADDITIVE BY (column) metric: the value is taken at the last value of that column."""
    force, where = (), ""
    if last_period:
        pa, pc = adapter.alias(last_period[0]), sb_column(last_period[1])
        force, where = (pa,), f' WHERE "{pa}"."{pc}" = (SELECT MAX("{pc}") FROM "{pa}")'
    for t in TABLES:
        phys = sb_column(t).upper()
        ali = adapter.alias(t)
        if phys != ali:
            sql = re.sub(rf'(?<![\w\"])"?{re.escape(phys)}"?\.', f'{ali}.', sql)
    frm, used = cross_table.from_clause(sql, _aliases(), EDGES, adapter.alias(FACT), force)
    q = sqlglot.transpile(f"SELECT {sql} FROM {frm}{where}", read="snowflake", write="duckdb")[0]
    v = con.execute(q).fetchone()[0]
    names = {adapter.alias(t): t for t in TABLES}
    return (float(v) if v is not None else None), [names.get(u, u) for u in used]

def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, float(b), rel_tol=1e-6, abs_tol=1e-9)

def run_case(con, c, selftest):
    kind = c["expect"][0]
    if selftest:
        if kind != "value":
            return "SKIP", "no expected SQL for this kind", None
        res = {"status": "ok", "sql": c["expected_sql"], "reason": ""}
    else:
        res = adapter.translate(c["dax"], c["home"], c["id"], c.get("context"))
    sql = res["sql"]
    if kind == "value":
        if res["status"] != "ok":
            return "FAIL", f"{res['status']}: {res['reason'][:160]}", sql
        try:
            got, used = evaluate(con, sql, c.get("last_period"))
        except Exception as exc:
            return "FAIL", f"SQL does not run: {str(exc).splitlines()[0][:160]}", sql
        exp = float(c["expect"][1]())
        if MEASURE_TABLE in used:
            return "FAIL", "references the empty measure table instead of the table in the DAX", sql
        if c.get("anchor") and c["home"] != c["anchor"] and c["home"] in used and c["home"] != MEASURE_TABLE:
            return "FAIL", f"uses its DAX home table {c['home']} instead of the data table {c['anchor']}", sql
        return ("PASS", f"{got} == {exp}", sql) if close(got, exp) else ("FAIL", f"value {got}, expected {exp}", sql)
    if kind == "no_sql":
        return ("PASS", "rejected", sql) if res["status"] != "ok" else ("FAIL", "returned SQL for malformed input", sql)
    # not_applicable
    if res["status"] == "ok":
        return "FAIL", "translated to a metric although it is " + c["expect"][1], sql
    if res["status"] == "not_applicable":
        return "PASS", f"excluded: {res['reason'][:80]}", sql
    return "WARN", f"no SQL (good) but no 'not applicable' reason exposed ({res['status']})", sql

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feature", help="run one feature, e.g. F3")
    ap.add_argument("--selftest", action="store_true", help="evaluate the reference SQL instead of SemaBridge")
    ap.add_argument("--results", default=os.path.join("feature_test_results", "latest.json"))
    ap.add_argument("--mutate", type=int, metavar="SEED", help="rename every table/column (no-hardcoding check)")
    args = ap.parse_args()
    global TABLES, FACT, RELATIONSHIPS, CASES, MEASURE_TABLE, INACTIVE
    if args.mutate is not None:
        TABLES, FACT, RELATIONSHIPS, CASES, _, MEASURE_TABLE, names = mutate.apply(
            args.mutate, TABLES, FACT, RELATIONSHIPS, CASES, BASE, INACTIVE)
        INACTIVE = mutate.MUTATED_INACTIVE
        print("Renamed (seed %d): %s" % (args.mutate, ", ".join(f"{k} -> {v}" for k, v in names.items())))
        if args.results.endswith("latest.json"):
            args.results = args.results.replace("latest.json", f"latest_mutated_{args.mutate}.json")

    con = build_db()
    cases = [c for c in CASES if not args.feature or c["id"].split(".")[0] == args.feature.upper()]
    results, board = {}, OrderedDict()
    for c in cases:
        status, msg, sql = run_case(con, c, args.selftest)
        results[c["id"]] = {"status": status, "msg": msg, "sql": sql, "title": c["title"],
                            "solution": c.get("solution", "")}
        b = board.setdefault(c["feature"], [0, 0, 0]); b[0] += status == "PASS"; b[1] += 1; b[2] += status == "WARN"

    print(f"\n{'FEATURE':28} PASS / TOTAL")
    for f, (p, t, w) in board.items():
        print(f"{f:28} {p:>4} / {t:<4} {'(' + str(w) + ' warn)' if w else ''}")
    for cid, r in results.items():
        if r["status"] != "PASS":
            print(f"\n{r['status']:4} {cid} {r['title']}\n     {r['msg']}" + (f"\n     sql: {r['sql'][:200]}" if r["sql"] else ""))
            if r.get("solution"):
                print(f"     expected solution: {r['solution']}")

    if args.selftest:
        bad = [k for k, r in results.items() if r["status"] == "FAIL"]
        print(f"\nSelftest: {'OK' if not bad else 'BROKEN ' + ', '.join(bad)}")
        sys.exit(1 if bad else 0)

    os.makedirs(os.path.dirname(args.results) or ".", exist_ok=True)
    prev = json.load(open(args.results)) if os.path.exists(args.results) else {}
    regress = [k for k, r in results.items() if prev.get(k, {}).get("status") == "PASS" and r["status"] != "PASS"]
    fixed = [k for k, r in results.items() if r["status"] == "PASS" and prev.get(k, {}).get("status") not in (None, "PASS")]
    merged = {**prev, **results}
    json.dump(merged, open(args.results, "w"), indent=1)
    print(f"\nvs previous run: {len(fixed)} newly passing {fixed}\n                 {len(regress)} REGRESSED {regress}")
    sys.exit(1 if regress else 0)

if __name__ == "__main__":
    main()
