#!/usr/bin/env python3
"""
test_window_metrics.py - generic checks for share-of-total / window metrics. Read-only; changes no code.

PART 1  detector   Runs SemaBridge's own detect_nested_aggregate (and _is_scalar_metric_sql if found) on a fixed set
                   of SQL snippets. A real nested aggregate must be flagged WITH or WITHOUT OVER(); a window over a
                   metric reference (the form proven in Snowflake, "B1") must not be flagged and must pass the scalar
                   check. Catches an over-broad "OVER exemption".
PART 2  ddl        For every generated semantic-view DDL:
                   - FORM: each window metric is classified
                       B1        SUM(<TABLE>.<METRIC>) OVER (PARTITION BY EXCLUDING ...) where <METRIC> is a metric of
                                 the view                                  -> proven in Snowflake (PASS)
                       INLINED   SUM(<aggregate call>) OVER (...)           -> NOT proven in Snowflake (FAIL until tested)
                   - every name after EXCLUDING is a declared dimension
                   - with --pbix: the DAX ALL()/REMOVEFILTERS()/ALLEXCEPT() target is compared with the excluded list:
                       ALL(Table)            -> every dimension of that table must be excluded
                       ALL(Table[Col])       -> that dimension must be excluded
                       ALLEXCEPT(Table, c..) -> every dimension of the table except c.. must be excluded
                       ALL(<the measure's own fact table>) must NOT become a window (DAX ignores every filter there)
                   - no helper columns (ALL_*, __SB_ALL_*) exposed as dimensions
                   - NULL share-of-total metrics are reported (not an error)

usage:
  uv run python test_window_metrics.py --repo .                                         # part 1 + output/debug/*.sql
  uv run python test_window_metrics.py --repo . --ddl "output/debug/*.sql" --pbix "<model>.pbix"
  uv run python test_window_metrics.py --part detector --repo .
Exit code 1 if anything fails.
"""
import argparse, glob, importlib, os, re, sys, warnings
from collections import defaultdict
warnings.filterwarnings("ignore")

AGG = r"(?:SUM|AVG|COUNT|MIN|MAX|MEDIAN|STDDEV|VARIANCE|ANY_VALUE|COUNT_IF|LISTAGG|ARRAY_AGG)"

# --------------------------------------------------------------------------- PART 1
DETECTOR_CASES = [
    # (id, sql, nested?, note)
    ("N1", "SUM(SUM(CASES.CSAT))", True, "plain nested"),
    ("N2", "AVG(COUNT(*))", True, "nested with COUNT(*)"),
    ("N3", "COUNT(CASE WHEN CASES.CSAT = MAX(CASES.CSAT) THEN 1 END)", True, "aggregate inside CASE inside aggregate"),
    ("N4", 'COUNT(*) / NULLIF(MAX(CASES."__SB_ALL_ROWS"), 0)', False, "two separate aggregates"),
    ("N5", 'SUM(CASES."CASE_COUNT") OVER (PARTITION BY EXCLUDING CASES."SUBJECT")', False, "B1: window over a metric reference"),
    ("N6", 'SUM(SUM(CASES.CSAT)) OVER (PARTITION BY EXCLUDING CASES."SUBJECT")', True, "nested inside a window - must still be flagged"),
    ("N7", '(COUNT(*)) / NULLIF(SUM((COUNT(*))::FLOAT) OVER (PARTITION BY EXCLUDING PRODUCTS.PRODUCT), 0)', True,
     "INLINED form (structurally nested; only valid if Snowflake accepts it)"),
    ("N8", 'CASES."CASE_COUNT" / NULLIF(SUM(CASES."CASE_COUNT") OVER (PARTITION BY EXCLUDING PRODUCTS."PRODUCT", PRODUCTS."PRODUCT_CATEGORY"), 0)',
     False, "B1 with several excluded dimensions"),
    ("N9", "SUM(CASE WHEN CASES.STATUS = 'SUM(' THEN 1 END)", False, "aggregate name inside a string literal"),
    ("N10", "COUNT(DISTINCT CASES.AGENT) + MAX(IFF(CASES.AGENT IS NULL, 1, 0))", False, "DISTINCT + separate MAX"),
    ("N11", "SUM(CASES.CSAT) / NULLIF(COUNT(CASES.CSAT), 0)", False, "ratio of two aggregates"),
    ("N12", "MAX(COUNT(DISTINCT CASES.AGENT)) OVER ()", True, "nested distinct inside window"),
]
SCALAR_OK = ["N5", "N8"]          # these must pass the scalar/syntax check (they are valid metric SQL)


