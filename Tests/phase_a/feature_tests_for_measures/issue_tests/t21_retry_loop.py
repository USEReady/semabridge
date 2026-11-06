"""Issue: deploy retry loop removes one failing measure per attempt (often without a reason).
Expect: the run log has no 'pass N: fixing' lines; optional --src scan finds no auto-remediation loop."""
import os, re
from common import Result, cli
NAME = "T21 deploy retry loop"
def check(sv, pbix, args):
    r = Result(NAME)
    if args.log:
        txt = open(args.log, encoding="utf-8", errors="replace").read()
        passes = re.findall(r"pass\s+(\d+):\s+fixing invalid identifier\s+'([^']+)'", txt, re.I)
        if passes: r.fail(f"{len(passes)} retry passes; metrics removed: {[p[1] for p in passes][:10]}")
        else: r.ok("no retry passes in the log")
    if args.src:
        for root, _, files in os.walk(args.src):
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(root, f)
                    s = open(p, encoding="utf-8", errors="replace").read()
                    if re.search(r"auto-remediate|_remediated\s*=|pass\s*\{_pass", s, re.I):
                        r.fail(f"retry/remediation code still present: {p}")
    if not r.items: r.info("give --log and/or --src")
    return r
if __name__ == "__main__":
    cli(check, NAME, extra=[(["--log"], {}), (["--src"], {})])
