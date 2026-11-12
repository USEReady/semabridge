"""O03 - Generate a domain from a project's OSI file and check nothing is lost.

Works even while 'from-osi by project_id' is broken: it sends the OSI JSON itself.

    uv run python ontology_tests/o03_generate_from_osi_file.py --project proj-artificial-intelligence-sample
    uv run python ontology_tests/o03_generate_from_osi_file.py --osi output/inferred/<id>/osi_inferred.json
    add --keep to leave the domain registered (default: deleted after the test)

Checks (generic, any model):
  concepts == datasets, attributes == columns, metrics == metrics,
  relationships == distinct dataset pairs (and warns when several BI relationships
  between the same two tables collapse into one), no invented COUNT(*) formula,
  no collisions inside a single generated domain, YAML round-trips through /validate.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text, find_osi_file, unique  # noqa: E402
from urllib.parse import quote as _q
def requests_quote(s): return _q(s, safe="")

p = base_args(__doc__)
p.add_argument("--project", help="project id (to find output/inferred/<id>/osi_inferred.json)")
p.add_argument("--osi", help="explicit path to osi_inferred.json")
p.add_argument("--keep", action="store_true", help="keep the generated domain")
p.add_argument("--out", default="ontology_tests/out", help="where to save the generated YAML")
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report("O03 generate domain from OSI file")
repo = Path.cwd()

osi_path = Path(a.osi) if a.osi else (find_osi_file(repo, a.project) if a.project else None)
if not osi_path or not osi_path.exists():
    rep.fail("OSI file found", "pass --osi or --project (file is written when the project deploys)")
    sys.exit(rep.done())
osi = json.loads(osi_path.read_text(encoding="utf-8"))
rep.ok("OSI file loaded", str(osi_path))

datasets = osi.get("datasets", [])
metrics = osi.get("metrics", [])
rels = osi.get("relationships", [])
n_cols = sum(len(d.get("columns", [])) for d in datasets)
ds_keys = {d["unique_name"].casefold() for d in datasets}
pairs = Counter(f'{r["from_dataset"].casefold()}->{r["to_dataset"].casefold()}' for r in rels)
print(f"  OSI: {len(datasets)} datasets, {n_cols} columns, {len(metrics)} metrics, {len(rels)} relationships")

domain = unique("TEST OSI")
r = api.post("/api/ontology/domains/from-osi",
             {"osi_model": osi, "domain_name": domain, "save_to_disk": bool(a.keep)})
if not rep.check(r.ok, "POST from-osi with osi_model", f"{r.status_code} {body_text(r) if not r.ok else ''}"):
    sys.exit(rep.done())
res = r.json()

rep.check(res["concept_count"] == len(ds_keys), "one concept per dataset",
          f'{res["concept_count"]} concepts vs {len(ds_keys)} datasets')
rep.check(res["metric_count"] == len({m["unique_name"].casefold() for m in metrics}), "one metric per OSI metric",
          f'{res["metric_count"]} vs {len(metrics)}')
rep.check(res["relationship_count"] == len(pairs), "one relationship per dataset pair",
          f'{res["relationship_count"]} vs {len(pairs)} pairs')
multi = {k: v for k, v in pairs.items() if v > 1}
if multi:
    rep.warn("several BI relationships between the same tables collapse into one", json.dumps(multi))
own = [c for c in res.get("collisions", []) if domain in json.dumps(c)]
rep.check(not own, "no collisions inside the generated domain", json.dumps(own)[:300])

g = api.get("/api/ontology/domains/" + requests_quote(domain))
if rep.check(g.ok, "GET generated domain", str(g.status_code)):
    dom = g.json()
    dom = dom.get("parsed") or dom
    concepts = dom.get("concepts", [])
    dmetrics = dom.get("metrics", [])
    n_attrs = sum(len(c.get("attributes", [])) for c in concepts)
    rep.check(n_attrs == n_cols, "one attribute per column", f"{n_attrs} vs {n_cols}")
    osi_expr = {m["unique_name"].casefold(): (m.get("expression") or m.get("source_column")) for m in metrics}
    invented = [m["name"] for m in dmetrics
                if (m.get("sql_expression") or "").strip().upper() == "COUNT(*)" and not osi_expr.get(m["name"].casefold())]
    rep.check(not invented, "no invented COUNT(*) formulas for metrics without an expression",
              f"{len(invented)}: {invented[:8]}")
    bad_concepts = [c["name"] for c in concepts if c["name"].casefold() not in ds_keys]
    rep.check(not bad_concepts, "every concept is a real dataset", str(bad_concepts[:8]))

yaml_text = res.get("yaml_content", "")
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
yf = out / f"{(a.project or osi_path.parent.name)}.generated.ontology.yaml"
yf.write_text(yaml_text, encoding="utf-8")
rep.ok("generated YAML saved", str(yf))
v = api.post("/api/ontology/domains/validate", {"yaml_content": yaml_text, "original_domain": domain})
rep.check(v.ok and v.json().get("valid"), "generated YAML passes /validate (round trip)", body_text(v) if not (v.ok and v.json().get("valid")) else "")

if not a.keep:
    api.delete_domain_quiet(domain)
sys.exit(rep.done())
