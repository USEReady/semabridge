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
from cases import CASES
from relationship_inventory import sb_column          # SemaBridge naming rules (uppercase, specials -> _, reserved -> COL_)
import adapter

def build_db(tables=TABLES):
    con = duckdb.connect()
    for t, df in tables.items():
        d = df.copy(); d.columns = [sb_column(c) for c in d.columns]
        con.register("_tmp", d)
        a = adapter.alias(t)
        con.execute(f'CREATE TABLE "{a}" AS SELECT * FROM _tmp')
        san = sb_column(t).upper()
        if san.casefold() != a.casefold():
            con.execute(f'CREATE VIEW "{san}" AS SELECT * FROM "{a}"')
        con.unregister("_tmp")
    return con

def from_clause(sql, tables=TABLES, fact=FACT, rels=RELATIONSHIPS):
    def _in_sql(t):
        a1, a2 = re.escape(adapter.alias(t)), re.escape(sb_column(t).upper())
        return bool(re.search(rf'(?<![\w"])(?:{a1}|{a2})"?\s*\.', sql, re.I))

    used = [t for t in tables if _in_sql(t)]
    use_upper = any(re.search(rf'(?<![\w"]){re.escape(sb_column(t).upper())}"?\s*\.', sql) for t in tables)
    def _name(t):
        return f'"{sb_column(t).upper()}"' if use_upper else f'"{adapter.alias(t)}"'

    if not used:
        return _name(fact), used
    if len(used) == 1:
        return _name(used[0]), used
    sql_from = _name(fact)
    for ft, fc, tt, tc in rels:
        if tt in used and tt != fact:
            sql_from += f' LEFT JOIN {_name(tt)} ON {_name(ft)}.{sb_column(fc)} = {_name(tt)}.{sb_column(tc)}'
    return sql_from, used

def evaluate(con, sql, tables=TABLES, fact=FACT, rels=RELATIONSHIPS):
    frm, used = from_clause(sql, tables, fact, rels)
    q = sqlglot.transpile(f"SELECT {sql} FROM {frm}", read="snowflake", write="duckdb")[0]
    v = con.execute(q).fetchone()[0]
    return (float(v) if v is not None else None), used

def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, float(b), rel_tol=1e-6, abs_tol=1e-9)

def run_case(con, c, selftest, tables=TABLES, fact=FACT, rels=RELATIONSHIPS, measures_tbl="Measures"):
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
            got, used = evaluate(con, sql, tables, fact, rels)
        except Exception as exc:
            return "FAIL", f"SQL does not run: {str(exc).splitlines()[0][:160]}", sql
        exp = float(c["expect"][1]())
        if measures_tbl in used:
            return "FAIL", "references the empty measure table instead of the table in the DAX", sql
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
    ap.add_argument("--mutate", type=int, help="seed for mutate.py table/column renaming")
    args = ap.parse_args()

    tables, fact, rels, cases_list, base, measures_tbl = TABLES, FACT, RELATIONSHIPS, CASES, None, "Measures"
    if args.mutate is not None:
        import mutate
        from cases import BASE
        tables, fact, rels, cases_list, base, measures_tbl, _ = mutate.apply(args.mutate, TABLES, FACT, RELATIONSHIPS, CASES, BASE)

    con = build_db(tables)
    cases = [c for c in cases_list if not args.feature or c["id"].split(".")[0] == args.feature.upper()]
    results, board = {}, OrderedDict()
    for c in cases:
        status, msg, sql = run_case(con, c, args.selftest, tables, fact, rels, measures_tbl)
        results[c["id"]] = {"status": status, "msg": msg, "sql": sql, "title": c["title"]}
        b = board.setdefault(c["feature"], [0, 0, 0]); b[0] += status == "PASS"; b[1] += 1; b[2] += status == "WARN"

    print(f"\n{'FEATURE':28} PASS / TOTAL")
    for f, (p, t, w) in board.items():
        print(f"{f:28} {p:>4} / {t:<4} {'(' + str(w) + ' warn)' if w else ''}")
    for cid, r in results.items():
        if r["status"] != "PASS":
            print(f"\n{r['status']:4} {cid} {r['title']}\n     {r['msg']}" + (f"\n     sql: {r['sql'][:200]}" if r["sql"] else ""))

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
