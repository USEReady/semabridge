"""Run the governance test scripts.

    uv run python governance_tests/run_all.py
    uv run python governance_tests/run_all.py --view DB.SCHEMA.VIEW --expected governance_tests/fixtures/my_values.json
"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).parent
args = sys.argv[1:]


def opt(n):
    return args[args.index(n) + 1] if n in args else None


common = []
for n in ("--base", "--token"):
    if opt(n):
        common += [n, opt(n)]
steps = [["g05_fake_data_scan.py"], ["g01_linter_rules.py"], ["g02_blast_radius.py"],
         ["g03_recon_verify.py"], ["g04_recon_query_generation.py"]]
if opt("--view") and opt("--expected"):
    steps.append(["g06_recon_real.py", "--view", opt("--view"), "--expected", opt("--expected")]
                 + (["--project", opt("--project")] if opt("--project") else []))
res = []
for s in steps:
    extra = [] if s[0] == "g05_fake_data_scan.py" else common
    res.append((s[0], subprocess.call([sys.executable, str(here / s[0]), *s[1:], *extra])))
print("\n=== SUMMARY ===")
for n, rc in res:
    print(f"  {'PASS' if rc == 0 else 'FAIL'}  {n}")
sys.exit(1 if any(rc for _, rc in res) else 0)
