"""O07 - Attach domains to a project, preview, graph, knowledge-graph SQL.

    uv run python ontology_tests/o07_project_attach_preview.py --project proj-artificial-intelligence-sample
    (add --osi <osi_inferred.json> to build the test domain from that project's real model)

Preview and knowledge-graph SQL load the project's OSI model the same way Generate-from-OSI does,
so they also fail (404) until _load_osi_model is fixed - that is reported, not hidden.
"""
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text, find_osi_file, unique  # noqa: E402

p = base_args(__doc__)
p.add_argument("--project", required=True)
p.add_argument("--osi")
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report("O07 project attach / preview / graph")
pid = a.project
path = f"/api/ontology/projects/{quote(pid, safe='')}"

before = api.get(f"{path}/domains")
rep.check(before.ok, "GET project domains", body_text(before)[:150])
orig = [d.get("id") or d.get("domain") for d in (before.json().get("domains") or [])] if before.ok else []

osi_file = Path(a.osi) if a.osi else find_osi_file(Path.cwd(), pid)
name = unique("TEST Attach")
if osi_file and osi_file.exists():
    osi = json.loads(osi_file.read_text(encoding="utf-8"))
    tables = [d["unique_name"] for d in osi["datasets"]][:2]
    measures = [m["unique_name"] for m in osi["metrics"]][:2]
else:
    rep.warn("no OSI file: using a synthetic domain (bindings will be 'unmatched')")
    tables, measures = ["NoSuchTable"], ["No Such Measure"]
y = f"domain: {name}\nconcepts:\n" + "".join(
    f"  - name: {t} Concept\n    synonyms: [{t} alias]\n    maps_to: [{t}]\n" for t in tables) + "metrics:\n" + "".join(
    f"  - name: {m} Biz\n    synonyms: [{m} alias]\n    maps_to: [{m}]\n" for m in measures) + \
    "  - name: Unbound Metric Biz\n    maps_to: [Measure That Does Not Exist]\n"
s = api.post("/api/ontology/domains", {"yaml_content": y})
rep.check(s.ok, "create test domain", body_text(s)[:150])

att = api.put(f"{path}/domains", {"domains": orig + [name]})
rep.check(att.ok, "attach domain to project", body_text(att)[:200])
bad = api.put(f"{path}/domains", {"domains": orig + ["no such domain zzz"]})
rep.check(bad.status_code == 422, "attaching an unknown domain is rejected", str(bad.status_code))

pv = api.post(f"{path}/preview")
if rep.check(pv.ok, "preview ontology against project model",
             f"{pv.status_code} {body_text(pv)[:200]}" + ("  (same loader bug as from-osi)" if pv.status_code == 404 else "")):
    txt = json.dumps(pv.json())
    rep.check("Measure That Does Not Exist" in txt or "Unbound Metric Biz" in txt,
              "unmatched binding is listed in the preview (not dropped quietly)")
    for t in tables:
        rep.check(t in txt, f"table binding '{t}' appears in preview")

gr = api.get(f"/api/ontology/domains/graph?project_id={quote(pid, safe='')}")
if rep.check(gr.ok, "connection graph for project"):
    gtxt = json.dumps(gr.json())
    rep.check(name in gtxt, "graph contains the attached domain")
    rep.check(len(gr.json().get("nodes", [])) > 0, "graph has nodes", str(len(gr.json().get("nodes", []))))

kg = api.get(f"{path}/knowledge-graph.sql")
if rep.check(kg.ok, "knowledge-graph SQL generated", f"{kg.status_code} {kg.text[:150] if not kg.ok else ''}"):
    sql = kg.text
    rep.check("ONTOLOGY_CONCEPTS" in sql.upper(), "SQL creates ONTOLOGY_CONCEPTS")
    out = Path("ontology_tests/out"); out.mkdir(parents=True, exist_ok=True)
    (out / f"{pid}.knowledge_graph.sql").write_text(sql, encoding="utf-8")
    rep.ok("SQL saved for review", str(out / f"{pid}.knowledge_graph.sql"))

restore = api.put(f"{path}/domains", {"domains": orig})
rep.check(restore.ok, "project attachments restored", str(orig))
api.delete_domain_quiet(name)
sys.exit(rep.done())
