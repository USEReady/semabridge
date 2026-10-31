#!/usr/bin/env python3
"""
ddl_regression_diff.py - did today's changes REMOVE anything that worked before?
Compares two sets of generated semantic-view DDLs (e.g. a copy of output/debug from yesterday vs now), per model:

  TABLES         tables removed / added, physical name changed (e.g. DATE -> COL_DATE)
  RELATIONSHIPS  relationships removed / added (e.g. CASES -> CASE_CALENDAR dropped)
  DIMENSIONS     dimensions removed / renamed (helper columns ALL_*, IS_*, MAX_DATE listed separately)
  METRICS        REAL -> NULL / MISSING  = REGRESSION
                 NULL -> REAL            = improvement
                 REAL -> REAL, SQL changed = listed (check it is intended)
                 NON ADDITIVE BY lost    = REGRESSION
  ENRICHED       enriched (_ENRICHED) tables that are no longer used

usage:
  # 1) keep a baseline copy once:   xcopy /E /I output\debug baseline_ddl_2026-10-30
  # 2) after changes, regenerate and compare:
  python ddl_regression_diff.py --before baseline_ddl_2026-10-30 --after output\debug
  python ddl_regression_diff.py --before old.sql --after new.sql          # single files
Exit code 1 if any REGRESSION is found.
"""
import argparse, glob, os, re, sys


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
NULLRE = re.compile(r"^\(?\s*(CAST\s*\(\s*NULL\s+AS\s+\w+\s*\)|NULL)\s*\)?\s*$", re.I)


