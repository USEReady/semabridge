#!/usr/bin/env python3
"""
test_alias_consistency.py - READ-ONLY checks that SemaBridge names every table alias with ONE rule.
It only imports and calls functions and reads files; it never patches or writes SemaBridge code.

Three checks (run all by default, or pick one):
  helpers  Discovers every alias/sanitize helper in SemaBridge (by name pattern) and runs them on tricky table
           names (spaces, &, %, $, -, dots, brackets, leading digits, reserved words, accents, case variants, long
           names). PASS when all helpers that name TABLES agree (case-insensitively) for every name, and no two
           DIFFERENT table names collapse to the same alias.
  scan     Lists every place in src/ that builds an alias: calls to the helpers found above plus ad-hoc patterns
           (re.sub(...) on a table/alias variable, .lower()/.upper() on a table name). PASS when alias building
           only happens through ONE helper (ad-hoc sites are listed for review).
  ddl      For generated DDL files: every alias used in RELATIONSHIPS, DIMENSIONS and METRICS must be declared
           in TABLES exactly; a reference that matches a declared table only after normalisation (e.g.
           opportunityforecastadjustment vs OPPORTUNITY_FORECAST_ADJUSTMENT) is a naming mismatch.

usage:
  uv run python test_alias_consistency.py --repo .                                  # helpers + scan + ddl (output/debug)
  uv run python test_alias_consistency.py --repo . --check helpers
  uv run python test_alias_consistency.py --repo . --check ddl --ddl "output/debug/*.sql"
Exit code 1 if any check fails.
"""
import argparse, glob, importlib, inspect, os, re, sys, unicodedata
from collections import defaultdict

