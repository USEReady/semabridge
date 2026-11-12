"""G05 - Fake / demo data in the governance and reconciliation code paths (offline, read-only).

    uv run python governance_tests/g05_fake_data_scan.py

Looks only at code that calls the governance / reconciliation / verified-query APIs, and flags:
  F1  literal result rows (arrays of objects with numbers) sent to /verify instead of real query results
  F2  'scenario' switches that decide the outcome (certified / drift)
  F3  non-empty default values for user inputs (metric, expression, table, dimensions)
  F4  random or simulated numbers (Math.random, faker, simulated=true)
  F5  backend governance/reconciliation modules with hard-coded business words
Exit code 1 when anything is found.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Report  # noqa: E402

rep = Report("G05 fake data scan")
root = Path.cwd()
API_RX = re.compile(r"/api/(reconciliation|governance)|verified[-_]?quer", re.I)
front = [p for p in (root / "frontend" / "src").rglob("*.js*") if "node_modules" not in str(p)] if (root / "frontend").exists() else []
back = list((root / "src" / "semabridge" / "governance").glob("*.py")) + \
    list((root / "src" / "semabridge" / "reconciliation").glob("*.py")) + \
    [p for p in (root / "src" / "semabridge" / "api" / "routers").glob("*.py") if re.search(r"governance|reconcil", p.name)] + \
    list((root / "src" / "semabridge" / "ai").glob("verified*.py"))

files = [f for f in front if API_RX.search(f.read_text(encoding="utf-8", errors="ignore"))]
print(f"  frontend files calling governance/reconciliation APIs: {[str(f.relative_to(root)) for f in files]}")
if not files:
    rep.warn("no frontend file calls the governance/reconciliation APIs (run from repo root?)")

for f in files:
    txt = f.read_text(encoding="utf-8", errors="ignore")
    rel = f.relative_to(root)
    lines = txt.splitlines()
    # F1 literal rows: object literals that contain a number value, inside an array
    lit_rows = [i + 1 for i, l in enumerate(lines)
                if re.search(r"\{\s*[\[\w'\"]+[\]'\"]?\s*:\s*['\"][^'\"]+['\"]\s*,\s*\w+\s*:\s*-?\d[\d_.]*\s*\}", l)]
    rep.check(not lit_rows, f"F1 {rel}: no literal result rows", f"{len(lit_rows)} rows at lines {lit_rows[:6]}")
    # F2 scenario switches
    scen = [i + 1 for i, l in enumerate(lines) if re.search(r"scenario|['\"]drift['\"]|Intentional", l, re.I)]
    rep.check(not scen, f"F2 {rel}: no outcome-deciding scenario switch", f"lines {scen[:6]}")
    # F3 non-empty defaults for recon/blast inputs
    defaults = [(i + 1, m.group(1), m.group(2)) for i, l in enumerate(lines)
                for m in [re.search(r"const\s*\[\s*(recon\w*|blast\w*|lint\w*)\s*,.*useState\(\s*'([^']+)'\s*\)", l)] if m]
    fixed = [d for d in defaults if not re.fullmatch(r"[A-Z_]+", d[2])]  # enum-like defaults (METRIC, DROP) are fine
    rep.check(not fixed, f"F3 {rel}: user inputs start empty", "; ".join(f"line {d[0]} {d[1]}='{d[2]}'" for d in fixed[:5]))
    # F4 random / simulated
    rnd = [i + 1 for i, l in enumerate(lines)
           if re.search(r"Math\.random|faker|is_?simulated\s*[:=]\s*true|allow_?simulated\s*[:=]\s*true", l, re.I)]
    rep.check(not rnd, f"F4 {rel}: no random/simulated values", f"lines {rnd[:6]}")

BUSINESS = r"revenue|margin|cogs|customer[_ ]tier|mid-market|\bsmb\b|fiscal_year|\bsales\b|profit"
for f in back:
    txt = f.read_text(encoding="utf-8", errors="ignore")
    hits = [(i + 1, l.strip()[:80]) for i, l in enumerate(txt.splitlines())
            if re.search(r"['\"][^'\"]*(" + BUSINESS + r")[^'\"]*['\"]", l, re.I) and not l.strip().startswith(("#", chr(34) * 3, chr(39) * 3)) and "e.g." not in l]
    rep.check(not hits, f"F5 {f.relative_to(root)}: no business words in literals", f"{hits[:3]}")
sys.exit(rep.done())
