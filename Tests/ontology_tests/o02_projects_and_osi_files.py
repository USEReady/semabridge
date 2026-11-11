"""O02 - Which project ids exist, and which have an OSI model on disk / via the API?

    uv run python ontology_tests/o02_projects_and_osi_files.py
    uv run python ontology_tests/o02_projects_and_osi_files.py --project proj-artificial-intelligence-sample

Explains the 'proj-artificial-intelligence-sample' vs 'proj-artificial-intelligence' mismatch:
the API project id is what Generate-from-OSI uses; output folders may be named differently.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text, find_osi_file  # noqa: E402

p = base_args(__doc__)
p.add_argument("--project", help="only this project id")
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report("O02 projects vs OSI files")
repo = Path.cwd()

r = api.get("/api/projects?limit=200&offset=0")
if not rep.check(r.ok, "GET /api/projects", f"{r.status_code}"):
    sys.exit(rep.done())
data = r.json()
items = data if isinstance(data, list) else (data.get("items") or data.get("projects") or [])
ids = [(it.get("project_id") or it.get("id"), it.get("name") or it.get("display_name")) for it in items]
if a.project:
    ids = [x for x in ids if x[0] == a.project] or [(a.project, "?")]

out_dirs = sorted(d.name for d in (repo / "output" / "inferred").glob("*") if d.is_dir()) if (repo / "output" / "inferred").exists() else []
print(f"  output/inferred folders: {out_dirs}")

for pid, name in ids:
    f = find_osi_file(repo, pid)
    if f:
        rep.ok(f"{pid} ({name}) has OSI file", str(f.relative_to(repo)))
    else:
        near = [d for d in out_dirs if d in pid or pid in d]
        rep.warn(f"{pid} ({name}) has no output/inferred/{pid}/osi_inferred.json",
                 f"similar folders: {near}" if near else "run the project once (deploy writes this file)")
    pr = api.post("/api/ontology/domains/from-osi", {"project_id": pid, "save_to_disk": False,
                                                    "domain_name": f"__probe {pid}"})
    if pr.ok:
        rep.ok(f"{pid}: from-osi by project_id works", f"{pr.json().get('concept_count')} concepts")
        api.delete_domain_quiet(f"__probe {pid}")
    else:
        rep.fail(f"{pid}: from-osi by project_id", f"{pr.status_code} {body_text(pr)}")

sys.exit(rep.done())
