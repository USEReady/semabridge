"""Hard-coding guard (read-only): finds model names and business words written into product code.

Builds the "forbidden" list from the models themselves, so it never needs updating:
  * every table, column and measure name found in output/reverse/*/sml/model.yaml (and --sml files)
  * every concept / metric / synonym in Config/ontologies/*.ontology.yaml
  * a small list of business words (override with --words)
then scans string literals (Python AST, so comments are ignored) in the source tree.

    uv run python ontology_tests/t_no_hardcoding.py                       # scan src/semabridge
    uv run python ontology_tests/t_no_hardcoding.py --changed             # only files changed vs origin branch
    uv run python ontology_tests/t_no_hardcoding.py --paths src/semabridge/ontology src/semabridge/ai

Exit code 1 when anything is found. Allowed places: tests, fixtures, docs, demo seed blocks marked
with  # hardcoding-ok: <reason>  on the same line.
"""
import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

import yaml

BUSINESS_WORDS = [
    "revenue", "sales", "profit", "margin", "close %", "win %", "win rate", "close rate", "escalat", "csat",
    "churn", "pipeline", "forecast", "customer", "opportunit", "units sold", "returns", "headcount",
    "enterprise north america", "emea", "apac", "latam",
]
GENERIC_OK = {  # technical words that legitimately appear in code
    "id", "name", "date", "year", "month", "day", "value", "type", "status", "key", "count", "sum", "avg",
    "min", "max", "total", "label", "description", "table", "column", "measure", "metric", "metrics", "level", "sort",
    "order", "index", "code", "rank", "category", "amount", "number", "price", "cost", "rate", "score",
    "model", "models", "root", "owner", "account", "empty", "entity", "entities", "period", "custom", "updated",
    "path", "store", "agent", "calculations",
    # DAX / SQL keywords and date parts that are also common column names
    "blank", "true", "false", "null", "all", "filter", "week", "weekday", "quarter", "hour", "minute",
    "second", "time", "datetime", "timestamp", "text", "string", "boolean", "values", "select", "from",
    "group", "where", "having", "limit", "distinct", "join", "on", "as",
}

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--paths", nargs="*", default=[
    "src/semabridge/ontology", "src/semabridge/ai", "src/semabridge/api/routers/ai_router.py",
    "src/semabridge/api/routers/domain_ontology_router.py", "src/semabridge/connectors/ontology_emitter.py"],
    help="default: ontology + AI code; use --paths src/semabridge for everything")
ap.add_argument("--sml", nargs="*", default=[])
ap.add_argument("--words", nargs="*", default=None, help="replace the business word list")
ap.add_argument("--changed", action="store_true", help="only .py files changed vs --base-ref")
ap.add_argument("--base-ref", default="origin/integrate/relationship-direction-fixes")
ap.add_argument("--min-len", type=int, default=4)
a = ap.parse_args()
root = Path.cwd()

# ------------------------------------------------------------------ forbidden names from the models
names = set()
sml_files = [Path(p) for p in a.sml] + list((root / "output" / "reverse").glob("*/sml/model.yaml"))
for f in sml_files:
    try:
        m = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception:
        continue
    for d in m.get("datasets") or []:
        names.add(d.get("unique_name"))
        for c in d.get("columns") or []:
            names.add(c.get("unique_name"))
    for x in m.get("metrics") or []:
        names.update([x.get("unique_name"), x.get("label"), x.get("original_name")])
for f in (root / "Config" / "ontologies").glob("*.yaml"):
    try:
        o = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception:
        continue
    for c in o.get("concepts") or []:
        names.update([c.get("name"), *(c.get("synonyms") or [])])
    for x in o.get("metrics") or []:
        names.update([x.get("name"), x.get("label"), *(x.get("synonyms") or [])])
names = {n.strip() for n in names if isinstance(n, str) and len(n.strip()) >= a.min_len
         and n.strip().casefold() not in GENERIC_OK}
words = [w.casefold() for w in (a.words if a.words is not None else BUSINESS_WORDS)]
name_re = {n: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(n.casefold()) + r"(?![A-Za-z0-9_])") for n in names}
print(f"forbidden model names: {len(names)} (from {len(sml_files)} SML files + Config/ontologies); business words: {len(words)}")

# ------------------------------------------------------------------ files to scan
if a.changed:
    out = subprocess.run(["git", "diff", "--name-only", a.base_ref, "--", *a.paths], capture_output=True, text=True)
    files = [root / p for p in out.stdout.split() if p.endswith(".py") and (root / p).exists()]
    out2 = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", *a.paths], capture_output=True, text=True)
    files += [root / p for p in out2.stdout.split() if p.endswith(".py")]
else:
    files = [p for base in map(Path, a.paths) for p in ([base] if base.is_file() else base.rglob("*.py"))]
files = [f for f in files if not re.search(r"(^|[\\/])(tests?|fixtures|docs)([\\/]|$)", str(f), re.I)]

# ------------------------------------------------------------------ scan string literals
hits = []
for f in files:
    try:
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        continue
    lines = src.splitlines()
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                doc_nodes.add(id(first.value))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in doc_nodes:
            continue
        s = node.value
        line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
        if "hardcoding-ok" in line or "logger." in line or len(s) < 3:
            continue
        low = s.casefold()
        found = [n for n, rx in name_re.items() if rx.search(low)]
        found += [w for w in words if w in low]
        if found:
            hits.append((str(f.relative_to(root)) if f.is_absolute() else str(f), node.lineno, sorted(set(found))[:4], s[:70]))

for path, ln, what, s in sorted(hits):
    print(f"  {path}:{ln}  {what}  ->  {s!r}")
print(f"--- {len(hits)} hard-coded literal(s) in {len(files)} file(s)")
sys.exit(1 if hits else 0)
