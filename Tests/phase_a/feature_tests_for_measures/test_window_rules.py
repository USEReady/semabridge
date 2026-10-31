#!/usr/bin/env python3
"""
test_window_rules.py - checks a generated semantic-view DDL against the window-metric rules
(PROMPT_window_metrics.md) for EVERY measure in a .pbix that removes filters. Generic, read-only.

For each DAX measure using ALL / REMOVEFILTERS / ALLEXCEPT / ALLSELECTED / ALLNOBLANKROW:
  expected route  WINDOW | PRECOMPUTE (ALL of the measure's own fact) | NULL (non-decomposable / ALLSELECTED / ...)
  checks on the emitted metric:
    R-FORM     window arguments are metric references (B1), never inlined aggregates (SUM(COUNT(*)) OVER)
    R-DECL     every PARTITION BY EXCLUDING name is a declared dimension
    R-TABLE    excluded dimensions belong to the table named in ALL(...)
    R-COL      ALL(T[c]) excludes exactly c's dimension
    R-MIN      no key / id / helper dimension in the exclusion list (minimal list, Snowflake 010277)
    R-SHORT    no window over a non-additive metric (AVG(..) or a ratio) - must be decomposed
    R-FACT     ALL(<own fact>) is not a window
    R-NONDEC   DISTINCTCOUNT / MEDIAN / ALLSELECTED measures are NULL, not windows
    R-COMMENT  window metrics have COMMENT naming the required dimension; NULLs have a reason (WARN)
usage:
  uv run python test_window_rules.py --pbix "<model>.pbix" --ddl "<view>.sql" [--pbix .. --ddl ..]
Exit code 1 on any FAIL (WARN does not fail).
"""
import argparse, re, sys, warnings
from collections import defaultdict
warnings.filterwarnings("ignore")

AGG = r"(?:SUM|AVG|COUNT|MIN|MAX|MEDIAN|COUNT_IF|ANY_VALUE)"
_REP = {"%": "PCT", "$": "DOL", "&": "AND", "#": "NUM"}


