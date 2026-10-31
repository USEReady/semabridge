#!/usr/bin/env python3
"""
test_filter_plan.py - tests the GENERIC filter-plan solution (one analyzer for every CALCULATE with ALL/REMOVEFILTERS/
ALLEXCEPT/ALLSELECTED/ALLNOBLANKROW), on any model. Read-only.

A reference planner (independent of SemaBridge) reads each measure's DAX and decides:
  base      SUM | COUNT | MIN | MAX | AVERAGE | RATIO | DISTINCT | NONADD | OTHER   (measure refs inlined)
  removed   column T[c] (incl. T[Date].[Level]) | table T | allexcept T | own-fact | allselected
  kept      fixed filters next to ALL
  route     PLAIN | WINDOW | PRECOMPUTE | PRECOMPUTE_LOO (leave-one-out) | NULL
  parts     number of window terms the SQL must contain (AVERAGE / RATIO -> 2)

MODES
  plan      python test_filter_plan.py plan --pbix <m>.pbix
            -> table of every filter-removing measure with its expected plan (diagnosis, no SemaBridge needed)
  ddl       python test_filter_plan.py ddl --pbix <m>.pbix --ddl <view>.sql   (repeatable pairs)
            -> compares the emitted metric with the plan:
               route matches; WINDOW: number of windows = parts, window args are metric refs, EXCLUDING names are
               declared non-key dimensions of the removed table/column, no AVG(..)/ratio inside a window, ::FLOAT on
               ratio parts, COMMENT 'queried with'; PRECOMPUTE: no OVER in the metric; NULL: has a reason COMMENT
  cases     python test_filter_plan.py cases [--repo .]
            -> 24 synthetic DAX shapes through SemaBridge's AST translator (try_ast_translate); checks route and shape.
               Without --repo / SemaBridge: --selftest verifies the cases against reference SQL.
Exit code 1 on FAIL (WARN never fails).
"""
import argparse, os, re, sys, warnings
from types import SimpleNamespace
from collections import defaultdict
warnings.filterwarnings("ignore")

MOD_RE = re.compile(r"^\s*(ALLSELECTED|ALLEXCEPT|ALLNOBLANKROW|REMOVEFILTERS|ALL)\s*\(", re.I)
_REP = {"%": "PCT", "$": "DOL", "&": "AND", "#": "NUM"}


def norm(s):
    s = "".join(_REP.get(c, c) for c in str(s)).upper()
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"^COL_", "", s))


def split_args(s):
    out, depth, cur, q = [], 0, [], None
    for ch in s:
        if q:
            cur.append(ch); q = None if ch == q else q; continue
        if ch in "\"'":
            q = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip()); cur = []; continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def calls(expr, fname):
    """yield (start, args_text) for every fname( ... ) with balanced parens"""
    for m in re.finditer(rf"\b{fname}\s*\(", expr, re.I):
        depth, i = 1, m.end()
        while i < len(expr) and depth:
            depth += {"(": 1, ")": -1}.get(expr[i], 0); i += 1
        yield m.start(), expr[m.end():i - 1]


def inline(expr, measures, depth=0):
    if depth > 4:
        return expr
    return re.sub(r"(?<![\w'\]])\[([^\]]+)\]",
                  lambda m: "(" + inline(measures[m.group(1)], measures, depth + 1) + ")" if m.group(1) in measures else m.group(0), expr)


TBL = r"(?:'([^']+)'|([A-Za-z_][\w ]*?))"


def base_kind(text):
    t = text.upper()
    if re.search(r"\bDISTINCTCOUNT(NOBLANK)?\s*\(|\bMEDIANX?\s*\(|\bPERCENTILE", t):
        return "DISTINCT"
    if re.search(r"\bLASTDATE|\bLASTNONBLANK|\bCLOSINGBALANCE|=\s*MAX\s*\(", t):
        return "NONADD"
    if re.search(r"\bDIVIDE\s*\(", t) or re.search(r"[^/]/[^/]", re.sub(r"'[^']*'|\[[^\]]*\]|\"[^\"]*\"", "", t)):
        return "RATIO"
    for k, rx in (("AVERAGE", r"\bAVERAGEX?\s*\("), ("MIN", r"\bMINX?\s*\("), ("MAX", r"\bMAXX?\s*\("),
                  ("COUNT", r"\bCOUNT(ROWS|A|X|AX)?\s*\("), ("SUM", r"\bSUMX?\s*\(")):
        if re.search(rx, t):
            return k
    return "OTHER"


