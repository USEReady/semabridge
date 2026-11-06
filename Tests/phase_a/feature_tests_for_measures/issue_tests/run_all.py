"""Run every issue test on one model.
    python run_all.py --ddl view.sql --pbix model.pbix [--log run.log] [--src path/to/src]
                      [--ddl2 other_run.sql] [--converted N] [--limit 10000]
Tests that need a .pbix are skipped without it. Exit code 1 if any test FAILs."""
import argparse, importlib, glob, os, sys, inspect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_ddl, load_pbix

ap = argparse.ArgumentParser()
ap.add_argument("--ddl", required=True); ap.add_argument("--pbix")
ap.add_argument("--log"); ap.add_argument("--src"); ap.add_argument("--ddl2")
ap.add_argument("--converted", type=int); ap.add_argument("--limit", type=int, default=10000)
ap.add_argument("--only", help="comma-separated test prefixes, e.g. t05,t16")
ap.add_argument("--quiet", action="store_true", help="summary only")
args = ap.parse_args()

sv = load_ddl(args.ddl)
pb = load_pbix(args.pbix) if args.pbix else None
here = os.path.dirname(os.path.abspath(__file__))
summary = []
for path in sorted(glob.glob(os.path.join(here, "t[0-9][0-9]_*.py"))):
    mod_name = os.path.basename(path)[:-3]
    if args.only and not any(mod_name.startswith(p.strip()) for p in args.only.split(",")): continue
    mod = importlib.import_module(mod_name)
    needs_pbix = "needs_pbix=True" in inspect.getsource(mod)
    if needs_pbix and pb is None:
        summary.append((mod_name, "SKIP", "needs --pbix")); continue
    try:
        res = mod.check(sv, pb, args)
    except Exception as e:
        summary.append((mod_name, "ERROR", f"{type(e).__name__}: {e}")); continue
    if not args.quiet: res.show()
    n = sum(1 for s, _ in res.items if s == res.status)
    summary.append((mod_name, res.status, f"{n} item(s)"))

print(f"\n================ SUMMARY  view={sv.name}  tables={len(sv.tables)}  dims={len(sv.dims)}  metrics={len(sv.metrics)}")
for n, s, m in summary:
    print(f"  {s:5}  {n:40} {m}")
sys.exit(1 if any(s in ("FAIL", "ERROR") for _, s, _ in summary) else 0)
