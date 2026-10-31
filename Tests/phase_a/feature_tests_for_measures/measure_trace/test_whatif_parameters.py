#!/usr/bin/env python3
"""
test_whatif_parameters.py - does SemaBridge resolve What-if (parameter) measures correctly on EVERY path?

Rule under test (generic):
  A PARAMETER measure is  SELECTEDVALUE(T[c], default)  where table T has NO relationships.
  * the parameter measure itself      -> IFF(COUNT(DISTINCT c) + MAX(IFF(c IS NULL,1,0)) = 1, MAX(c), default)
  * used inside ANY other measure      -> replaced by (default); the SQL must NOT reference table T at all
  * SELECTEDVALUE on a RELATED table   -> NOT a parameter: must not be replaced by a constant
  * a disconnected table that is NOT read through SELECTEDVALUE -> not a parameter: no guessed default

MODES
  synthetic (default)  12 cases on a small synthetic model, one per translation path / edge case:
                       simple arithmetic (regex/rule path), 'Table'[Measure] syntax, VAR + CALCULATE (AST path),
                       measure-of-measure chain (dependency path), DIVIDE, non-zero default, two parameters,
                       parameter without default, related-table SELECTEDVALUE, disconnected non-parameter table.
                       Each case: SQL produced by SemaBridge, structural check (no parameter-table reference),
                       value on the data (DuckDB) vs an independently computed expected value.
      uv run python test_whatif_parameters.py --repo .
      uv run python test_whatif_parameters.py --selftest          # proves cases + expected SQL + data agree
  model                Any real model: finds its parameter tables generically (no relationships + SELECTEDVALUE
                       with a default) and checks every OTHER metric never references them.
      uv run python test_whatif_parameters.py model --pbix "<model>.pbix" --ddl "<view ddl>.sql"
      uv run python test_whatif_parameters.py model --pbix "<model>.pbix" --repo .      # SML stage (pipeline)

Note: the LLM tier (tier 5) is not exercised - all runs use skip_tier5=True (deterministic paths only).
needs: pip install duckdb sqlglot pandas pbixray
"""
import argparse, math, os, re, sys, warnings
from types import SimpleNamespace
warnings.filterwarnings("ignore")
import logging
logging.getLogger("sqlglot").setLevel(logging.ERROR)

_RES = {"DATE", "VALUE", "YEAR", "MONTH", "DAY", "ORDER", "GROUP", "TABLE", "USER", "TIME", "NUMBER", "COLUMN", "ROW", "START", "CURRENT"}
_REP = {"%": "PCT", "$": "DOL", "&": "AND", "#": "NUM", "@": "AT"}


def sanitize(name, reserved=True):
    try:
        from semabridge.utils.identifiers import IdentifierSanitizer
        return IdentifierSanitizer(force_uppercase=True, suppress_reserved=reserved).sanitize_column(name)
    except Exception:
        s = "".join(_REP.get(c, c) for c in str(name))
        s = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", s)).strip("_").upper()
        return "COL_" + s if reserved and s in _RES else s


def alias(t):
    try:
        from semabridge.utils.naming import to_alias
        return to_alias(t)
    except Exception:
        return re.sub(r"[^A-Za-z0-9_]", "_", str(t)).lower()


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def table_variants(t):
    return {norm(v) for v in (t, alias(t), sanitize(t, False), sanitize(t))}


def references_table(sql, table):
    """True if the SQL references `table` under any naming form (to_alias, sanitizer, plain)."""
    want = table_variants(table)
    return any(norm(m.group(1)) in want for m in re.finditer(r'("?[A-Za-z_][\w$]*"?)\s*\.\s*"?[A-Za-z_]', re.sub(r"'(?:[^']|'')*'", "''", sql or "")))


EXACT_SV = re.compile(r"IFF\s*\(\s*COUNT\s*\(\s*DISTINCT\s+[^)]+\)\s*\+\s*MAX\s*\(\s*IFF\s*\([^)]*IS\s+NULL", re.I)


