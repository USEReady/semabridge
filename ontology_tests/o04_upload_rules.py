"""O04 - Ingest YAML: good files are accepted, bad files are rejected with a clear reason.

    uv run python ontology_tests/o04_upload_rules.py

Every test domain gets a random name and is deleted at the end.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text, unique  # noqa: E402

a = base_args(__doc__).parse_args()
api = Api(a.base, a.token)
rep = Report("O04 Ingest YAML rules")
created = []


def good(name):
    return f"""domain: {name}
owner: Test Team
concepts:
  - name: Order {name[-6:]}
    synonyms: [Purchase]
    maps_to: [SomeTable]
    attributes:
      - name: Amount
        maps_to: [SomeTable.Amount]
metrics:
  - name: Order Value {name[-6:]}
    concept: Order {name[-6:]}
    maps_to: [Order Value]
""".encode()


def expect_reject(label, files, needle):
    r = api.upload(files)
    txt = body_text(r)
    rejected = r.status_code == 422 or (r.ok and r.json().get("file_errors"))
    rep.check(bool(rejected) and needle.lower() in txt.lower(), label, f"{r.status_code} {txt[:220]}")


# 1 valid file
n1 = unique("TEST Upload"); created.append(n1)
r = api.upload([("good.yaml", good(n1))])
rep.check(r.ok and n1 in r.json().get("uploaded_domains", []), "valid domain YAML is ingested", f"{r.status_code}")

# 2 semantic model file (what happened with model.yaml)
expect_reject("SML/OSI model.yaml is rejected (no 'domain' key)",
              [("model.yaml", b"unique_name: x\ndatasets: []\nmetrics: []\n")], "Missing required key 'domain'")
# 3 typo key
expect_reject("misspelled key is rejected and named",
              [("typo.yaml", f"domain: {unique('TEST T')}\nconcepts:\n  - name: A\n    synonymz: [b]\n".encode())], "synonymz")
# 4 duplicate concept names
expect_reject("duplicate concept names are rejected",
              [("dup.yaml", f"domain: {unique('TEST D')}\nconcepts:\n  - name: A\n  - name: a\n".encode())], "Duplicate")
# 5 duplicate YAML key
expect_reject("duplicate YAML key is rejected",
              [("dupkey.yaml", f"domain: {unique('TEST K')}\ndomain: Other\n".encode())], "Duplicate key")
# 6 metric -> undefined concept
expect_reject("metric pointing to an undefined concept is rejected",
              [("ref.yaml", f"domain: {unique('TEST R')}\nmetrics:\n  - name: M\n    concept: Nope\n".encode())], "undefined concept")
# 7 relationship -> undefined concept
expect_reject("relationship to an undefined concept is rejected",
              [("rel.yaml", f"domain: {unique('TEST L')}\nconcepts:\n  - name: A\nrelationships:\n  - from_concept: A\n    to_concept: Ghost\n".encode())],
              "undefined concept")
# 8 two files, same domain
dn = unique("TEST Same")
r = api.upload([("a.yaml", good(dn)), ("b.yaml", good(dn))])
txt = body_text(r)
rep.check("more than one uploaded file" in txt, "two files declaring the same domain: second is rejected", txt[:200])
created.append(dn)
# 9 wrong extension
expect_reject("non-YAML file is rejected", [("notes.txt", b"domain: x\n")], ".yaml")
# 10 oversized
big = b"domain: Big\ndescription: " + b"x" * (1024 * 1024 + 10) + b"\n"
expect_reject("file over the size limit is rejected", [("big.yaml", big)], "limit")
# 11 unsafe id
expect_reject("unsafe domain id (path) is rejected",
              [("evil.yaml", b"domain: Evil\nid: ../../evil\n")], "")
# 12 invalid YAML
expect_reject("broken YAML syntax is rejected", [("bad.yaml", b"domain: [unclosed\n")], "YAML")
# 13 rejected file did not register anything
lst = api.get("/api/ontology/domains").json()
names = [d["domain"] for d in lst.get("domains", [])]
rep.check(not any(n in ("Evil", "Big") for n in names), "rejected files leave no domain behind")
rep.check(not lst.get("load_errors"), "store has no load errors", str(lst.get("load_errors"))[:200])

for n in created:
    api.delete_domain_quiet(n)
sys.exit(rep.done())