def norm(s):
    s = "".join(_REP.get(c, c) for c in str(s)).upper()
    s = re.sub(r"^COL_", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


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


U = lambda s: s.strip().strip('"').upper()


def parse_ddl(text):
    tables = {}
    for t in _split(_block(text, "tables")):
        m = re.match(r'(?:("?[\w$]+"?)\s+as\s+)?([\w.\"$]+)(.*)$', t, re.I | re.S)
        if m:
            phys = U(m.group(2).split(".")[-1]); alias = U(m.group(1) or phys)
            keys = set()
            for km in re.finditer(r"(?:primary\s+key|unique)\s*\(([^)]*)\)", m.group(3), re.I):
                keys |= {U(c) for c in km.group(1).split(",")}
            tables[alias] = dict(phys=phys, keys=keys)
    fks = defaultdict(set)
    for r in _split(_block(text, "relationships")):
        m = re.search(r'("?\w+"?)\s*\(([^)]*)\)\s*references\s+("?\w+"?)\s*\(([^)]*)\)', r, re.I)
        if m:
            fks[U(m.group(1))] |= {U(c) for c in m.group(2).split(",")}
            fks[U(m.group(3))] |= {U(c) for c in m.group(4).split(",")}
    dims = {}          # "ALIAS.NAME" -> physical column
    for d in _split(_block(text, "dimensions")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?[\w$]+"?)\s+as\s+("?\w+"?)\s*\.\s*("?[\w$]+"?)', d, re.I)
        if m:
            dims[f"{U(m.group(1))}.{U(m.group(2))}"] = U(m.group(4))
    metrics = {}
    for line in _split(_block(text, "metrics")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?\w+"?)\s+(?:non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
        if m:
            body = m.group(3)
            cm = re.search(r"\bCOMMENT\s*=\s*'((?:[^']|'')*)'", body, re.I)
            expr = re.sub(r"\s+COMMENT\s*=\s*'(?:[^']|'')*'", "", body, flags=re.I)
            metrics[U(m.group(2))] = dict(table=U(m.group(1)), expr=" ".join(expr.split()), comment=cm.group(1) if cm else "")
    return tables, fks, dims, metrics


def windows(expr):
    s = re.sub(r"'(?:[^']|'')*'", "''", expr)
    for m in re.finditer(rf"\b({AGG})\s*\(", s, re.I):
        depth, i = 1, m.end()
        while i < len(s) and depth:
            depth += {"(": 1, ")": -1}.get(s[i], 0); i += 1
        arg, rest = s[m.end():i - 1], s[i:]
        o = re.match(r"\s*OVER\s*\(", rest, re.I)
        if not o:
            continue
        j, depth = o.end(), 1
        while j < len(rest) and depth:
            depth += {"(": 1, ")": -1}.get(rest[j], 0); j += 1
        spec = rest[o.end():j - 1]
        ex = re.search(r"PARTITION\s+BY\s+EXCLUDING\s+(.*?)(?:\bORDER\s+BY\b|$)", spec, re.I | re.S)
        yield m.group(1).upper(), arg.strip(), [p.strip() for p in ex.group(1).split(",")] if ex else []


FILTER_FN = re.compile(r"\b(ALLSELECTED|ALLEXCEPT|ALLNOBLANKROW|REMOVEFILTERS|ALL)\s*\(\s*(?:'([^']+)'|([A-Za-z_][\w ]*?))?\s*(?:\[([^\]]+)\])?\s*(\.\s*\[[^\]]+\])?\s*(,[^()]*)?\)", re.I)


def dax_info(expr, measures, depth=0):
    """inline measure refs (2 levels) to find aggregates; return text"""
    if depth > 2:
        return expr
    def rep(m):
        name = m.group(1)
        if name in measures:
            return "(" + dax_info(measures[name], measures, depth + 1) + ")"
        return m.group(0)
    return re.sub(r"(?<![\w'\]])\[([^\]]+)\]", rep, expr)


def check(pbix, ddl_path):
    from pbixray import PBIXRay
    px = PBIXRay(pbix)
    mdf = px.dax_measures
    measures = {r["Name"]: str(r["Expression"] or "") for _, r in mdf.iterrows()}
    home = {r["Name"]: r["TableName"] for _, r in mdf.iterrows()}
    tables, fks, dims, metrics = parse_ddl(open(ddl_path, encoding="utf-8", errors="ignore").read())
    alias_of = {}
    for al, t in tables.items():
        alias_of.setdefault(norm(al), al); alias_of.setdefault(norm(re.sub(r"_ENRICHED$", "", t["phys"])), al)
    metric_by_norm = {norm(k): k for k in metrics}
    fails = warns = 0
    print(f"\n=== {pbix.replace(chr(92), '/').split('/')[-1]}  vs  {ddl_path.replace(chr(92), '/').split('/')[-1]}")
    for name, expr in measures.items():
        hits = list(FILTER_FN.finditer(expr))
        if not hits:
            continue
        mkey = metric_by_norm.get(norm(name))
        if not mkey:
            print(f"  MISSING  {name}: no metric in the view"); fails += 1; continue
        met = metrics[mkey]
        full = dax_info(expr, measures)
        agg_cols = re.findall(r"\b(?:SUM|SUMX|AVERAGE|AVERAGEX|COUNT|COUNTA|COUNTROWS|COUNTX|MIN|MAX|DISTINCTCOUNT)\s*\(\s*(?:'([^']+)'|([A-Za-z_][\w ]*))", full, re.I)
        fact_tables = {norm(a or b) for a, b in agg_cols}
        is_null = bool(re.fullmatch(r"\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?", met["expr"], re.I))
        ws = list(windows(met["expr"]))
        problems, notes = [], []
        nondec = re.search(r"\b(DISTINCTCOUNT|DISTINCTCOUNTNOBLANK|MEDIAN|MEDIANX|PERCENTILE\w*)\s*\(", full, re.I)
        sel = any(h.group(1).upper() == "ALLSELECTED" for h in hits)
        all_fact = any(h.group(1).upper() in ("ALL", "REMOVEFILTERS") and not h.group(4)
                       and norm(h.group(2) or h.group(3) or "") in fact_tables for h in hits)
        rowctx = re.search(r"FILTER\s*\(\s*ALL\w*\s*\(", full, re.I) and re.search(r"\b(SELECTEDVALUE|EARLIER|VALUES|HASONEVALUE)\s*\(", full, re.I)
        route = "NULL" if (sel or nondec or rowctx) else ("PRECOMPUTE" if all_fact else "WINDOW")
        if route == "NULL" and ws:
            problems.append(f"R-NONDEC window emitted for {'ALLSELECTED' if sel else ('FILTER(ALL(..)) with selection/row context' if rowctx else nondec.group(1))} (must be NULL + reason)")
        if route == "PRECOMPUTE" and ws:
            problems.append("R-FACT ALL(<own fact>) means 'ignore every filter' - must be precomputed, not a window")
        if route == "WINDOW" and is_null:
            notes.append("NULL (window possible)" + (f" - reason: {met['comment'][:70]}" if met["comment"] else " - no reason COMMENT"))
            warns += 1
        if is_null and not met["comment"]:
            notes.append("R-COMMENT NULL without reason COMMENT"); warns += 1
        for agg, arg, excl in ws:
            ref = re.fullmatch(r'(?:\(\s*)*("?\w+"?)\s*\.\s*("?\w+"?)(?:\s*\))*(?:\s*::\s*\w+)?', arg)
            if not (ref and U(ref.group(2)) in metrics):
                problems.append(f"R-FORM {agg}({arg[:40]}) OVER - window argument must be a metric reference")
            else:
                base = metrics[U(ref.group(2))]["expr"]
                if re.match(r"\s*AVG\s*\(", base, re.I) or re.search(r"/", base):
                    problems.append(f"R-SHORT window over non-additive metric {U(ref.group(2))} ({base[:40]}) - decompose")
            declared = set(dims)
            for e in excl:
                p = re.match(r'"?(\w+)"?\s*\.\s*"?([\w$]+)"?$', e)
                key = f"{p.group(1).upper()}.{p.group(2).upper()}" if p else e
                if key not in declared:
                    problems.append(f"R-DECL {e} is not a declared dimension"); continue
                al, dn = key.split(".")
                col = dims[key]
                if col.endswith(("SEQ", "ID", "KEY")) or col in tables.get(al, {}).get("keys", set()) or col in fks.get(al, set()) or dn.startswith("__SB"):
                    problems.append(f"R-MIN {key} is a key/helper dimension - not groupable, makes queries fail (010277)")
            for h in hits:
                if h.group(1).upper() in ("ALLSELECTED",):
                    continue
                tbl = h.group(2) or h.group(3)
                al = alias_of.get(norm(tbl or ""))
                ex_tables = {e.split(".")[0].strip('"').upper() for e in excl}
                if al and excl and h.group(1).upper() != "ALLEXCEPT" and norm(tbl) not in fact_tables and al not in ex_tables:
                    problems.append(f"R-TABLE ALL({tbl}) but excluded dims are on {sorted(ex_tables)}")
                if h.group(4) and al and h.group(1).upper() in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW") and not h.group(5):
                    want = norm(h.group(4))
                    if not any(norm(dims.get(f"{e.split('.')[0].strip(chr(34)).upper()}.{e.split('.')[-1].strip(chr(34)).upper()}", "")) == want for e in excl):
                        problems.append(f"R-COL ALL({tbl}[{h.group(4)}]) but that column's dimension is not excluded")
            if "QUER" not in met["comment"].upper():
                notes.append("R-COMMENT window metric without 'queried with <dims>' COMMENT"); warns += 1
            if re.search(r"/\s*NULLIF\s*\(\s*SUM\(", met["expr"], re.I) and "::FLOAT" not in met["expr"].upper():
                notes.append("ratio window without ::FLOAT (integer division rounding)"); warns += 1
        status = "FAIL" if problems else ("WARN" if notes else "PASS")
        fails += bool(problems)
        form = "NULL" if is_null else ("WINDOW" if ws else "PLAIN")
        print(f"  {status:4}  {name[:38]:38} expected {route:10} emitted {form:6}  [{home[name]}]")
        for p in problems:
            print(f"          FAIL {p}")
        for n in notes:
            print(f"          warn {n}")
    print(f"  -> {fails} FAIL, {warns} warn")
    return fails == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbix", action="append", required=True); ap.add_argument("--ddl", action="append", required=True)
    a = ap.parse_args()
    if len(a.pbix) != len(a.ddl):
        sys.exit("one --ddl per --pbix")
    ok = all([check(p, d) for p, d in zip(a.pbix, a.ddl)])
    print("\nALL MODELS PASS" if ok else "\nSOME MODELS FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