# ============================================================================ synthetic model
def fixture():
    import pandas as pd
    T = {
        "Orders": pd.DataFrame({"OrderID": range(1, 9), "ProductID": [1, 1, 2, 2, 3, 3, 1, 2],
                                "Status": ["Sold", "Sold", "Open", "Sold", "Open", "Sold", "Sold", "Open"],
                                "Amount": [100.0, 250.0, 80.0, 40.0, 60.0, 300.0, 20.0, 150.0],
                                "Qty": [1, 3, 2, 1, 4, 5, 1, 2]}),
        "Product": pd.DataFrame({"ProductID": [1, 2, 3], "Category": ["Hardware", "Software", "Software"]}),
        "Forecast Adjustment": pd.DataFrame({"Forecast Adjustment": [-20, -10, 0, 10, 20]}),   # parameter, default 0
        "Growth Rate": pd.DataFrame({"Growth Rate": [5, 10, 15]}),                             # parameter, default 5
        "Legend": pd.DataFrame({"Metric": ["A", "B"], "Bubble": [1.0, 2.0]}),                  # disconnected, NOT a parameter
    }
    rels = [("Orders", "ProductID", "Product", "ProductID")]
    return T, rels


def cases(T):
    O = T["Orders"]
    total, sold = O.Amount.sum(), O[O.Status == "Sold"].Amount.sum()
    P = {"Forecast Adjustment Value": ("Forecast Adjustment", "SELECTEDVALUE('Forecast Adjustment'[Forecast Adjustment], 0)"),
         "Growth Rate Value": ("Growth Rate", "SELECTEDVALUE('Growth Rate'[Growth Rate], 5)"),
         "Adj No Default": ("Forecast Adjustment", "SELECTEDVALUE('Forecast Adjustment'[Forecast Adjustment])")}
    base = {"Sold Amount": ("Orders", 'CALCULATE(SUM(Orders[Amount]), Orders[Status] = "Sold")'),
            "Adjusted Amount": ("Orders", "SUM(Orders[Amount]) * (1 + [Forecast Adjustment Value] / 100)")}
    c = lambda **k: k
    return [
        c(id="W1", path="parameter measure itself", home="Forecast Adjustment", dax=P["Forecast Adjustment Value"][1],
          expect="value", value=0, must_be_exact_sv=True,
          sql="IFF(COUNT(DISTINCT forecast_adjustment.FORECAST_ADJUSTMENT) + MAX(IFF(forecast_adjustment.FORECAST_ADJUSTMENT IS NULL, 1, 0)) = 1, MAX(forecast_adjustment.FORECAST_ADJUSTMENT), 0)"),
        c(id="W2", path="simple arithmetic, [Measure] (rule/regex path)", home="Orders",
          dax="SUM(Orders[Amount]) * (1 + [Forecast Adjustment Value] / 100)", ctx=P, expect="value", value=total,
          sql="SUM(orders.AMOUNT) * (1 + (0) / 100)"),
        c(id="W3", path="'Table'[Measure] syntax", home="Orders",
          dax="SUM(Orders[Amount]) * (1 + 'Forecast Adjustment'[Forecast Adjustment Value] / 100)", ctx=P,
          expect="value", value=total, sql="SUM(orders.AMOUNT) * (1 + (0) / 100)"),
        c(id="W4", path="VAR + CALCULATE (AST path) - the Revenue In Pipeline shape", home="Orders",
          dax='VAR r = CALCULATE(SUM(Orders[Amount]), Orders[Status] = "Sold") '
              "RETURN r + (r * ('Forecast Adjustment'[Forecast Adjustment Value] / 100))", ctx=P,
          expect="value", value=sold,
          sql="(SUM(CASE WHEN orders.STATUS = 'Sold' THEN orders.AMOUNT END)) + ((SUM(CASE WHEN orders.STATUS = 'Sold' THEN orders.AMOUNT END)) * ((0) / 100))"),
        c(id="W5", path="measure-of-measure chain (dependency path) - the Forecast shape", home="Orders",
          dax="[Sold Amount] + [Adjusted Amount]", ctx={**P, **base}, expect="value", value=sold + total,
          sql="SUM(CASE WHEN orders.STATUS = 'Sold' THEN orders.AMOUNT END) + SUM(orders.AMOUNT) * (1 + (0) / 100)"),
        c(id="W6", path="parameter inside DIVIDE", home="Orders",
          dax="SUM(Orders[Amount]) + DIVIDE(SUM(Orders[Amount]) * [Forecast Adjustment Value], 100, 0)", ctx=P,
          expect="value", value=total, sql="SUM(orders.AMOUNT) + (SUM(orders.AMOUNT) * (0)) / 100"),
        c(id="W7", path="non-zero default (must use 5, not 0)", home="Orders",
          dax="SUM(Orders[Amount]) * [Growth Rate Value] / 100", ctx=P, expect="value", value=total * 5 / 100,
          sql="SUM(orders.AMOUNT) * (5) / 100"),
        c(id="W8", path="two parameters in one measure", home="Orders",
          dax="SUM(Orders[Amount]) * (1 + [Forecast Adjustment Value] / 100) * (1 + [Growth Rate Value] / 100)", ctx=P,
          expect="value", value=total * 1.05, sql="SUM(orders.AMOUNT) * (1 + (0) / 100) * (1 + (5) / 100)"),
        c(id="W9", path="parameter WITHOUT default (DAX: BLANK acts as 0 in arithmetic)", home="Orders",
          dax="SUM(Orders[Amount]) * (1 + [Adj No Default] / 100)", ctx=P, expect="value_or_na", value=total,
          sql="SUM(orders.AMOUNT) * (1 + COALESCE(NULL, 0) / 100)"),
        c(id="W10", path="SELECTEDVALUE on a RELATED table is not a parameter", home="Orders",
          dax='IF(SELECTEDVALUE(Product[Category], "All") = "Hardware", SUM(Orders[Qty]), SUM(Orders[Amount]))',
          expect="value_or_na", value=total, must_reference="Product",
          sql="IFF(IFF(COUNT(DISTINCT product.CATEGORY) + MAX(IFF(product.CATEGORY IS NULL, 1, 0)) = 1, MAX(product.CATEGORY), 'All') = 'Hardware', SUM(orders.QTY), SUM(orders.AMOUNT))"),
        c(id="W11", path="disconnected table that is NOT a parameter (no SELECTEDVALUE)", home="Orders",
          dax="SUM(Orders[Amount]) * MAX(Legend[Bubble])", expect="no_sql_or_na"),
        c(id="W12", path="parameter used twice + inside IF", home="Orders",
          dax="IF([Forecast Adjustment Value] > 0, SUM(Orders[Amount]) * (1 + [Forecast Adjustment Value] / 100), SUM(Orders[Amount]))",
          ctx=P, expect="value", value=total, sql="IFF((0) > 0, SUM(orders.AMOUNT) * (1 + (0) / 100), SUM(orders.AMOUNT))"),
        c(id="W13", path="parameter WITHOUT default multiplied directly (must be BLANK)", home="Orders",
          dax="SUM(Orders[Amount]) * [Adj No Default]", ctx=P, expect="value_or_na", value=None,
          sql="SUM(orders.AMOUNT) * NULL"),
    ]


