#!/usr/bin/env python3
"""
audit_hardcoding.py - checks that the code you ADDED (git diff) contains no model-specific names.

It builds a list of every table, column and measure name from your real models (.pbix folder and/or the
measures report) plus the synthetic test fixture, then scans every line added under src/ since --base:

  HARD    a string literal equal to (or containing) a real table / column / measure name   -> exit 1
  REVIEW  code that looks name-specific: == "SomeName", startswith("SomeName"), lists/sets of
          capitalised names, or a literal containing a model name in its Snowflake form (e.g. "SALES.")
  INFO    the same names only inside comments / docstrings (harmless, listed for completeness)

  uv run python phase_a\\feature_tests\\audit_hardcoding.py --pbix-dir "C:\\Users\\manoj.s\\Downloads\\PBIX Files"
  uv run python phase_a\\feature_tests\\audit_hardcoding.py --base <commit> --report <measures report.xlsx>
Default --base = parent of the first commit whose message starts with "F1:" (i.e. everything since F1).
Combine with:  run_feature_tests.py --mutate <seed>  (same cases with every name changed must still pass)
"""
import argparse, csv, glob, os, re, subprocess, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

DAX_SQL_WORDS = set("""
abs all allexcept allselected and any_value as asc average averagex avg blank by calculate calculatetable case cast ceil
coalesce concatenate concat count counta countax countblank countrows countx date dateadd datediff datesytd day desc
distinct distinctcount divide else end eomonth false filter firstdate first floor format from hasonevalue hour if iff
in int is isblank iserror isfiltered isinscope keepfilters lastdate last left len lower max maxx min minute minx month
mround not now null nullif or parallelperiod previousmonth previousyear quarter rankx related relatedtable removefilters
return right round rounddown roundup row sameperiodlastyear search second selectedvalue sqrt sum sumx switch then today
totalmtd totalqtd totalytd trim true trunc try_to_number upper userelationship value values var week when where year
id key name type status category amount price qty quantity total units unit rate code label flag level text number
""".split())


def git(args, repo):
    return subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout


def default_base(repo):
    for line in git(["log", "--format=%H %s"], repo).splitlines():
        h, _, msg = line.partition(" ")
        if msg.startswith("F1:"):
            return h + "~1"
    return "HEAD~1"


def model_names(pbix_dir, report):
    names = {}
    if pbix_dir:
        from pbixray import PBIXRay
        for f in glob.glob(os.path.join(pbix_dir, "*.pbix")):
            try:
                db = PBIXRay(f)._metadata._meta._db
                src = os.path.basename(f)
                for q, kind in (("SELECT Name n FROM [Table]", "table"),
                                ("SELECT COALESCE(ExplicitName, InferredName) n FROM [Column]", "column"),
                                ("SELECT Name n FROM Measure", "measure")):
                    df = db.query(q)
                    for n in (df.n if df is not None and len(df) else []):
                        if isinstance(n, str) and not n.startswith(("H$", "R$", "U$", "LocalDateTable", "DateTableTemplate", "RowNumber")):
                            names.setdefault(n, f"{kind} in {src}")
            except Exception as exc:
                print(f"  (skipped {os.path.basename(f)}: {exc})")
    if report:
        sys.path.insert(0, os.path.dirname(HERE))
        from measure_inventory import read_report
        for r in read_report(report):
            names.setdefault(r["measure"], f"measure in report ({r['model']})")
            if r["table"]:
                names.setdefault(r["table"], f"table in report ({r['model']})")
            names.setdefault(r["model"], "model name")
    from fixture import TABLES
    import mutate
    for t, df in TABLES.items():
        names.setdefault(t, "test fixture table")
        for c in df.columns:
            names.setdefault(c, "test fixture column")
    for pool in (mutate.TABLE_POOL, mutate.COLUMN_POOL):
        for vals in pool.values():
            for v in vals:
                names.setdefault(v, "test rename pool")
    # drop names that are generic programming / DAX words or too short to be meaningful
    return {n: why for n, why in names.items() if len(n.strip()) >= 3 and n.strip().lower() not in DAX_SQL_WORDS}


