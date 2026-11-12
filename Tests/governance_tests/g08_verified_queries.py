"""G08 - Cortex verified queries (POST /api/ai/save-verified-query, GET /api/ai/verified-queries).

    uv run python governance_tests/g08_verified_queries.py --project <id>
    uv run python governance_tests/g08_verified_queries.py --project <id> --view DB.SCH.VIEW --metric T.METRIC [--dim T.COL]

Without --view (no Snowflake needed):
  VQ1 empty question / SQL -> 400
  VQ2 not confirmed -> 400
  VQ3 confirmed but never executed -> 400 (cannot be marked verified)
  VQ4 list without project_id -> 400/422; list for a project returns a count matching the list
With --view (runs a real SEMANTIC_VIEW query through /api/ai/execute-query first):
  VQ5 executed + confirmed -> saved, appears in the list
  VQ6 same SQL with comments / LIMIT / whitespace changes -> still recognised as executed
  VQ7 same SQL with a different string-literal case ('East' vs 'EAST') -> must NOT count as executed
  VQ8 saving the same question again -> updated, not duplicated
  VQ9 two different questions whose names sanitize to the same 40 chars -> must not overwrite each other
  VQ10 project isolation: query saved for project A is not listed for project B
  VQ11 a failed execution never makes a query 'verified'
Test entries use questions starting with 'TEST VQ' so they are easy to find and delete.
"""
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

p = base_args(__doc__)
p.add_argument("--project", required=True)
p.add_argument("--view")
p.add_argument("--metric", help="logical metric name in the view, e.g. CASES.CASE_COUNT")
p.add_argument("--dim", help="optional logical dimension, e.g. CASES.AGENT")
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report("G08 verified queries")
tag = uuid.uuid4().hex[:6]


def save(question, sql, confirmed=True, project=None):
    return api.post("/api/ai/save-verified-query", {"question": question, "sql": sql, "confirmed": confirmed,
                                                     "project_id": project or a.project, "target": "SNOWFLAKE"})


def listed(project):
    r = api.get(f"/api/ai/verified-queries?project_id={project}")
    return r.json().get("queries", []) if r.ok else []


never_sql = f"SELECT 1 AS NEVER_RUN_{tag}"
rep.check(save("", never_sql).status_code == 400, "VQ1 empty question -> 400")
rep.check(save(f"TEST VQ {tag}", "").status_code == 400, "VQ1 empty SQL -> 400")
rep.check(save(f"TEST VQ {tag}", never_sql, confirmed=False).status_code == 400, "VQ2 not confirmed -> 400")
r = save(f"TEST VQ never {tag}", never_sql)
rep.check(r.status_code == 400, "VQ3 never-executed SQL cannot be saved as verified", f"{r.status_code} {body_text(r)[:120]}")
r = api.get("/api/ai/verified-queries?project_id=")
rep.check(r.status_code in (400, 422), "VQ4 list without project_id rejected", str(r.status_code))
r = api.get(f"/api/ai/verified-queries?project_id={a.project}")
rep.check(r.ok and r.json().get("count") == len(r.json().get("queries", [])), "VQ4 count matches list")

if not (a.view and a.metric):
    rep.warn("VQ5-VQ11 skipped", "pass --view and --metric (needs Snowflake) to test real verification")
    sys.exit(rep.done())

sql = f"SELECT * FROM SEMANTIC_VIEW({a.view} METRICS {a.metric}" + (f" DIMENSIONS {a.dim}" if a.dim else "") + ")"
e = api.post("/api/ai/execute-query", {"sql": sql, "target": "SNOWFLAKE", "project_id": a.project, "limit": 50})
ej = e.json() if e.ok else {}
if not rep.check(e.ok and ej.get("success") and not ej.get("is_simulated"), "execute the SEMANTIC_VIEW query",
                 (ej.get("error") or body_text(e))[:160]):
    sys.exit(rep.done())
q1 = f"TEST VQ verified {tag}"
r = save(q1, sql)
rep.check(r.ok, "VQ5 executed + confirmed -> saved", body_text(r)[:160])
rep.check(any(q["question"] == q1 for q in listed(a.project)), "VQ5 saved query listed")
variant = f"-- comment\n{sql.replace(' ', '  ')}\nLIMIT 10;"
r = save(f"TEST VQ variant {tag}", variant)
rep.check(r.ok, "VQ6 comment/LIMIT/whitespace variant recognised as executed", body_text(r)[:120])
lit_sql = sql[:-1] + f" WHERE {a.dim} = 'zz{tag}')" if a.dim else None
if lit_sql:
    e2 = api.post("/api/ai/execute-query", {"sql": lit_sql, "target": "SNOWFLAKE", "project_id": a.project, "limit": 5})
    r = save(f"TEST VQ literal {tag}", lit_sql.replace(f"'zz{tag}'", f"'ZZ{tag.upper()}'"))
    rep.check(r.status_code == 400, "VQ7 different literal case is a different query (not 'executed')",
              "normalization upper-cases the whole SQL including string literals")
before = len([q for q in listed(a.project) if q["question"] == q1])
save(q1, sql)
after = len([q for q in listed(a.project) if q["question"] == q1])
rep.check(before == after == 1, "VQ8 same question saved twice -> one entry", f"{before} -> {after}")
long = "TEST VQ " + "x" * 40
qa, qb = long + f" alpha {tag}", long + f" beta {tag}"
save(qa, sql)
save(qb, sql)
names = [q["question"] for q in listed(a.project)]
rep.check(qa in names and qb in names, "VQ9 different questions with the same 40-char name both kept",
          "second save overwrote the first (name = first 40 chars of the question)")
rep.check(not any(q["question"] == q1 for q in listed(f"other-project-{tag}")), "VQ10 not visible in another project")
bad = f"SELECT * FROM SEMANTIC_VIEW({a.view} METRICS NO_SUCH.METRIC_{tag})"
api.post("/api/ai/execute-query", {"sql": bad, "target": "SNOWFLAKE", "project_id": a.project})
rep.check(save(f"TEST VQ failed {tag}", bad).status_code == 400, "VQ11 failed execution cannot be saved as verified")
print("  note: test entries start with 'TEST VQ' (no delete endpoint is exposed; remove them in the DB/UI)")
sys.exit(rep.done())