def find(fn_names):
    found = {}
    for mod in ("semabridge.connectors.type_safety_validator", "semabridge.connectors.metrics_clause_builder",
                "semabridge.connectors.translator", "semabridge.connectors.ddl_helpers"):
        try:
            m = importlib.import_module(mod)
        except Exception:
            continue
        for n in fn_names:
            if n not in found and hasattr(m, n):
                found[n] = (mod, getattr(m, n))
    return found


def reference_detector(sql):
    """Structural reference: an aggregate call whose own argument list contains another aggregate call."""
    s = re.sub(r"'(?:[^']|'')*'", "''", sql)
    for m in re.finditer(rf"\b{AGG}\s*\(", s, re.I):
        depth, i = 1, m.end()
        while i < len(s) and depth:
            depth += {"(": 1, ")": -1}.get(s[i], 0); i += 1
        if re.search(rf"\b{AGG}\s*\(", s[m.end():i - 1], re.I):
            return m.group(0)
    return None


def part_detector():
    print("=== PART 1: nested-aggregate detector ===")
    fns = find(["detect_nested_aggregate", "_is_scalar_metric_sql"])
    det = fns.get("detect_nested_aggregate")
    if det:
        print(f"  using SemaBridge {det[0]}.detect_nested_aggregate")
        detect = det[1]
    else:
        print("  SemaBridge detector not importable - using the reference detector (results show expected behaviour only)")
        detect = reference_detector
    ok_all = True
    for cid, sql, nested, note in DETECTOR_CASES:
        try:
            got = detect(sql)
            flagged = bool(got)
        except Exception as exc:
            flagged, got = None, f"error {exc}"
        ok = flagged == nested
        ok_all &= ok
        print(f"  {cid:4} {'PASS' if ok else 'FAIL'}  expected {'nested' if nested else 'clean ':6}  got {'nested' if flagged else 'clean ' if flagged is False else got}   {note}")
    sc = fns.get("_is_scalar_metric_sql")
    if sc:
        for cid in SCALAR_OK:
            sql = next(c[1] for c in DETECTOR_CASES if c[0] == cid)
            try:
                res = sc[1](sql)
            except TypeError:
                res = None
            if res is False:
                ok_all = False
                print(f"  {cid:4} FAIL  _is_scalar_metric_sql rejects a valid window metric")
            elif res is True:
                print(f"  {cid:4} PASS  _is_scalar_metric_sql accepts the window metric")
    hint = ""
    if det:
        n6 = bool(detect(DETECTOR_CASES[5][1]))
        n7 = bool(detect(DETECTOR_CASES[6][1]))
        if not n6:
            hint = "  -> N6 not flagged: the detector exempts OVER(...) too broadly (real nested aggregates slip through)"
        elif not n7:
            hint = "  -> N7 (inlined form) not flagged: generator output passes only because of an exemption"
    if hint:
        print(hint)
    return ok_all


# --------------------------------------------------------------------------- PART 2
def _block(text, word):
    m = re.search(rf"\b{word}\s*\(", text, re.I)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0); i += 1
    return text[m.end():i - 1]


