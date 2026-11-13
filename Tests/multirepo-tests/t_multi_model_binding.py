"""
t_multi_model_binding.py - generic live check for multi-model table binding.

Checks deployed semantic views (any models, any schema) for the problems seen when several
models deploy into one schema: binding to another model's same-name table, empty or
all-VARCHAR auto-created tables, relationships/columns that do not exist in the bound table,
broken enriched views and metrics NULLed for binding reasons.

No model, table or metric names are hard-coded. Everything is read from GET_DDL and
INFORMATION_SCHEMA.

Usage (PowerShell):
  $env:SNOWFLAKE_ACCOUNT="IJEBJJZ-USEREADY"; $env:SNOWFLAKE_USER="..."
  $env:SNOWFLAKE_PRIVATE_KEY_PATH="C:\\keys\\rsa_key.p8"   # or $env:SNOWFLAKE_PASSWORD
  $env:SNOWFLAKE_ROLE="SEMABRIDGE_DEVELOPER"; $env:SNOWFLAKE_WAREHOUSE="DI_OTH_POC_WH"
  python t_multi_model_binding.py --database SEMABRIDGE --schema TESTING_SEMABRIDGE `
      --views EMPLOYEE_HIRING_AND_HISTORY_SEMANTIC,HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC,ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC
  # optional: --expected expected.json  (reference values, see bottom of file)
  # optional: --all-views  (check every semantic view in the schema)

Exit code 1 if any FAIL.
"""
import argparse, json, os, re, sys
from collections import defaultdict

import snowflake.connector

IDENT = r'"?([A-Za-z_][A-Za-z0-9_$]*)"?'
BINDING_NULL_HINTS = ("does not support VARCHAR", "invalid identifier", "not emitted in DIMENSIONS",
                      "does not exist", "Enriched view creation failed")
results = []  # (status, view, check, detail)


def rec(status, view, check, detail=""):
    results.append((status, view, check, detail))


