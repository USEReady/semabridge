"""O05 - Cross-domain collisions are detected (and only real ones).

    uv run python ontology_tests/o05_collisions.py
    uv run python ontology_tests/o05_collisions.py --osi-a <a.json> --osi-b <b.json>   (two real projects)

Part 1: synthetic domains (same metric name, different formula -> collision; first domain wins).
Part 2 (optional): generate domains from two real projects and list the collisions this creates.
Generic table names shared by many models (Accounts, Date, Products...) will collide; that is
the case Generate-from-OSI hits in practice.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text, unique  # noqa: E402

p = base_args(__doc__)
p.add_argument("--osi-a"); p.add_argument("--osi-b")
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report("O05 collisions")
tag = unique("x")[-6:]
metric = f"Total Units {tag}"
d1, d2, d3 = f"TEST Coll A {tag}", f"TEST Coll B {tag}", f"TEST Coll C {tag}"


def dom(name, formula=None, maps=None):
    s = f"domain: {name}\nmetrics:\n  - name: {metric}\n"
    if formula:
        s += f"    sql_expression: \"{formula}\"\n"
    if maps:
        s += f"    maps_to: [{maps}]\n"
    return s.encode()


base_coll = len(api.get("/api/ontology/domains").json().get("collisions", []))
r = api.upload([("a.yaml", dom(d1, formula="SUM(Units)")), ("b.yaml", dom(d2, formula="SUM(Units) * 2"))])
rep.check(r.ok, "upload two domains with the same metric", str(r.status_code))
colls = [c for c in r.json().get("collisions", []) if metric.casefold() in json.dumps(c).casefold()]
rep.check(len(colls) >= 1, "same name + different formula -> collision reported", json.dumps(colls)[:300])
fed_metric = None
for dname in (d1, d2):
    g = api.get(f"/api/ontology/domains/{dname}")
    rep.check(g.ok, f"{dname} registered")

r3 = api.upload([("c.yaml", dom(d3, maps="Total Units"))])
colls3 = [c for c in r3.json().get("collisions", []) if metric.casefold() in json.dumps(c).casefold()]
if colls3:
    sev = {c.get("severity") for c in colls3}
    rep.warn("formula vs maps_to-only definition also collides", f"severity {sev}: decide if a binding-only "
             "definition should be ERROR (screenshot: 'Total Units' SUM(Units)*1 vs N/A)")

for n in (d1, d2, d3):
    api.delete_domain_quiet(n)
after = len(api.get("/api/ontology/domains").json().get("collisions", []))
rep.check(after == base_coll, "collisions disappear after the test domains are deleted", f"{base_coll} -> {after}")

if a.osi_a and a.osi_b:
    names = []
    for i, f in enumerate((a.osi_a, a.osi_b)):
        n = f"TEST Proj {i} {tag}"; names.append(n)
        osi = json.loads(Path(f).read_text(encoding="utf-8"))
        rr = api.post("/api/ontology/domains/from-osi", {"osi_model": osi, "domain_name": n, "save_to_disk": False})
        rep.check(rr.ok, f"generate domain from {Path(f).parent.name}", f"{rr.status_code} {'' if rr.ok else body_text(rr)}")
    allc = api.get("/api/ontology/domains").json().get("collisions", [])
    mine = [c for c in allc if all(n in json.dumps(c) for n in names)]
    print(f"  {len(mine)} collisions between the two generated domains:")
    for c in mine[:30]:
        print("   -", c.get("name") or c.get("item_name"), c.get("collision_type") or c.get("type"), c.get("severity"))
    rep.warn("generated domains collide on shared table/measure names", f"{len(mine)} collisions") if mine else rep.ok("no collisions between the two projects")
    for n in names:
        api.delete_domain_quiet(n)
sys.exit(rep.done())