def fact_tables(text):
    return {norm(a or b) for a, b in re.findall(rf"\b(?:SUM|SUMX|AVERAGE|AVERAGEX|COUNT|COUNTA|COUNTROWS|COUNTX|MIN|MAX|DISTINCTCOUNT)\s*\(\s*{TBL}\s*[\[),]", text, re.I)}


def plan_for(expr, measures):
    """reference plan for one measure (first CALCULATE with a filter-removal modifier decides)"""
    full = inline(expr, measures)
    plans = []
    for _, body in calls(full, "CALCULATE"):
        args = split_args(body)
        if len(args) < 2:
            continue
        mods = [a for a in args[1:] if MOD_RE.match(a)]
        kept = [a for a in args[1:] if not MOD_RE.match(a)]
        rowctx = [a for a in kept if re.match(r"\s*FILTER\s*\(\s*ALL", a, re.I)]
        if not mods and not rowctx:
            continue
        base = args[0]
        facts = fact_tables(base)
        removed = []
        for m in mods + [re.search(r"FILTER\s*\(\s*(ALL\w*\s*\(.*)", r, re.I | re.S).group(1) for r in rowctx]:
            fn = MOD_RE.match(m).group(1).upper()
            inner = m[m.index("(") + 1:m.rindex(")")] if ")" in m else ""
            parts = split_args(inner)
            if fn == "ALLSELECTED":
                removed.append(("allselected", None, None, None)); continue
            if not parts:
                removed.append(("own-fact", None, None, None)); continue
            first = parts[0]
            cm = re.match(rf"{TBL}\s*\[([^\]]+)\](?:\s*\.\s*\[([^\]]+)\])?\s*$", first)
            tm = re.match(rf"{TBL}\s*$", first)
            if fn == "ALLEXCEPT" and tm:
                tbl = (tm.group(1) or tm.group(2)).strip()
                keep = [k for k in re.findall(r"\[([^\]]+)\]", ",".join(parts[1:]))]
                removed.append(("allexcept", tbl, None, keep))
            elif cm:
                tbl = (cm.group(1) or cm.group(2) or "").strip()
                for p in parts:
                    c2 = re.match(rf"{TBL}\s*\[([^\]]+)\](?:\s*\.\s*\[([^\]]+)\])?", p)
                    if c2:
                        removed.append(("column", (c2.group(1) or c2.group(2) or "").strip(), c2.group(3), c2.group(4)))
            elif tm:
                tbl = (tm.group(1) or tm.group(2)).strip()
                removed.append(("own-fact" if norm(tbl) in facts else "table", tbl, None, None))
        sel_in_filter = any(re.search(r"\bSELECTEDVALUE\s*\(|\bEARLIER\s*\(|\bVALUES\s*\(", r, re.I) for r in rowctx)
        loo = bool(rowctx) and any(re.search(r"<>\s*SELECTEDVALUE\s*\(", r, re.I) for r in rowctx)
        kept = [k for k in kept if k not in rowctx]
        plans.append(dict(base=base_kind(base), removed=removed, kept=kept, rowctx=bool(rowctx),
                          sel=sel_in_filter, loo=loo, base_text=base[:60]))
    if not plans:
        return None
    # route of the whole measure = most restrictive plan
    routes = []
    for p in plans:
        kinds = {r[0] for r in p["removed"]}
        if "allselected" in kinds or (p["rowctx"] and not p["loo"]) or (p["sel"] and not p["loo"]):
            r = "NULL"
        elif p["loo"] and kinds <= {"own-fact"}:
            r = "PRECOMPUTE_LOO"
        elif kinds == {"own-fact"}:
            r = "PRECOMPUTE"
        elif p["base"] in ("DISTINCT", "NONADD", "OTHER"):
            r = "NULL"
        else:
            r = "WINDOW"
        p["route"] = r
        p["parts"] = 2 if p["base"] in ("AVERAGE", "RATIO") else 1
        routes.append(r)
    order = ["NULL", "PRECOMPUTE_LOO", "PRECOMPUTE", "WINDOW"]
    return dict(route=min(routes, key=order.index), plans=plans)