TRICKY = [
    "Opportunity Forecast Adjustment", "opportunity forecast adjustment", "OPPORTUNITY_FORECAST_ADJUSTMENT",
    "Sales & Returns", "Sales and Returns", "% Return Rate", "Return Rate %", "Unit Price ($)", "Cost per Unit (%)",
    "Holes & Nums A8", "Dim-Date", "Dim Date", "Dim_Date", "Fact.Sales", "[Measures]", "2020 Targets", "Date",
    "Value", "Order", "Año Fiscal", "Café Sales", "  Leading Space", "Trailing Space ", "Double  Space",
    "A" * 120, "Calendar Ship Date", "KPI", "_Measures", "Key Metrics",
]
HELPER_NAME = re.compile(r"(alias|sanitiz|clean_ident|safe_ident|quote_ident|identifier)", re.I)
MODULES = ["semabridge.utils.naming", "semabridge.utils.identifiers", "semabridge.connectors.table_binding_resolver",
           "semabridge.connectors.ddl_helpers", "semabridge.connectors.tables_clause_builder",
           "semabridge.converter.dax_ast_parser", "semabridge.converter.dax_translator"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------------------- helpers
def discover_helpers():
    found = {}
    for mod in MODULES:
        try:
            m = importlib.import_module(mod)
        except Exception as exc:
            print(f"  (cannot import {mod}: {str(exc)[:80]})")
            continue
        for name, obj in vars(m).items():
            if not HELPER_NAME.search(name) or name.startswith("__"):
                continue
            if inspect.isfunction(obj) and obj.__module__ == m.__name__:
                found[f"{mod}.{name}"] = obj
            elif inspect.isclass(obj) and obj.__module__ == m.__name__:
                try:
                    inst = obj()
                except Exception:
                    try:
                        inst = obj(force_uppercase=True)
                    except Exception:
                        continue
                for meth in dir(inst):
                    if HELPER_NAME.search(meth) and not meth.startswith("_") and callable(getattr(inst, meth)):
                        found[f"{mod}.{name}().{meth}"] = getattr(inst, meth)
    return found


def call_one(fn, name):
    try:
        sig = inspect.signature(fn)
        required = [p for p in sig.parameters.values()
                    if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if len(required) != 1:
            return None
        out = fn(name)
        return out if isinstance(out, str) else None
    except Exception as exc:
        return f"<error {type(exc).__name__}>"


def check_helpers():
    print("\n=== helpers: does every alias helper produce the same name? ===")
    helpers = discover_helpers()
    if not helpers:
        print("  no helpers importable (run with --repo <semabridge root>)"); return False
    results = {h: {t: call_one(fn, t) for t in TRICKY} for h, fn in helpers.items()}
    usable = {h: r for h, r in results.items() if any(v is not None for v in r.values())}
    print(f"  helpers taking one string: {len(usable)}")
    for h in usable:
        print(f"    - {h}")
    # group helpers by behaviour on the tricky names
    groups = defaultdict(list)
    for h, r in usable.items():
        groups[tuple(str(r[t]).upper() if r[t] else "" for t in TRICKY)].append(h)
    ok = True
    if len(groups) > 1:
        ok = False
        print(f"\n  FAIL: {len(groups)} different naming rules (helpers in the same group agree):")
        for i, (sig, hs) in enumerate(groups.items(), 1):
            print(f"    rule {i}: {', '.join(h.split('.')[-1] for h in hs)}")
        print("\n  table name".ljust(40) + " | ".join(f"rule {i}" for i in range(1, len(groups) + 1)))
        reps = [hs[0] for hs in groups.values()]
        for t in TRICKY:
            vals = [str(usable[h][t]) for h in reps]
            if len({v.upper() for v in vals}) > 1:
                print(f"  {t[:36]:38} " + " | ".join(v[:34] for v in vals))
    else:
        print("  PASS: all helpers apply the same rule")
    # collisions within each rule
    for i, (sig, hs) in enumerate(groups.items(), 1):
        coll = defaultdict(list)
        for t, v in zip(TRICKY, sig):
            if v and not v.startswith("<ERROR"):
                coll[v].append(t)
        same = lambda x: re.sub(r"[\s_]+", " ", x.strip().lower())     # case / spaces / underscores = same name
        real = {v: ts for v, ts in coll.items() if len({same(x) for x in ts}) > 1}
        if real:
            print(f"\n  rule {i}: different table names that collapse to ONE alias (collisions):")
            for v, ts in real.items():
                print(f"    {v[:40]:42} <- {ts}")
    return ok


# ---------------------------------------------------------------------------- scan
ADHOC = [
    (re.compile(r"re\.sub\([^)]*\[\^A-Za-z0-9_?\]"), "ad-hoc regex identifier cleaning"),
    (re.compile(r"\b(table|tbl|alias|dataset|dim)\w*\s*\.\s*(lower|upper)\s*\(\s*\)\s*\.\s*replace\("), "ad-hoc lower/upper + replace on a table name"),
    (re.compile(r"\b(table|tbl|alias|dataset|dim)\w*\.replace\(\s*['\"] ['\"]"), "ad-hoc space replacement on a table name"),
]


def check_scan(repo):
    print("\n=== scan: where are table aliases built? ===")
    src = os.path.join(repo, "src")
    if not os.path.isdir(src):
        print(f"  no src/ under {repo}"); return False
    helper_names = {h.split(".")[-1].split("(")[0] for h in discover_helpers()} or {"to_alias", "sanitize_column", "inactive_alias"}
    call_re = re.compile(r"\b(" + "|".join(sorted(map(re.escape, helper_names))) + r")\s*\(")
    calls, adhoc = defaultdict(lambda: defaultdict(int)), []
    define_re = re.compile(r"^\s*(def\s+(" + "|".join(sorted(map(re.escape, helper_names))) + r")\b|class\s+\w*Sanitiz)", re.M)
    for path in glob.glob(os.path.join(src, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, repo)
        text = open(path, encoding="utf-8", errors="ignore").read()
        helper_home = bool(define_re.search(text))       # the helper's own module may clean identifiers
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in call_re.finditer(line):
                if not re.search(rf"\bdef\s+{re.escape(m.group(1))}\b", line):
                    calls[m.group(1)][rel] += 1
            for pat, why in ADHOC:
                if not helper_home and pat.search(line):
                    adhoc.append((rel, n, why, line.strip()[:110]))
    print("  helper calls (function -> files):")
    for fn, files in sorted(calls.items()):
        print(f"    {fn:28} {sum(files.values()):4} calls in {len(files)} files: " + ", ".join(sorted(os.path.basename(f) for f in files))[:150])
    if adhoc:
        print(f"\n  REVIEW: {len(adhoc)} ad-hoc identifier-building lines (should call the one helper):")
        for rel, n, why, line in adhoc[:40]:
            print(f"    {rel}:{n}  [{why}]\n        {line}")
    table_rules = [fn for fn in calls if re.search(r"alias", fn, re.I)]
    ok = len(table_rules) <= 1 and not adhoc
    print(f"\n  {'PASS' if ok else 'FAIL'}: {len(table_rules)} alias-building helper(s) in use"
          + (f" ({', '.join(table_rules)})" if table_rules else "") + f", {len(adhoc)} ad-hoc site(s)")
    return ok


# ---------------------------------------------------------------------------- ddl
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


def check_ddl(pattern):
    print("\n=== ddl: are all aliases used in the view declared exactly in TABLES? ===")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"  no DDL files match {pattern}"); return True
    all_ok = True
    for f in files:
        text = open(f, encoding="utf-8", errors="ignore").read()
        if not re.search(r"\bsemantic\s+view\b", text, re.I):
            continue
        declared = set()
        for t in _split(_block(text, "tables")):
            m = re.match(r'(?:("?[\w$]+"?)\s+as\s+)?([\w.\"$]+)', t, re.I)
            if m:
                declared.add((m.group(1) or m.group(2).split(".")[-1]).strip('"'))
        used = defaultdict(set)
        for r in _split(_block(text, "relationships")):
            for m in re.finditer(r'(?:as\s+\w+\s*)?\b("?[\w$]+"?)\s*\(', r, re.I):
                name = m.group(1).strip('"')
                if name.lower() not in ("as", "references"):
                    used[name].add("relationships")
        for sect in ("dimensions", "facts", "metrics"):
            body = re.sub(r"'(?:[^']|'')*'", "''", _block(text, sect))
            for m in re.finditer(r'(?<![\w."])("?[A-Za-z_][\w$]*"?)\s*\.\s*"?[A-Za-z_]', body):
                used[m.group(1).strip('"')].add(sect)
        dec_exact = {d.upper() for d in declared}
        dec_norm = {norm(d): d for d in declared}
        problems = []
        for u, where in used.items():
            if u.upper() in dec_exact:
                continue
            if norm(u) in dec_norm:
                problems.append(f"MISMATCH  {u} used in {'/'.join(sorted(where))}, declared as {dec_norm[norm(u)]}")
            elif u.upper() not in {"CASE", "WHEN", "THEN", "ELSE", "END"}:
                problems.append(f"UNDECLARED {u} used in {'/'.join(sorted(where))}")
        status = "PASS" if not problems else "FAIL"
        all_ok &= not problems
        print(f"  {status}  {os.path.basename(f)}  ({len(declared)} tables, {len(used)} aliases used)")
        for p in problems[:15]:
            print(f"        {p}")
    return all_ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--check", choices=["helpers", "scan", "ddl", "all"], default="all")
    ap.add_argument("--ddl", default=os.path.join("output", "debug", "*.sql"))
    args = ap.parse_args()
    sys.path.insert(0, os.path.join(os.path.abspath(args.repo), "src"))
    ok = True
    if args.check in ("helpers", "all"):
        ok &= check_helpers()
    if args.check in ("scan", "all"):
        ok &= check_scan(os.path.abspath(args.repo))
    if args.check in ("ddl", "all"):
        ok &= check_ddl(args.ddl if os.path.isabs(args.ddl) else os.path.join(os.path.abspath(args.repo), args.ddl))
    print(f"\n{'ALL CHECKS PASS' if ok else 'SOME CHECKS FAILED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