# ============================================================================ SemaBridge call
def translate(dax, home, name, context=None, _memo={}):
    from semabridge.converter.dax_translator import DAXTranslator
    t = _memo.setdefault("t", DAXTranslator())
    ctx = []
    for cname, (chome, cdax) in (context or {}).items():
        sub = {k: v for k, v in (context or {}).items() if k != cname}
        cr = translate(cdax, chome, cname, sub)
        ctx.append(SimpleNamespace(unique_name=cname, name=cname, label=cname, expression=cdax, dataset=chome,
                                   sql_expression=cr["sql"], tier=1, sync_enabled=cr["ok"]))
    try:
        try:
            res = t.translate(dax, alias(home), home, metric_name=name, skip_tier5=True, metrics_context=ctx)
        except TypeError:
            res = t.translate(dax, alias(home), home, metric_name=name, skip_tier5=True)
    except Exception as exc:
        return {"ok": False, "sql": None, "reason": f"{type(exc).__name__}: {exc}"}
    sql = getattr(res, "sql", None)
    ok = bool(getattr(res, "is_success", False) and sql and not re.fullmatch(r"\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?", sql, re.I))
    reason = " ".join(str(getattr(res, a, "") or "") for a in ("failure_reason", "reason")).strip()
    return {"ok": ok, "sql": sql if ok else None, "reason": reason}


