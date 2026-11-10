"""O06 - Validate (no save), save, edit, rename, delete a domain.

    uv run python ontology_tests/o06_validate_save_rename.py
"""
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text, unique  # noqa: E402

a = base_args(__doc__).parse_args()
api = Api(a.base, a.token)
rep = Report("O06 validate / save / rename / delete")
q = lambda s: quote(s, safe="")  # noqa: E731
name = unique("TEST Life")
new_name = name + " Renamed"
yaml1 = f"domain: {name}\nconcepts:\n  - name: Thing\n    synonyms: [Item]\nmetrics:\n  - name: Thing Count\n    concept: Thing\n"

v = api.post("/api/ontology/domains/validate", {"yaml_content": yaml1})
rep.check(v.ok and v.json().get("valid"), "validate: valid YAML", body_text(v)[:150])
rep.check(api.get(f"/api/ontology/domains/{q(name)}").status_code == 404, "validate does not save")

bad = api.post("/api/ontology/domains/validate", {"yaml_content": "concepts: []\n"})
rep.check(bad.ok and not bad.json().get("valid") and bad.json().get("errors"), "validate: invalid YAML returns errors, 200",
          body_text(bad)[:150])

s = api.post("/api/ontology/domains", {"yaml_content": yaml1})
rep.check(s.ok and s.json().get("success"), "save new domain", body_text(s)[:150])
g = api.get(f"/api/ontology/domains/{q(name)}")
dom_id = g.json().get("id") if g.ok else None
rep.check(g.ok and g.json().get("concept_count") == 1 and g.json().get("metric_count") == 1, "saved domain readable with counts")

yaml2 = yaml1.replace("synonyms: [Item]", "synonyms: [Item, Object]")
u = api.put(f"/api/ontology/domains/{q(name)}", {"yaml_content": yaml2})
g2 = api.get(f"/api/ontology/domains/{q(name)}")
syns = g2.json()["parsed"]["concepts"][0]["synonyms"] if g2.ok else []
rep.check(u.ok and "Object" in syns, "edit is saved", str(syns))

yaml3 = yaml2.replace(f"domain: {name}", f"domain: {new_name}")
rn = api.put(f"/api/ontology/domains/{q(name)}", {"yaml_content": yaml3})
rep.check(rn.ok and rn.json().get("renamed_from") == name, "rename reported", body_text(rn)[:150])
rep.check(api.get(f"/api/ontology/domains/{q(name)}").status_code == 404, "old name gone (rename, not copy)")
g3 = api.get(f"/api/ontology/domains/{q(new_name)}")
rep.check(g3.ok and g3.json().get("id") == dom_id, "id kept across rename", f"{dom_id} -> {g3.json().get('id') if g3.ok else None}")

missing = api.put(f"/api/ontology/domains/{q(unique('TEST none'))}", {"yaml_content": yaml1})
rep.check(missing.status_code == 404, "PUT unknown domain -> 404")

d = api.delete(f"/api/ontology/domains/{q(new_name)}")
rep.check(d.ok, "delete domain")
rep.check(api.delete(f"/api/ontology/domains/{q(new_name)}").status_code == 404, "delete again -> 404")
sys.exit(rep.done())