def load_measures(pbix):
    from pbixray import PBIXRay
    m = PBIXRay(pbix).dax_measures
    return {r["Name"]: str(r["Expression"] or "") for _, r in m.iterrows()}, {r["Name"]: r["TableName"] for _, r in m.iterrows()}


def describe(p):
    rem = []
    for k, t, c, lvl in p["removed"]:
        rem.append({"column": f"{t}[{c}]" + (f".[{lvl}]" if lvl else ""), "table": f"ALL({t})", "allexcept": f"ALLEXCEPT({t})",
                    "own-fact": f"ALL(own fact {t or ''})".replace(" )", ")"), "allselected": "ALLSELECTED"}[k])
    return f"base={p['base']:8} removes {', '.join(rem)}" + (f"  kept={len(p['kept'])}" if p["kept"] else "") + ("  row-context" if p["rowctx"] else "")


# ------------------------------------------------------------------ plan mode
def mode_plan(a):
    for pbix in a.pbix:
        measures, home = load_measures(pbix)
        print(f"\n=== {os.path.basename(pbix)}")
        counts = defaultdict(int)
        for name, expr in measures.items():
            pl = plan_for(expr, measures)
            if not pl:
                continue
            counts[pl["route"]] += 1
            print(f"  {pl['route']:15} {name[:38]:38} [{home[name]}]")
            for p in pl["plans"]:
                print(f"  {'':15}   {describe(p)}")
        print("  totals: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0


# ------------------------------------------------------------------ ddl mode
def _block(text, word):
    m = re.search(rf"\b{word}\s*\(", text, re.I)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0); i += 1
    return text[m.end():i - 1]


U = lambda s: s.strip().strip('"').upper()