def _split(s):
    out, depth, cur, q = [], 0, [], None
    for ch in s:
        if q:
            cur.append(ch); q = None if ch == q else q; continue
        if ch in "'\"":
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip()); cur = []; continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def U(s):
    return s.strip().strip('"').upper()


def parse_view(text):
    dims = defaultdict(set)
    for d in _split(_block(text, "dimensions")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?[\w$]+"?)\s+as\s', d, re.I)
        if m:
            dims[U(m.group(1))].add(U(m.group(2)))
    metrics = {}
    for line in _split(_block(text, "metrics")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?\w+"?)\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
        if m:
            metrics[U(m.group(2))] = (U(m.group(1)), " ".join(m.group(3).split()))
    return dims, metrics


def windows(expr):
    """yield (agg, argument, partition_list) for every '<AGG>(arg) OVER (PARTITION BY EXCLUDING ...)'."""
    s = re.sub(r"'(?:[^']|'')*'", "''", expr)
    for m in re.finditer(rf"\b({AGG})\s*\(", s, re.I):
        depth, i = 1, m.end()
        while i < len(s) and depth:
            depth += {"(": 1, ")": -1}.get(s[i], 0); i += 1
        arg = s[m.end():i - 1]
        rest = s[i:]
        o = re.match(r"\s*OVER\s*\(", rest, re.I)
        if not o:
            continue
        j, depth = o.end(), 1
        while j < len(rest) and depth:
            depth += {"(": 1, ")": -1}.get(rest[j], 0); j += 1
        spec = rest[o.end():j - 1]
        ex = re.search(r"PARTITION\s+BY\s+EXCLUDING\s+(.*?)(?:\bORDER\s+BY\b|$)", spec, re.I | re.S)
        parts = [p.strip() for p in ex.group(1).split(",")] if ex else []
        yield m.group(1).upper(), arg.strip(), parts, spec.strip()


def dax_all_targets(pbix):
    from pbixray import PBIXRay
    m = PBIXRay(pbix).dax_measures
    out = {}
    pat = re.compile(r"\b(ALL|REMOVEFILTERS|ALLEXCEPT)\s*\(\s*(?:'([^']+)'|([A-Za-z_][\w ]*?))\s*(?:\[([^\]]+)\])?\s*(,[^)]*)?\)", re.I)
    for _, r in m.iterrows():
        e = str(r.get("Expression") or "")
        hits = []
        for x in pat.finditer(e):
            fn, table, col, extra = x.group(1).upper(), (x.group(2) or x.group(3) or "").strip(), x.group(4), x.group(5)
            keep = re.findall(r"\[([^\]]+)\]", extra or "") if fn == "ALLEXCEPT" else []
            hits.append((fn, table, col, keep))
        if hits:
            key = re.sub(r"[^A-Z0-9]", "", r["Name"].upper().replace("%", "PCT"))
            out[key] = (r["Name"], r["TableName"], hits)
    return out


def norm_dim(name):
    return re.sub(r"[^A-Z0-9]", "", name.upper().replace("%", "PCT"))


def part_ddl(pattern, pbix):
    print("\n=== PART 2: window metrics in generated DDL ===")
    targets = dax_all_targets(pbix) if pbix else {}
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"  no files match {pattern}"); return True
    all_ok = True
    for f in files:
        text = open(f, encoding="utf-8", errors="ignore").read()
        if not re.search(r"\bsemantic\s+view\b", text, re.I):
            continue
        dims, metrics = parse_view(text)
        declared = {f"{t}.{d}" for t, ds in dims.items() for d in ds}
        problems, notes = [], []
        helper = sorted(d for d in declared if re.search(r"\.(ALL_\w+|__SB_ALL\w*)$", d))
        if helper:
            problems.append(f"helper column(s) exposed as dimensions: {', '.join(helper)}")
        for name, (table, expr) in metrics.items():
            ws = list(windows(expr))
            dax = targets.get(re.sub(r"[^A-Z0-9]", "", name))
            if not ws:
                if dax and re.fullmatch(r"\(?\s*CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)\s*\)?", expr, re.I):
                    notes.append(f"{name}: NULL ({dax[2][0][0]} measure)")
                continue
            for agg, arg, parts, spec in ws:
                ref = re.fullmatch(r'(?:\(\s*)*("?\w+"?)\s*\.\s*("?\w+"?)(?:\s*\))*(?:\s*::\s*\w+)?', arg)
                if ref and U(ref.group(2)) in metrics:
                    form = "B1"
                elif re.search(rf"\b{AGG}\s*\(", arg, re.I):
                    form = "INLINED"
                    problems.append(f"{name}: INLINED window {agg}({arg[:40]}) OVER (...) - not the Snowflake-proven form; "
                                    f"use {agg}(<TABLE>.<METRIC>) or prove it with sf_share_of_total_check.py (B5)")
                else:
                    form = "OTHER"
                    notes.append(f"{name}: window over a plain column {agg}({arg[:40]}) - check")
                undeclared = [p for p in parts if U(p.split(".")[0]) + "." + U(p.split(".")[-1]) not in declared]
                if undeclared:
                    problems.append(f"{name}: EXCLUDING undeclared dimension(s) {undeclared}")
                if dax:
                    excl = {norm_dim(p.split(".")[-1]) for p in parts}
                    excl_tables = {U(p.split(".")[0]) for p in parts}
                    for fn, tbl, col, keep in dax[2]:
                        alias = next((t for t in dims if norm_dim(t) == norm_dim(tbl)), None)
                        if alias is None:
                            continue
                        if fn in ("ALL", "REMOVEFILTERS") and col is None:
                            if alias == table:
                                problems.append(f"{name}: ALL({tbl}) is the metric's own table - DAX ignores every filter; "
                                                f"must not be a query-time window (precompute or NULL)")
                                continue
                            missing = sorted(d for d in dims[alias] if norm_dim(d) not in excl)
                            if missing:
                                problems.append(f"{name}: ALL({tbl}) must exclude every {alias} dimension; missing {missing}")
                        elif fn in ("ALL", "REMOVEFILTERS"):
                            if norm_dim(col) not in excl:
                                problems.append(f"{name}: ALL({tbl}[{col}]) but {col} is not excluded")
                        elif fn == "ALLEXCEPT":
                            keepn = {norm_dim(k) for k in keep}
                            missing = sorted(d for d in dims[alias] if norm_dim(d) not in excl and norm_dim(d) not in keepn)
                            wrong = sorted(k for k in keep if norm_dim(k) in excl)
                            if missing:
                                problems.append(f"{name}: ALLEXCEPT({tbl}, ...) must exclude {missing}")
                            if wrong:
                                problems.append(f"{name}: ALLEXCEPT keeps {wrong} but they are excluded")
                notes.append(f"{name}: {form}  EXCLUDING {', '.join(parts) or '(nothing)'}")
        status = "PASS" if not problems else "FAIL"
        all_ok &= not problems
        print(f"\n  {status}  {os.path.basename(f)}")
        for n in notes:
            print(f"        {n}")
        for p in problems:
            print(f"        FAIL  {p}")
    return all_ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="."); ap.add_argument("--part", choices=["detector", "ddl", "all"], default="all")
    ap.add_argument("--ddl", default=os.path.join("output", "debug", "*.sql")); ap.add_argument("--pbix")
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(os.path.abspath(a.repo), "src"))
    ok = True
    if a.part in ("detector", "all"):
        ok &= part_detector()
    if a.part in ("ddl", "all"):
        pat = a.ddl if os.path.isabs(a.ddl) else os.path.join(os.path.abspath(a.repo), a.ddl)
        ok &= part_ddl(pat, a.pbix)
    print(f"\n{'ALL CHECKS PASS' if ok else 'SOME CHECKS FAILED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