def parse(text):
    tables = {}
    for t in _split(_block(text, "tables")):
        m = re.match(r'(?:("?[\w$]+"?)\s+as\s+)?([\w.\"$]+)', t, re.I)
        if m:
            phys = U(m.group(2).split(".")[-1])
            tables[U(m.group(1) or phys)] = phys
    rels = set()
    for r in _split(_block(text, "relationships")):
        m = re.search(r'("?\w+"?)\s*\(([^)]*)\)\s*references\s+("?\w+"?)\s*\(([^)]*)\)', r, re.I)
        if m:
            rels.add(f"{U(m.group(1))}({','.join(U(c) for c in m.group(2).split(','))}) -> "
                     f"{U(m.group(3))}({','.join(U(c) for c in m.group(4).split(','))})")
    dims = set()
    for d in _split(_block(text, "dimensions")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?[\w$]+"?)\s+as\s', d, re.I)
        if m:
            dims.add(f"{U(m.group(1))}.{U(m.group(2))}")
    metrics = {}
    for line in _split(_block(text, "metrics")):
        m = re.match(r'("?\w+"?)\s*\.\s*("?\w+"?)\s+(non\s+additive\s+by\s*\([^)]*\)\s+)?as\s+(.*)$', line, re.I | re.S)
        if m:
            expr = " ".join(re.sub(r"/\*.*?\*/|\s+COMMENT\s*=\s*'(?:[^']|'')*'", "", m.group(4), flags=re.S | re.I).split())
            metrics[U(m.group(2))] = dict(table=U(m.group(1)), expr=expr, na=bool(m.group(3)),
                                          status="NULL" if NULLRE.match(expr) else "REAL")
    return tables, rels, dims, metrics


def model_key(path, text):
    m = re.search(r"semantic\s+view\s+([\w.\"$]+)", text, re.I)
    return U(m.group(1).split(".")[-1]) if m else os.path.basename(path)


def load(p):
    files = [p] if os.path.isfile(p) else sorted(glob.glob(os.path.join(p, "*.sql")))
    out = {}
    for f in files:
        t = open(f, encoding="utf-8", errors="ignore").read()
        if re.search(r"\bsemantic\s+view\b", t, re.I):
            out[model_key(f, t)] = parse(t)
    return out


HELPER = re.compile(r"\.(ALL_\w+|__SB_\w+|IS_[A-Z0-9_]+|MAX_DATE|_CURRENT\w*)$")


def compare(name, b, a):
    (tb, rb, db, mb), (ta, ra, da, ma) = b, a
    reg, imp, info = [], [], []
    for al in sorted(set(tb) - set(ta)):
        reg.append(f"TABLE removed: {al} ({tb[al]})")
    for al in sorted(set(tb) & set(ta)):
        if tb[al] != ta[al]:
            reg.append(f"TABLE physical name changed: {al}: {tb[al]} -> {ta[al]}")
    for al in sorted(set(ta) - set(tb)):
        info.append(f"table added: {al} ({ta[al]})")
    for r in sorted(rb - ra):
        reg.append(f"RELATIONSHIP removed: {r}")
    for r in sorted(ra - rb):
        info.append(f"relationship added: {r}")
    lost = sorted(db - da)
    lost_real = [d for d in lost if not HELPER.search(d)]
    renamed = []
    for d in lost_real:
        col = d.split(".")[1]
        twin = next((x for x in da - db if x.split(".")[0] == d.split(".")[0] and (x.split(".")[1].endswith(col) or col.endswith(x.split(".")[1]))), None)
        if twin:
            renamed.append(f"{d} -> {twin}")
    if renamed:
        info.append(f"dimensions renamed ({len(renamed)}): " + ", ".join(renamed[:6]) + (" ..." if len(renamed) > 6 else ""))
    gone = [d for d in lost_real if not any(r.startswith(d + " ") for r in renamed)]
    for d in gone:
        reg.append(f"DIMENSION removed: {d}")
    for d in [d for d in lost if HELPER.search(d)]:
        info.append(f"helper dimension removed: {d}")
    for k in sorted(set(mb) | set(ma)):
        x, y = mb.get(k), ma.get(k)
        if x and not y:
            reg.append(f"METRIC missing now: {k} (was {x['status']})")
        elif y and not x:
            info.append(f"metric added: {k} ({y['status']})")
        elif x["status"] == "REAL" and y["status"] == "NULL":
            reg.append(f"METRIC REAL -> NULL: {k}")
        elif x["status"] == "NULL" and y["status"] == "REAL":
            imp.append(f"metric NULL -> REAL: {k}")
        elif x["status"] == "REAL" and x["expr"] != y["expr"]:
            info.append(f"metric SQL changed: {k}")
        if x and y and x["na"] and not y["na"] and y["status"] == "REAL":
            reg.append(f"NON ADDITIVE BY lost: {k} (closing balance now sums every period)")
        if x and y and x["table"] != y["table"] and y["status"] == "REAL":
            info.append(f"metric re-anchored: {k}: {x['table']} -> {y['table']}")
    enr_b = {a for a, p in tb.items() if p.endswith("_ENRICHED")}
    enr_a = {a for a, p in ta.items() if p.endswith("_ENRICHED")}
    for al in sorted(enr_b - enr_a):
        reg.append(f"ENRICHED view no longer used for {al} (dimension copies / anchors may be gone)")
    real_b = sum(m["status"] == "REAL" for m in mb.values()); real_a = sum(m["status"] == "REAL" for m in ma.values())
    print(f"\n=== {name}   metrics REAL {real_b} -> {real_a}   relationships {len(rb)} -> {len(ra)}   tables {len(tb)} -> {len(ta)}")
    for r in reg:
        print(f"  REGRESSION  {r}")
    for i in imp:
        print(f"  improved    {i}")
    for i in info:
        print(f"  changed     {i}")
    if not (reg or imp or info):
        print("  identical")
    return len(reg)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", required=True); ap.add_argument("--after", required=True)
    a = ap.parse_args()
    before, after = load(a.before), load(a.after)
    total = 0
    for name in sorted(set(before) | set(after)):
        if name not in after:
            print(f"\n=== {name}: DDL missing in --after (model not regenerated?)"); continue
        if name not in before:
            print(f"\n=== {name}: new (no baseline)"); continue
        total += compare(name, before[name], after[name])
    print(f"\n{'NO REGRESSIONS' if not total else str(total) + ' REGRESSION(S)'}")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