# ---------------------------------------------------------------- connection
def connect(db, schema):
    kw = dict(account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
              role=os.environ.get("SNOWFLAKE_ROLE"), warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
              database=db, schema=schema)
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key_path:
        from cryptography.hazmat.primitives import serialization
        pw = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        with open(key_path, "rb") as f:
            pk = serialization.load_pem_private_key(f.read(), password=pw.encode() if pw else None)
        kw["private_key"] = pk.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
    else:
        kw["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kw)


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


# ---------------------------------------------------------------- DDL parsing
def sections(ddl):
    """Split GET_DDL output into {section: [item lines]} using the one-tab section headers."""
    out, cur_sec = defaultdict(list), None
    for line in ddl.splitlines():
        m = re.match(r"^\t(tables|relationships|facts|dimensions|metrics)\s*\(", line, re.I)
        if m:
            cur_sec = m.group(1).lower()
            continue
        if cur_sec and line.startswith("\t\t"):
            out[cur_sec].append(line.strip().rstrip(","))
        elif cur_sec and line.strip().startswith(")"):
            cur_sec = None
    return out


def parse_tables(lines):
    """alias -> fully qualified physical name."""
    tabs = {}
    for ln in lines:
        m = re.match(r'^(?:' + IDENT + r'\s+as\s+)?([A-Za-z0-9_$".]+?)(?:\s+primary key|\s+unique|\s+comment|$)', ln, re.I)
        if not m:
            continue
        alias, phys = m.group(1), m.group(2).replace('"', "")
        if not alias:
            alias = phys.split(".")[-1]
        tabs[alias.upper()] = phys.upper()
    return tabs


def item_name(ln):
    m = re.match(r'^(?:PRIVATE\s+)?' + IDENT + r'\.' + IDENT + r'\s+as\s+', ln, re.I)
    return (m.group(1).upper(), m.group(2).upper()) if m else (None, None)


def item_expr(ln):
    m = re.match(r'^(?:PRIVATE\s+)?\S+\s+as\s+(.*?)(?:\s+with synonyms.*|\s+comment\s*=.*)?$', ln, re.I | re.S)
    return m.group(1) if m else ""


def item_comment(ln):
    m = re.search(r"comment\s*=\s*'(.*)'$", ln, re.I | re.S)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- checks
def check_view(cur, db, schema, view, expected):
    try:
        ddl = q(cur, f"SELECT GET_DDL('SEMANTIC_VIEW', '{db}.{schema}.{view}')")[0][0]
    except Exception as e:
        rec("FAIL", view, "C1 view exists", str(e)[:200]); return
    rec("PASS", view, "C1 view exists")
    sec = sections(ddl)
    tabs = parse_tables(sec["tables"])
    if not tabs:
        rec("FAIL", view, "C2 parse TABLES", "no tables parsed"); return

    # physical columns per alias
    cols, types = {}, {}
    for alias, phys in tabs.items():
        p_db, p_sch, p_tab = (phys.split(".") + ["", "", ""])[-3:]
        rows = q(cur, f'SELECT column_name, data_type, character_maximum_length FROM "{p_db}".information_schema.columns '
                      "WHERE table_schema=%s AND table_name=%s", (p_sch, p_tab))
        if not rows:
            rec("FAIL", view, "C2 physical table exists", f"{alias} -> {phys} not found"); continue
        cols[alias] = {r[0].upper() for r in rows}
        types[alias] = rows
    rec("PASS" if len(cols) == len(tabs) else "FAIL", view, "C2 physical tables exist",
        f"{len(cols)}/{len(tabs)} bound tables found")

    # C3 data present, C4 not an auto-created all-VARCHAR table
    for alias, phys in tabs.items():
        if alias not in cols:
            continue
        n = q(cur, f"SELECT COUNT(*) FROM {phys}")[0][0]
        rec("PASS" if n > 0 else "FAIL", view, "C3 table has rows", f"{alias} -> {phys}: {n} rows")
        rows = types[alias]
        if rows and all(r[1] == "TEXT" for r in rows) and len(rows) > 1:
            rec("FAIL", view, "C4 real column types", f"{alias} -> {phys}: all {len(rows)} columns are VARCHAR (auto-created?)")

    dims = [item_name(l) for l in sec["dimensions"]] + [item_name(l) for l in sec["facts"]]
    mets = [item_name(l) for l in sec["metrics"]]
    logical = {(a, n) for a, n in dims + mets if a}

    # C5 relationships: aliases defined, key columns exist in the bound tables
    bad = []
    for ln in sec["relationships"]:
        m = re.search(r'as\s+' + IDENT + r'\s*\(([^)]*)\)\s+references\s+' + IDENT + r'\s*\(([^)]*)\)', ln, re.I)
        if not m:
            continue
        for alias, colstr in ((m.group(1).upper(), m.group(2)), (m.group(3).upper(), m.group(4))):
            if alias not in tabs:
                bad.append(f"{alias} not in TABLES"); continue
            for c in [c.strip().strip('"').upper() for c in colstr.split(",")]:
                if alias in cols and c not in cols[alias]:
                    bad.append(f"{alias}.{c} missing in {tabs[alias]}")
    rec("FAIL" if bad else "PASS", view, "C5 relationships resolve", "; ".join(bad[:10]))

    # C6 every physical column reference in dims/facts/metrics exists in the bound table
    bad = []
    for ln in sec["dimensions"] + sec["facts"] + sec["metrics"]:
        for a, c in re.findall(r'\b' + IDENT + r'\."?([A-Za-z_][A-Za-z0-9_$]*)"?', item_expr(ln)):
            a, c = a.upper(), c.upper()
            if a in tabs and (a, c) not in logical and a in cols and c not in cols[a]:
                bad.append(f"{a}.{c} not in {tabs[a]}")
    bad = sorted(set(bad))
    rec("FAIL" if bad else "PASS", view, "C6 column refs exist in bound tables", "; ".join(bad[:10]))

    # C7 enriched views read from an existing base table with data
    for alias, phys in tabs.items():
        if not phys.endswith("_ENRICHED"):
            continue
        try:
            vddl = q(cur, f"SELECT GET_DDL('VIEW', '{phys}')")[0][0]
            base = re.search(r'FROM\s+([A-Za-z0-9_$".]+)\s+f\b', vddl, re.I)
            base = base.group(1).replace('"', "") if base else "?"
            n = q(cur, f"SELECT COUNT(*) FROM {base}")[0][0] if base != "?" else 0
            rec("PASS" if n > 0 else "FAIL", view, "C7 enriched view base", f"{phys} FROM {base}: {n} rows")
        except Exception as e:
            rec("FAIL", view, "C7 enriched view base", f"{phys}: {str(e)[:150]}")

    # C8 metrics NULLed for binding-related reasons
    nulls, binding_nulls = [], []
    for ln in sec["metrics"]:
        a, n = item_name(ln)
        if re.search(r"CAST\(NULL AS DOUBLE\)", item_expr(ln), re.I):
            reason = item_comment(ln)
            nulls.append(f"{a}.{n}")
            if any(h.lower() in reason.lower() for h in BINDING_NULL_HINTS):
                binding_nulls.append(f"{a}.{n}: {reason[:120]}")
    rec("FAIL" if binding_nulls else "PASS", view, "C8 no binding-caused NULL metrics", " | ".join(binding_nulls[:8]))
    if nulls:
        rec("INFO", view, "C8 all NULL metrics", f"{len(nulls)}: " + ", ".join(nulls[:15]))

    # C9 smoke query: each public, non-window, non-NULL metric runs on its own
    errs, ok = [], 0
    for ln in sec["metrics"]:
        if ln.upper().startswith("PRIVATE") or " OVER " in ln.upper() or "CAST(NULL AS DOUBLE)" in ln.upper():
            continue
        a, n = item_name(ln)
        if not a:
            continue
        try:
            q(cur, f"SELECT * FROM SEMANTIC_VIEW({db}.{schema}.{view} METRICS {a}.{n})")
            ok += 1
        except Exception as e:
            errs.append(f"{a}.{n}: {str(e)[:100]}")
    rec("FAIL" if errs else "PASS", view, "C9 metrics query", f"{ok} ok; " + " | ".join(errs[:5]))

    # C10 optional reference values
    for case in expected.get(view, []):
        metric, dims_, rows_exp, tol = case["metric"], case.get("dimensions", []), case["rows"], case.get("tol", 1e-6)
        dim_sql = f" DIMENSIONS {', '.join(dims_)}" if dims_ else ""
        try:
            got = q(cur, f"SELECT * FROM SEMANTIC_VIEW({db}.{schema}.{view}{dim_sql} METRICS {metric})")
            got_map = {tuple(str(v) for v in r[:-1]): r[-1] for r in got}
            miss = []
            for exp in rows_exp:
                key, val = tuple(str(v) for v in exp[:-1]), exp[-1]
                g = got_map.get(key)
                if g is None or abs(float(g) - float(val)) > tol:
                    miss.append(f"{key}: got {g}, expected {val}")
            rec("FAIL" if miss else "PASS", view, f"C10 values {metric}", "; ".join(miss[:5]))
        except Exception as e:
            rec("FAIL", view, f"C10 values {metric}", str(e)[:200])
    return tabs


def check_cross_view(view_tabs):
    """C11 a physical table shared by several views must be used with the same columns by all of them
    (C6 already proves each view's columns exist); here we just report sharing so it is visible."""
    by_phys = defaultdict(list)
    for view, tabs in view_tabs.items():
        for alias, phys in (tabs or {}).items():
            by_phys[phys].append(f"{view}:{alias}")
    for phys, users in by_phys.items():
        if len({u.split(':')[0] for u in users}) > 1:
            rec("INFO", "*", "C11 shared physical table", f"{phys} used by {', '.join(users)}")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--views", default="")
    ap.add_argument("--all-views", action="store_true")
    ap.add_argument("--expected", default="")
    a = ap.parse_args()

    con = connect(a.database, a.schema)
    cur = con.cursor()
    views = [v.strip().upper() for v in a.views.split(",") if v.strip()]
    if a.all_views:
        views = [r[1] for r in q(cur, f'SHOW SEMANTIC VIEWS IN SCHEMA "{a.database}"."{a.schema}"')]
    if not views:
        sys.exit("No views given (use --views or --all-views)")
    expected = json.load(open(a.expected)) if a.expected else {}

    view_tabs = {v: check_view(cur, a.database, a.schema, v, expected) for v in views}
    check_cross_view(view_tabs)

    width = max(len(r[2]) for r in results)
    for st, v, chk, det in results:
        print(f"[{st:4}] {v:45} {chk:{width}}  {det}")
    fails = sum(1 for r in results if r[0] == "FAIL")
    passes = sum(1 for r in results if r[0] == "PASS")
    print(f"\nSUMMARY: {passes} pass, {fails} fail, {len(views)} views")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

# expected.json example (optional, values from Power BI / PBIX):
# {
#   "ARTIFICIAL_INTELLIGENCE_SAMPLE_SEMANTIC": [
#     {"metric": "CASES.CASE_COUNT", "rows": [[10000]]},
#     {"metric": "CASES.CASE_COUNT", "dimensions": ["CASES.AGENT"], "rows": [["Sanjay Shah", 701], ["Molly Clark", 1451]]}
#   ],
#   "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC": [
#     {"metric": "EMPLOYEE.ACTIVES", "rows": [[32401]]}
#   ]
# }