# ============================================================================ DuckDB evaluation
class DB:
    def __init__(self, T, rels):
        import duckdb
        self.con, self.T, self.rels, self.var = duckdb.connect(), T, rels, {}
        for t, df in T.items():
            d = df.copy(); d.columns = [sanitize(c) for c in d.columns]
            dn = sanitize(t, False)
            self.con.register("_t", d); self.con.execute(f'CREATE TABLE "{dn}" AS SELECT * FROM _t'); self.con.unregister("_t")
            for v in table_variants(t):
                self.var[v] = dn

    def evaluate(self, sql, home):
        import sqlglot
        sql = re.sub(r'(?<![\w."])("?[A-Za-z_][\w$]*"?)(\s*\.)',
                     lambda m: f'"{self.var[norm(m.group(1))]}"{m.group(2)}' if norm(m.group(1)) in self.var else m.group(0), sql)
        base = sanitize(home, False)
        used = {dn for dn in set(self.var.values()) if re.search(rf'"{re.escape(dn)}"\s*\.', sql)}
        frm = f'"{base}"'
        for ft, fc, tt, tc in self.rels:
            a, b = sanitize(ft, False), sanitize(tt, False)
            if a == base and b in used:
                frm += f' LEFT JOIN "{b}" ON "{a}"."{sanitize(fc)}" = "{b}"."{sanitize(tc)}"'
        q = sqlglot.transpile(f"SELECT {sql} FROM {frm}", read="snowflake", write="duckdb")[0]
        v = self.con.execute(q).fetchone()[0]
        return None if v is None else float(v)


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def synthetic(args):
    T, rels = fixture()
    params = {"Forecast Adjustment", "Growth Rate"}
    db = DB(T, rels)
    results = []
    for c in cases(T):
        if args.case and c["id"] not in args.case:
            continue
        if args.selftest:
            if not c.get("sql"):
                results.append((c, "SKIP", "no expected SQL for this kind", None)); continue
            r = {"ok": True, "sql": c["sql"], "reason": ""}
        else:
            r = translate(c["dax"], c["home"], c["id"], c.get("ctx"))
        sql = r["sql"]
        status, msg = "PASS", ""
        if c["expect"] == "no_sql_or_na":
            if r["ok"]:
                status, msg = "FAIL", "translated although it reads an unrelated, non-parameter table (no default exists)"
                if any(references_table(sql, p) for p in params) or not references_table(sql, "Legend"):
                    msg += " - and the table was replaced/guessed"
            else:
                msg = f"not translated ({r['reason'][:70]})"
        elif not r["ok"]:
            if c["expect"] == "value_or_na":
                msg = f"not translated (acceptable): {r['reason'][:70]}"
            else:
                status, msg = "FAIL", f"not translated: {r['reason'][:120]}"
        else:
            leaked = [p for p in params if references_table(sql, p) and not references_table(c["home"], p) and c["home"] != p]
            if leaked:
                status, msg = "FAIL", f"references parameter table {leaked} - must use the default constant"
            elif c.get("must_be_exact_sv") and not EXACT_SV.search(sql):
                status, msg = "FAIL", "parameter measure itself must be IFF(COUNT(DISTINCT c) + MAX(IFF(c IS NULL,1,0)) = 1, MAX(c), default)"
            elif re.search(r"\bANY_VALUE\s*\(", sql, re.I):
                status, msg = "FAIL", "ANY_VALUE returns an arbitrary slider value"
            elif c.get("must_reference") and not references_table(sql, c["must_reference"]):
                status, msg = "FAIL", f"SELECTEDVALUE on related table {c['must_reference']} was replaced by a constant"
            else:
                try:
                    got = db.evaluate(sql, c["home"])
                    status, msg = ("PASS", f"{got} == {c['value']}") if close(got, c["value"]) else ("FAIL", f"value {got}, expected {c['value']}")
                except Exception as exc:
                    status, msg = "FAIL", "SQL does not run: " + str(exc).splitlines()[0][:120]
        results.append((c, status, msg, sql))

    print(f"\n{'CASE':5} {'RESULT':6} PATH")
    for c, st, msg, sql in results:
        print(f"{c['id']:5} {st:6} {c['path']}")
        if st != "PASS":
            print(f"{'':12} {msg}")
            if sql:
                print(f"{'':12} sql: {sql[:200]}")
    bad = [c["id"] for c, st, _, _ in results if st == "FAIL"]
    print(f"\n{'SELFTEST ' if args.selftest else ''}{len(results) - len(bad)} / {len(results)} pass" + (f"   FAILED: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


# ============================================================================ real model
def model(args):
    from pbixray import PBIXRay
    px = PBIXRay(args.pbix)
    related = set()
    for r in px.relationships.itertuples():
        related.update((r.FromTableName, r.ToTableName))
    tables = set(px.schema.TableName)
    if hasattr(px, "dax_tables"):
        try:
            tables.update(px.dax_tables["TableName"] if hasattr(px.dax_tables, "columns") and "TableName" in px.dax_tables.columns else px.dax_tables)
        except Exception:
            pass
    if hasattr(px, "tables"):
        try:
            tables.update(px.tables)
        except Exception:
            pass
    measures = px.dax_measures
    if hasattr(measures, "columns") and "TableName" in measures.columns:
        tables.update(measures["TableName"].dropna())
    sv = re.compile(r"^\s*SELECTEDVALUE\s*\(\s*(?:'([^']+)'|(\w[\w ]*))\s*\[([^\]]+)\]\s*(?:,\s*(.+?))?\s*\)\s*$", re.I | re.S)
    params, param_measures = {}, {}
    for _, m in measures.iterrows():
        mm = sv.match(str(m.get("Expression") or ""))
        if mm:
            t = (mm.group(1) or mm.group(2)).strip()
            if t in tables and t not in related:
                params[t] = (mm.group(4) or "BLANK").strip()
                param_measures[m["Name"]] = (t, params[t])
    if not params:
        print("No parameter tables found (no table without relationships read through SELECTEDVALUE).")
        return 0
    print("Parameter tables:  " + ", ".join(f"{t} (default {d})" for t, d in params.items()))
    print("Parameter measures: " + ", ".join(param_measures))
    users = [m for _, m in measures.iterrows()
             if m["Name"] not in param_measures and any(re.search(rf"\[{re.escape(p)}\]", str(m.get("Expression") or "")) for p in param_measures)]
    print(f"Measures using a parameter: {', '.join(u['Name'] for u in users) or 'none'}\n")

    sqls = {}
    if args.ddl:
        text = open(args.ddl, encoding="utf-8").read()
        blk = re.search(r"\bmetrics\s*\((.*)\)\s*;?\s*$", text, re.I | re.S)
        for line in re.split(r",\s*\n", blk.group(1) if blk else ""):
            mm = re.match(r"\s*(\"?\w+\"?)\.\"?(\w+)\"?\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$", line, re.I | re.S)
            if mm:
                sqls[norm(mm.group(2))] = (mm.group(1).strip('"'), " ".join(mm.group(3).split()))
        source = "DDL"
    else:
        sys.path.insert(0, os.path.join(os.path.abspath(args.repo), "src"))
        from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
        tm = LocalPBIXConnector({"pbix_path": args.pbix}).discover().get("raw_tmsl")
        osi = TMSLToOSIConverter().to_osi({"tmsl": tm, "dataset_id": os.path.basename(args.pbix)})
        for m in OSIToSMLConverter().from_osi(osi).metrics:
            sqls[norm(sanitize(getattr(m, "unique_name", ""), False))] = (getattr(m, "dataset", ""), getattr(m, "sql_expression", None) or "")
        source = "SML"

    fails = 0
    print(f"{'MEASURE':34} {'STATUS':6} DETAIL   (source: {source})")
    for _, m in measures.iterrows():
        key = norm(sanitize(m["Name"], False))
        if key not in sqls:
            continue
        table, sql = sqls[key]
        is_param = m["Name"] in param_measures
        is_user = any(m["Name"] == u["Name"] for u in users)
        if not (is_param or is_user):
            leaked = [p for p in params if references_table(sql, p)]
            if leaked:
                fails += 1
                print(f"{m['Name'][:34]:34} FAIL   references parameter table {leaked}")
            continue
        null = bool(re.fullmatch(r"\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?", sql or "", re.I))
        if is_param:
            ok = null or EXACT_SV.search(sql or "")
            print(f"{m['Name'][:34]:34} {'PASS' if ok else 'FAIL':6} parameter measure: {'exact SELECTEDVALUE' if EXACT_SV.search(sql or '') else ('NULL' if null else sql[:90])}")
            fails += 0 if ok else 1
        else:
            leaked = [p for p in params if references_table(sql, p)]
            defaults = {d for t, d in params.items()}
            uses_default = any(re.search(rf"\(\s*{re.escape(d)}\s*\)", sql or "") for d in defaults)
            if null:
                st, det = "FAIL", "NULL - should translate with the parameter default"
            elif leaked:
                st, det = "FAIL", f"references parameter table {leaked}"
            else:
                st, det = "PASS", "default constant used" if uses_default else "no parameter-table reference"
            fails += st == "FAIL"
            print(f"{m['Name'][:34]:34} {st:6} {det}")
    print(f"\n{'OK' if not fails else str(fails) + ' problem(s)'}")
    return 1 if fails else 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "model":
        ap = argparse.ArgumentParser(prog="test_whatif_parameters.py model")
        ap.add_argument("model"); ap.add_argument("--pbix", required=True)
        ap.add_argument("--ddl"); ap.add_argument("--repo", default=".")
        sys.exit(model(ap.parse_args()))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="."); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--case", action="append", help="run only these case ids (W1..W12)")
    args = ap.parse_args()
    sys.path.insert(0, os.path.join(os.path.abspath(args.repo), "src"))
    sys.exit(synthetic(args))


if __name__ == "__main__":
    main()