def parse_ddl(text):
    keys, rels, dims, metrics, aliases = defaultdict(set), defaultdict(set), {}, {}, {}
    for t in split_args(_block(text, "tables")):
        m = re.match(r'(?:("?[\w$]+"?)\s+as\s+)?([\w.\"$]+)(.*)$', t, re.I | re.S)
        if m:
            phys = U(m.group(2).split(".")[-1]); al = U(m.group(1) or phys)
            aliases[norm(al)] = al; aliases.setdefault(norm(re.sub(r"_ENRICHED$", "", phys)), al)
            for km in re.finditer(r"(?:primary\s+key|unique)\s*\(([^)]*)\)", m.group(3), re.I):
                keys[al] |= {U(c) for c in km.group(1).split(",")}
    for r in split_args(_block(text, "relationships")):
        m = re.search(r'("?\w+"?)\s*\(([^)]*)\)\s*references\s+("?\w+"?)\s*\(([^)]*)\)', r, re.I)
        if m:
            rels[U(m.group(1))] |= {U(c) for c in m.group(2).split(",")}
            rels[U(m.group(3))] |= {U(c) for c in m.group(4).split(",")}
    for d in split_args(_block(text, "dimensions")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?[\w$]+"?)\s+as\s+("?\w+"?)\s*\.\s*("?[\w$]+"?)', d, re.I)
        if m:
            dims[f"{U(m.group(1))}.{U(m.group(2))}"] = U(m.group(4))
    for line in split_args(_block(text, "metrics")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?\w+"?)\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
        if m:
            body = m.group(3)
            cm = re.search(r"\bCOMMENT\s*=\s*'((?:[^']|'')*)'", body, re.I)
            metrics[U(m.group(2))] = dict(expr=" ".join(re.sub(r"\s+COMMENT\s*=\s*'(?:[^']|'')*'", "", body, flags=re.I).split()),
                                          comment=cm.group(1) if cm else "")
    return keys, rels, dims, metrics, aliases


def windows(expr):
    s = re.sub(r"'(?:[^']|'')*'", "''", expr)
    for m in re.finditer(r"\b(SUM|AVG|COUNT|MIN|MAX)\s*\(", s, re.I):
        depth, i = 1, m.end()
        while i < len(s) and depth:
            depth += {"(": 1, ")": -1}.get(s[i], 0); i += 1
        rest = s[i:]
        o = re.match(r"\s*OVER\s*\(", rest, re.I)
        if not o:
            continue
        j, dd = o.end(), 1
        while j < len(rest) and dd:
            dd += {"(": 1, ")": -1}.get(rest[j], 0); j += 1
        ex = re.search(r"PARTITION\s+BY\s+EXCLUDING\s+(.*)$", rest[o.end():j - 1], re.I | re.S)
        yield m.group(1).upper(), s[m.end():i - 1].strip(), [p.strip() for p in ex.group(1).split(",")] if ex else []


NULLRE = re.compile(r"^\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?$", re.I)


def emitted_route(expr):
    if NULLRE.match(expr):
        return "NULL"
    if " OVER" in expr.upper():
        return "WINDOW"
    if re.search(r'"?__SB_\w+"?|\bALL_\w+', expr, re.I):
        return "PRECOMPUTE"
    return "PLAIN"


def mode_ddl(a):
    if len(a.pbix) != len(a.ddl):
        sys.exit("one --ddl per --pbix")
    fails = 0
    for pbix, ddl in zip(a.pbix, a.ddl):
        measures, home = load_measures(pbix)
        keys, rels, dims, metrics, aliases = parse_ddl(open(ddl, encoding="utf-8", errors="ignore").read())
        mnorm = {norm(k): k for k in metrics}
        print(f"\n=== {os.path.basename(pbix)}  vs  {os.path.basename(ddl)}")
        tally = defaultdict(int)
        for name, expr in measures.items():
            pl = plan_for(expr, measures)
            if not pl:
                continue
            mk = mnorm.get(norm(name))
            if not mk:
                print(f"  FAIL  {name[:38]:38} not in the view"); fails += 1; continue
            met = metrics[mk]; got = emitted_route(met["expr"]); want = pl["route"]
            problems, warns = [], []
            want_cmp = "PRECOMPUTE" if want.startswith("PRECOMPUTE") else want
            if got != want_cmp:
                (warns if got == "NULL" else problems).append(f"route: expected {want}, emitted {got}")
            if got == "WINDOW":
                ws = list(windows(met["expr"]))
                need = max(p["parts"] for p in pl["plans"] if p["route"] == "WINDOW") if want == "WINDOW" else 1
                if len(ws) < need:
                    problems.append(f"{len(ws)} window term(s), decomposition needs {need} ({pl['plans'][0]['base']})")
                for agg, arg, excl in ws:
                    ref = re.fullmatch(r'(?:\(\s*)*("?\w+"?)\s*\.\s*("?\w+"?)(?:\s*\))*(?:\s*::\s*\w+)?', arg)
                    if not (ref and U(ref.group(2)) in metrics):
                        problems.append(f"{agg}({arg[:30]}) OVER: argument must be a metric reference")
                    else:
                        b = metrics[U(ref.group(2))]["expr"]
                        if re.match(r"\s*AVG\s*\(", b, re.I) or "/" in b:
                            problems.append(f"window over non-additive metric {U(ref.group(2))} - decompose")
                    for e in excl:
                        p = re.match(r'"?(\w+)"?\s*\.\s*"?([\w$]+)"?$', e)
                        k = f"{p.group(1).upper()}.{p.group(2).upper()}" if p else e
                        if k not in dims:
                            problems.append(f"EXCLUDING {e}: not a declared dimension")
                        else:
                            al, col = k.split(".")[0], dims[k]
                            explicit_cols = {norm(r[2]) for p in pl["plans"] for r in p["removed"] if r[0] == "column" and r[2]}
                            if norm(col) not in explicit_cols and (col in keys[al] or col in rels[al] or col.endswith(("SEQ", "ID", "KEY"))):
                                problems.append(f"EXCLUDING {k}: key/id column (query would require it - 010277)")
                    rem_tables = {norm(r[1] or "") for p in pl["plans"] for r in p["removed"] if r[0] in ("column", "table", "allexcept")}
                    ex_tables = {norm(e.split(".")[0]) for e in excl}
                    if rem_tables and ex_tables and not (ex_tables & {norm(aliases.get(t, t)) for t in rem_tables} | rem_tables):
                        problems.append(f"EXCLUDING tables {sorted(ex_tables)} do not match removed {sorted(rem_tables)}")
                if need == 2 and "::FLOAT" not in met["expr"].upper():
                    warns.append("ratio/average window without ::FLOAT")
                if "QUER" not in met["comment"].upper():
                    warns.append("no COMMENT 'must be queried with <dims>'")
            if got == "NULL" and not met["comment"]:
                warns.append("NULL without reason COMMENT")
            st = "FAIL" if problems else ("WARN" if warns else "PASS")
            tally[st] += 1; fails += bool(problems)
            print(f"  {st:4}  {name[:38]:38} plan {want:15} emitted {got:10} [{home[name]}]")
            for p in problems:
                print(f"          FAIL {p}")
            for w in warns:
                print(f"          warn {w}")
        print("  -> " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    return 1 if fails else 0


# ------------------------------------------------------------------ cases mode (synthetic, SemaBridge translator)
CASES = [
    # id, dax, expected route, expected parts, expected excluded (substring list) or None
    ("C1", "DIVIDE([Cnt], CALCULATE([Cnt], ALL(Orders[Status])))", "WINDOW", 1, ["STATUS"]),
    ("C2", "DIVIDE([Cnt], CALCULATE([Cnt], ALL(Product)))", "WINDOW", 1, ["PRODUCT"]),
    ("C3", "CALCULATE([Amt], ALL(Orders[Status]))", "WINDOW", 1, ["STATUS"]),
    ("C4", "CALCULATE([Amt], REMOVEFILTERS(Orders[Status]))", "WINDOW", 1, ["STATUS"]),
    ("C5", "CALCULATE([Amt], ALLNOBLANKROW(Orders[Status]))", "WINDOW", 1, ["STATUS"]),
    ("C6", "CALCULATE(AVERAGE(Orders[Amount]), ALL(Orders[Status]))", "WINDOW", 2, ["STATUS"]),
    ("C7", "CALCULATE([Rate], ALL(Orders[Status]))", "WINDOW", 2, ["STATUS"]),
    ("C8", "CALCULATE(MAX(Orders[Amount]), ALL(Orders[Status]))", "WINDOW", 1, ["STATUS"]),
    ("C9", "[Amt] - CALCULATE([Amt], ALL(Orders[Status]))", "WINDOW", 1, ["STATUS"]),
    ("C10", "1 - DIVIDE([Cnt], CALCULATE([Cnt], ALL(Orders[Status])))", "WINDOW", 1, ["STATUS"]),
    ("C11", "VAR t = CALCULATE([Cnt], ALL(Orders[Status])) RETURN DIVIDE([Cnt], t)", "WINDOW", 1, ["STATUS"]),
    ("C12", "DIVIDE(CALCULATE([Cnt], Orders[Flag] = 1), CALCULATE([Cnt], ALL(Orders[Status])))", "WINDOW", 1, ["STATUS"]),
    ("C13", "CALCULATE([Amt], ALL(Orders[Status]), Orders[Flag] = 1)", "WINDOW", 1, ["STATUS"]),
    ("C14", "CALCULATE([Amt], ALL(Orders[Status]), ALL(Product[Category]))", "WINDOW", 1, ["STATUS", "CATEGORY"]),
    ("C15", "CALCULATE([Amt], ALLEXCEPT(Product, Product[Category]))", "WINDOW", 1, ["PRODUCT"]),
    ("C16", "CALCULATE([Amt], ALL(Orders))", "PRECOMPUTE", 1, None),
    ("C17", "CALCULATE(MAX(Orders[OrderDate]), REMOVEFILTERS())", "PRECOMPUTE", 1, None),
    ("C18", "CALCULATE(DISTINCTCOUNT(Orders[Customer]), ALL(Orders[Status]))", "NULL", 1, None),
    ("C19", "CALCULATE([Amt], ALLSELECTED(Orders[Status]))", "NULL", 1, None),
    ("C20", "CALCULATE(AVERAGE(Orders[Amount]), FILTER(ALL(Orders), Orders[Status] <> SELECTEDVALUE(Orders[Status])))", "PRECOMPUTE_LOO", 1, None),
    ("C21", "CALCULATE([Amt], FILTER(ALL(Orders), Orders[Amount] > EARLIER(Orders[Amount])))", "NULL", 1, None),
    ("C22", "CALCULATE([Amt], ALL(Calendar[Date].[Month]))", "WINDOW", 1, ["MONTH"]),
    ("C23", "CALCULATE([Amt], ALL(Product[ProductID]))", "WINDOW", 1, ["PRODUCTID"]),   # key column: allowed only because DAX names it
    ("C24", "DIVIDE([Amt], CALCULATE([Amt], ALL(Product)), 0)", "WINDOW", 1, ["PRODUCT"]),
]
CTX = {"Cnt": "COUNTROWS(Orders)", "Amt": "SUM(Orders[Amount])", "Rate": "DIVIDE(SUM(Orders[Flag]), COUNTROWS(Orders))"}


def check_case_sql(cid, sql, route, parts, excl):
    if sql is None or NULLRE.match(sql or ""):
        return route == "NULL", "NULL"
    got = emitted_route(sql)
    want = "PRECOMPUTE" if route.startswith("PRECOMPUTE") else route
    if got != want:
        return False, f"route {got}, expected {route}"
    if got == "WINDOW":
        ws = list(windows(sql))
        if len(ws) < parts:
            return False, f"{len(ws)} windows, need {parts}"
        all_ex = " ".join(e.upper() for _, _, ex in ws for e in ex)
        miss = [x for x in (excl or []) if x not in all_ex]
        if miss:
            return False, f"EXCLUDING lacks {miss}: {all_ex[:80]}"
        if re.search(r"\b(SUM|AVG|MIN|MAX)\s*\(\s*\(?\s*(COUNT|SUM|AVG)\s*\(", sql, re.I):
            return False, "inlined aggregate inside window (not B1)"
    return True, got


REF_SQL = {   # reference outputs for --selftest (what a correct implementation emits)
    "C1": 'O.CNT / NULLIF(SUM(O.CNT) OVER (PARTITION BY EXCLUDING O."STATUS"), 0)',
    "C6": 'SUM(O.AMT_SUM::FLOAT) OVER (PARTITION BY EXCLUDING O."STATUS") / NULLIF(SUM(O.AMT_CNT::FLOAT) OVER (PARTITION BY EXCLUDING O."STATUS"), 0)',
    "C7": 'SUM(O.RATE_NUM::FLOAT) OVER (PARTITION BY EXCLUDING O."STATUS") / NULLIF(SUM(O.RATE_DEN::FLOAT) OVER (PARTITION BY EXCLUDING O."STATUS"), 0)',
    "C14": 'SUM(O.AMT) OVER (PARTITION BY EXCLUDING O."STATUS", P."CATEGORY")',
    "C16": 'MAX(O."__SB_ALL_SUM_AMOUNT")',
    "C18": "CAST(NULL AS DOUBLE)",
    "C20": 'IFF(COUNT(DISTINCT O.STATUS) = 1, (MAX(O."__SB_ALL_SUM_AMOUNT") - MAX(O."__SB_STATUS_SUM_AMOUNT")) / NULLIF(MAX(O."__SB_ALL_CNT_AMOUNT") - MAX(O."__SB_STATUS_CNT_AMOUNT"), 0), NULL)',
    "C22": 'SUM(O.AMT) OVER (PARTITION BY EXCLUDING C."MONTH")',
}


def mode_cases(a):
    ok_all = True
    if a.selftest:
        print("SELFTEST: reference SQL vs case expectations")
        for cid, dax, route, parts, excl in CASES:
            if cid in REF_SQL:
                ok, msg = check_case_sql(cid, REF_SQL[cid], route, parts, excl)
                ok_all &= ok
                print(f"  {cid:4} {'PASS' if ok else 'FAIL'}  {msg}")
        # planner on the synthetic DAX
        print("SELFTEST: reference planner routes")
        for cid, dax, route, parts, excl in CASES:
            pl = plan_for(dax, CTX)
            got = pl["route"] if pl else "PLAIN"
            ok = got == route
            ok_all &= ok
            print(f"  {cid:4} {'PASS' if ok else 'FAIL'}  planner {got:15} expected {route}")
        return 0 if ok_all else 1
    sys.path.insert(0, os.path.join(os.path.abspath(a.repo), "src"))
    try:
        from semabridge.converter.dax_ast_parser import try_ast_translate
    except Exception as exc:
        sys.exit(f"SemaBridge not importable ({exc}); use --selftest")
    col = lambda n, t="STRING": SimpleNamespace(unique_name=n, name=n, data_type=t, type=t)
    datasets = [
        SimpleNamespace(unique_name="Orders", name="Orders", columns=[col("OrderID", "INTEGER"), col("Status"), col("Amount", "FLOAT"),
                        col("Flag", "INTEGER"), col("Customer"), col("ProductID", "INTEGER"), col("OrderDate", "DATE")]),
        SimpleNamespace(unique_name="Product", name="Product", columns=[col("ProductID", "INTEGER"), col("Product"), col("Category")]),
        SimpleNamespace(unique_name="Calendar", name="Calendar", columns=[col("Date", "DATE"), col("Month"), col("Year", "INTEGER")]),
    ]
    rel = lambda f, fc, t, tc: SimpleNamespace(from_dataset=f, from_columns=[fc], to_dataset=t, to_columns=[tc], is_active=True)
    rels = [rel("Orders", "ProductID", "Product", "ProductID"), rel("Orders", "OrderDate", "Calendar", "Date")]
    aliases = {"Orders": "ORDERS", "Product": "PRODUCT", "Calendar": "CALENDAR"}
    msql = {"Cnt": "COUNT(*)", "Amt": "SUM(ORDERS.\"AMOUNT\"::FLOAT)", "Rate": "SUM(ORDERS.\"FLAG\"::FLOAT) / NULLIF(COUNT(*), 0)"}
    lookup = {d.unique_name: {c.unique_name for c in d.columns} for d in datasets}
    print("CASES through SemaBridge try_ast_translate (route and shape):")
    for cid, dax, route, parts, excl in CASES:
        try:
            sql = try_ast_translate(dax, "ORDERS", "CALENDAR", measure_sql_map=msql, known_measure_names=set(msql),
                                    primary_table_name="Orders", table_aliases=aliases, relationships=rels,
                                    datasets=datasets, dataset_col_lookup=lookup)
        except TypeError:
            sql = try_ast_translate(dax, "ORDERS", "CALENDAR", measure_sql_map=msql, known_measure_names=set(msql),
                                    primary_table_name="Orders", table_aliases=aliases, relationships=rels)
        except Exception as exc:
            sql = None
        ok, msg = check_case_sql(cid, sql, route, parts, excl)
        ok_all &= ok
        print(f"  {cid:4} {'PASS' if ok else 'FAIL'}  expected {route:15} {msg}")
        if not ok:
            print(f"        dax: {dax}\n        sql: {str(sql)[:160]}")
    print("Note: translator-level check (no COMMENT/precompute columns - those come from the DDL builder; use 'ddl' mode).")
    return 0 if ok_all else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["plan", "ddl", "cases"])
    ap.add_argument("--pbix", action="append", default=[]); ap.add_argument("--ddl", action="append", default=[])
    ap.add_argument("--repo", default="."); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    sys.exit({"plan": mode_plan, "ddl": mode_ddl, "cases": mode_cases}[a.mode](a))


if __name__ == "__main__":
    main()