def added_lines(repo, base, paths):
    diff = git(["diff", "-U0", base, "--"] + paths, repo)
    f, ln = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            f = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line); ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++") and f:
            yield f, ln, line[1:]; ln += 1


LIT = re.compile(r"""(?P<q>'''|\"\"\"|'|")(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)""")
REVIEW = [
    (re.compile(r"""==\s*(['"])[A-Z][\w &%$()\-]{2,}\1"""), "equality with a capitalised literal"),
    (re.compile(r"""\.(?:startswith|endswith)\(\s*(['"])[A-Z][\w &%$()\-]{2,}\1"""), "startswith/endswith a capitalised literal"),
    (re.compile(r"""(?:in|=)\s*[\[\({]\s*(['"])[A-Z][\w &%$()\-]{2,}\1\s*,\s*(['"])[A-Z]"""), "list/set of capitalised names"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="."); ap.add_argument("--base"); ap.add_argument("--paths", nargs="+", default=["src"])
    ap.add_argument("--pbix-dir"); ap.add_argument("--report"); ap.add_argument("--out", default="hardcoding_audit.csv")
    args = ap.parse_args()
    base = args.base or default_base(args.repo)
    names = model_names(args.pbix_dir, args.report)
    lower = {n.lower(): (n, why) for n, why in names.items()}
    multi = [(n.lower(), n, why) for n, why in names.items() if re.search(r"[\s&%$()\-]", n)]
    from relationship_inventory import sb_column
    sanitized = {}
    for n, why in names.items():
        sn = sb_column(n).upper()
        if len(sn) >= 4 and sn.lower() not in DAX_SQL_WORDS:
            sanitized.setdefault(sn, (n, why))
    print(f"Base: {base}   names checked: {len(names)}   paths: {' '.join(args.paths)}")

    hits = []
    for f, ln, text in added_lines(args.repo, base, args.paths):
        code, _, comment = text.partition("#")
        in_comment_only = text.strip().startswith(("#", '"""', "'''")) or text.strip().startswith("*")
        for m in LIT.finditer(code):
            s = m.group("s"); sl = s.lower().strip()
            found = lower.get(sl)
            if not found:
                found = next(((n, why) for ml, n, why in multi if len(ml) >= 5 and re.search(rf"(?<![a-z0-9]){re.escape(ml)}(?![a-z0-9])", sl)), None)
            if found:
                hits.append(("INFO" if in_comment_only else "HARD", f, ln, f'"{s[:60]}" = {found[0]!r} ({found[1]})', text.strip()))
            elif not in_comment_only:
                for tok in re.findall(r"[A-Z][A-Z0-9_]{3,}", s):
                    if tok in sanitized:
                        n, why = sanitized[tok]
                        hits.append(("REVIEW", f, ln, f'"{s[:60]}" contains {tok} = Snowflake name of {n!r} ({why})', text.strip()))
        for rx, why in REVIEW:
            if rx.search(code) and not in_comment_only:
                hits.append(("REVIEW", f, ln, why, text.strip()))
        for n, why in ((lower[w][0], lower[w][1]) for w in re.findall(r"[A-Za-z][\w ]{2,}", comment.lower()) if w.strip() in lower):
            hits.append(("INFO", f, ln, f"comment mentions {n!r} ({why})", text.strip()))

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["level", "file", "line", "finding", "code"]); w.writerows(hits)
    for level in ("HARD", "REVIEW", "INFO"):
        rows = [h for h in hits if h[0] == level]
        print(f"\n{level}: {len(rows)}")
        for _, f, ln, why, code in rows[:60]:
            print(f"  {f}:{ln}  {why}\n      {code[:150]}")
    hard = sum(1 for h in hits if h[0] == "HARD")
    print(f"\n{'FAIL' if hard else 'PASS'}: {hard} hardcoded name(s) in added code. Full list: {os.path.abspath(args.out)}")
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
