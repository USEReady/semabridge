"""O08 - AI/BI Conversational Studio: search by synonym, NL -> SQL, optional real execution.

    uv run python ontology_tests/o08_ai_studio.py --questions fixtures/questions_example.json
    uv run python ontology_tests/o08_ai_studio.py --questions my_questions.json --execute --project <id>

questions.json: a list of
  {"q": "total units sold by region", "expect_metrics": ["Total Units"],
   "expect_in_sql": ["SUM"], "expect_group_by": ["Region"], "synonym": "Volume Sold"}
Every key except "q" is optional. Nothing is model-specific in this script.

--execute runs the SQL through /api/ai/execute-query and FAILS if the result is simulated
(the endpoint returns made-up rows when Snowflake is not reachable).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

p = base_args(__doc__)
p.add_argument("--questions", required=True)
p.add_argument("--execute", action="store_true")
p.add_argument("--project")
p.add_argument("--target", default="SNOWFLAKE")
p.add_argument("--semantic-view", help="deployed semantic view name; SQL must query it")
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report("O08 AI Studio")
def nm(x):
    """Compare names ignoring case, spaces, _ and symbols (Case Count == CASE_COUNT)."""
    return re.sub(r"[^0-9a-z%]", "", str(x).casefold())


qs = json.loads(Path(a.questions).read_text(encoding="utf-8"))

st = api.get("/api/ai/status")
rep.check(st.ok, "AI status", body_text(st)[:200])
rx = api.post("/api/ai/reindex")
rep.check(rx.ok, "reindex search after domain changes", str(rx.status_code))

if a.project:
    rep.warn("project_id is accepted by /nl-query but not passed to the query engine",
             "SQL is built from all domains, not only the project's attached ones")
out = []
for item in qs:
    q = item["q"]
    if item.get("synonym"):
        s = api.post("/api/ai/search", {"query": item["synonym"], "limit": 5})
        hits = s.json() if s.ok else []
        names = [h.get("name") or h.get("item_name") for h in hits]
        want = item.get("expect_metrics", [])
        rep.check(s.ok and (not want or any(nm(w) in nm(n) for w in want for n in names)),
                  f"search '{item['synonym']}' finds {want}", str(names[:5]))
    r = api.post("/api/ai/nl-query", {"prompt": q, "target": a.target, "project_id": a.project})
    if not rep.check(r.ok, f"nl-query: {q}", body_text(r)[:200] if not r.ok else ""):
        continue
    plan = r.json()
    sql = plan.get("generated_sql", "")
    for m in item.get("expect_metrics", []):
        rep.check(any(nm(m) in nm(d) for d in plan.get("detected_metrics", [])),
                  f"  detects metric '{m}'", str(plan.get("detected_metrics")))
    for frag in item.get("expect_in_sql", []):
        rep.check(frag.casefold() in sql.casefold(), f"  SQL contains '{frag}'")
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    bad_alias = re.findall(r'\bAS\s+([A-Za-z_]\w*\s+[A-Za-z_]\w*)\s*(?:,|\n|FROM)', body, re.I)
    rep.check(not bad_alias, "  aliases with spaces are quoted", str(bad_alias[:3]))
    frm = re.search(r'\bFROM\s+(.+?)(?:\s+(?:WHERE|GROUP|ORDER|LIMIT|JOIN)\b|;|$)', body, re.I | re.S)
    frm_txt = frm.group(1).strip() if frm else ""
    rep.check(bool(frm_txt) and (not re.search(r"\s", frm_txt) or frm_txt.startswith(('"', "SEMANTIC_VIEW"))),
              "  FROM is a single valid object name", frm_txt[:80])
    if a.semantic_view:
        rep.check(a.semantic_view.upper() in body.upper(), f"  queries the deployed semantic view {a.semantic_view}", frm_txt[:80])
    for dim in item.get("expect_group_by", []):
        rep.check(re.search(r"GROUP\s+BY", body, re.I) is not None and nm(dim) in nm(body),
                  f"  groups by '{dim}' (asked in the question)")
    if plan.get("confidence_score", 0) < 0.5:
        rep.warn(f"  low confidence {plan.get('confidence_score')}", "; ".join(plan.get("warnings", []))[:200])
    row = {"q": q, "sql": sql, "metrics": plan.get("detected_metrics"), "confidence": plan.get("confidence_score")}
    if a.execute and sql:
        e = api.post("/api/ai/execute-query", {"sql": sql, "target": a.target, "project_id": a.project, "limit": 20})
        ej = e.json() if e.ok else {}
        rep.check(e.ok and ej.get("success"), "  SQL executes", (ej.get("error") or body_text(e))[:200] if not (e.ok and ej.get("success")) else f"{ej.get('row_count')} rows")
        rep.check(not ej.get("is_simulated"), "  result is real (not simulated preview)", ej.get("message") or "")
        row["rows"] = ej.get("rows", [])[:5]
    out.append(row)

o = Path("ontology_tests/out"); o.mkdir(parents=True, exist_ok=True)
(o / "ai_studio_results.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print(f"  SQL and results saved to {o / 'ai_studio_results.json'} - compare with Snowflake/Power BI by hand.")
sys.exit(rep.done())
