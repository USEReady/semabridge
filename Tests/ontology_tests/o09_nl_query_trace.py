"""O09 - Trace WHY an AI Studio question produced bad SQL (read-only).

    uv run python ontology_tests/o09_nl_query_trace.py --q "Show case count by agent" --expect-metric "Case Count" --expect-dim Agent
    uv run python ontology_tests/o09_nl_query_trace.py --q "..." --project <id> --semantic-view <VIEW_NAME> [--execute]

For one question it calls /api/ai/search (metrics, concepts, attributes) and /api/ai/nl-query,
reads the stored ontology metrics, and checks each known failure cause separately:

  RC1  DAX in the SQL           ontology metric expressions are DAX, pasted into SELECT
  RC2  metric over-matching     more metrics picked than asked for (loose search threshold)
  RC3  dimension not found      dimension asked for is a COLUMN, engine only searches tables (concepts)
  RC4  duplicate domains        same metric in several domains under different name forms
  RC5  broken conversion        partially converted expressions (e.g. DAX '--' comments turned into SQL)
  RC6  invalid query shape      FROM is not the semantic view / mixes tables / no GROUP BY
  RC7  execution config         Snowflake connection settings (only with --execute)
  RC8  bad joins                self-joins, joins to tables not in FROM, unquoted names with spaces
  RC9  no metric found          answers a measure question with a plain column list instead of saying so

Nothing is changed. Writes ontology_tests/out/nl_trace_<slug>.json.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

DAX_TOKENS = r"\b(CALCULATE|CALCULATETABLE|COUNTROWS|DIVIDE|SUMX|AVERAGEX|SELECTEDVALUE|ALLEXCEPT|ALLSELECTED|" \
             r"DATEADD|SAMEPERIODLASTYEAR|DATESYTD|TOTALYTD|RELATED|USERELATIONSHIP|HASONEVALUE)\s*\(|\bVAR\s+\w+\s*=|\bRETURN\b|'[^']+'\[[^\]]+\]|\[[A-Za-z][^\]]*\]"


def nm(x):
    return re.sub(r"[^0-9a-z%]", "", str(x).casefold())


p = base_args(__doc__)
p.add_argument("--q", required=True, help="the question, exactly as typed in AI Studio")
p.add_argument("--expect-metric", action="append", default=[], help="metric(s) the question asks for")
p.add_argument("--expect-dim", action="append", default=[], help="dimension(s) the question asks for")
p.add_argument("--project")
p.add_argument("--semantic-view", help="deployed semantic view name the SQL should query")
p.add_argument("--execute", action="store_true")
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report(f"O09 trace: {a.q}")
trace = {"question": a.q}

# ---------------------------------------------------------------- inputs the engine uses
doms = api.get("/api/ontology/domains").json().get("domains", [])
metrics_by_norm = defaultdict(list)       # normalized metric name -> [(domain, name, expr, concept)]
attr_index = defaultdict(list)            # normalized attribute name -> [(domain, concept, attr)]
for d in doms:
    g = api.get("/api/ontology/domains/" + quote(d["domain"], safe=""))
    if not g.ok:
        continue
    parsed = g.json().get("parsed", {})
    for m in parsed.get("metrics", []):
        metrics_by_norm[nm(m["name"])].append((d["domain"], m["name"], m.get("sql_expression") or "", m.get("concept")))
    for c in parsed.get("concepts", []):
        for at in c.get("attributes", []):
            attr_index[nm(at["name"])].append((d["domain"], c["name"], at["name"]))
print(f"  domains in store: {[d['domain'] for d in doms]}")

search = {}
for t in ("METRIC", "CONCEPT", "ATTRIBUTE"):
    r = api.post("/api/ai/search", {"query": a.q, "item_type": t, "limit": 10, "min_score": 0.0})
    search[t] = [(h["name"], round(h["score"], 2), h.get("domain"), h.get("match_type")) for h in (r.json() if r.ok else [])]
    print(f"  search {t:9}: {search[t][:6]}")
trace["search"] = search

r = api.post("/api/ai/nl-query", {"prompt": a.q, "target": "SNOWFLAKE", "project_id": a.project})
if not rep.check(r.ok, "nl-query answered", body_text(r)[:200] if not r.ok else ""):
    sys.exit(rep.done())
plan = r.json()
sql = plan.get("generated_sql", "")
body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
trace["plan"] = plan
print(f"  detected metrics   : {plan.get('detected_metrics')}")
print(f"  detected dimensions: {plan.get('detected_dimensions')}")

# ---------------------------------------------------------------- RC1 DAX in SQL
dax_metrics = []
for name in plan.get("detected_metrics", []):
    for dom, mname, expr, _ in metrics_by_norm.get(nm(name), []):
        if mname == name and re.search(DAX_TOKENS, expr, re.I):
            dax_metrics.append(name)
dax_in_sql = re.findall(DAX_TOKENS, body, re.I)
rep.check(not dax_in_sql, "RC1 no DAX in generated SQL",
          f"DAX found; stored metric expressions are DAX for {sorted(set(dax_metrics))[:6]} "
          "(sml_to_osi drops sql_expression; from_osi_model copies DAX into sql_expression)")

# ---------------------------------------------------------------- RC2 over-matching
want_m = {nm(x) for x in a.expect_metric}
got_m = plan.get("detected_metrics", [])
if want_m:
    hit = [g for g in got_m if nm(g) in want_m]
    extra = [g for g in got_m if nm(g) not in want_m]
    rep.check(bool(hit), "RC2 asked metric detected", f"{a.expect_metric} not in {got_m}")
    rep.check(not extra, "RC2 no extra metrics", f"{len(extra)} extra: {extra[:8]}  "
              "(engine keeps every search hit >= 0.20, up to 5, instead of the best match)")

# ---------------------------------------------------------------- RC3 dimension
got_d = [nm(x) for x in plan.get("detected_dimensions", [])]
for dim in a.expect_dim:
    ok = any(nm(dim) in g for g in got_d)
    in_attrs = attr_index.get(nm(dim), [])
    in_search_attr = [h for h in search["ATTRIBUTE"] if nm(h[0]) == nm(dim)]
    stolen = [h[0] for h in search["METRIC"] if nm(dim) in nm(h[0])]
    detail = []
    if in_attrs:
        detail.append(f"'{dim}' is a COLUMN ({in_attrs[0][1]}.{in_attrs[0][2]}); engine only searches CONCEPT (tables)")
    if in_search_attr:
        detail.append("attribute search does find it")
    if stolen:
        detail.append(f"word matched metric(s) instead: {stolen[:3]}")
    rep.check(ok, f"RC3 dimension '{dim}' detected and grouped", "; ".join(detail) or "not found anywhere in the ontology")
rep.check(not a.expect_dim or re.search(r"GROUP\s+BY", body, re.I) is not None, "RC3 SQL has GROUP BY")

# ---------------------------------------------------------------- RC4 duplicate domains
dups = {k: v for k, v in metrics_by_norm.items() if len({x[0] for x in v}) > 1}
shown = {k: [f"{x[0]}:{x[1]}" for x in v] for k, v in dups.items() if any(nm(g) == k for g in got_m)}
name_forms = {k: {x[1] for x in v} for k, v in dups.items() if len({x[1] for x in v}) > 1}
rep.check(not shown, "RC4 detected metrics exist in one domain only", json.dumps(shown)[:300])
if name_forms:
    rep.warn("RC4 same metric under different name forms (not reported as collisions)",
             json.dumps({k: sorted(v) for k, v in list(name_forms.items())[:5]})[:300])

# ---------------------------------------------------------------- RC5 broken conversion
broken = []
for k, v in metrics_by_norm.items():
    for dom, mname, expr, _ in v:
        if re.search(r"-\s*\(-|\"&&\"|&&|\|\||--", expr) and not re.search(DAX_TOKENS, expr, re.I):
            broken.append(f"{dom}:{mname} -> {' '.join(expr.split())[:90]}")
rep.check(not broken, "RC5 no half-converted expressions in the store",
          f"{len(broken)}: {broken[:3]}  (DAX line comments '-- && ...' kept/converted instead of stripped)")

# ---------------------------------------------------------------- RC6 query shape
frm = re.search(r"\bFROM\s+([^\s;]+)", body, re.I)
frm_t = frm.group(1) if frm else ""
if a.semantic_view:
    rep.check(nm(a.semantic_view) in nm(frm_t), "RC6 FROM is the deployed semantic view", frm_t)
uses_sv_syntax = re.search(r"SEMANTIC_VIEW\s*\(", body, re.I) is not None
rep.check(uses_sv_syntax or not frm_t.upper().endswith("_SEMANTIC"),
          "RC6 semantic view queried with SEMANTIC_VIEW(... METRICS ... DIMENSIONS ...)",
          f"SELECT <expressions> FROM {frm_t} is not how a semantic view is queried")
concepts = set()
for name in got_m:
    for dom, mname, expr, concept in metrics_by_norm.get(nm(name), []):
        if mname == name and concept:
            concepts.add(concept)
rep.check(len(concepts) <= 1, "RC6 metrics come from one table", f"mixes {sorted(concepts)}")

# ---------------------------------------------------------------- RC8 joins
frm_tables = re.findall(r"\bFROM\s+([\w\".]+)", body, re.I)
joins = re.findall(r"\bJOIN\s+([\w\".]+)\s+ON\s+(.+)", body, re.I)
in_query = {t.strip('"').casefold() for t in frm_tables}
self_joins, unknown, unquoted = [], [], []
for tbl, cond in joins:
    t = tbl.strip('"').casefold()
    if t in in_query:
        self_joins.append(tbl)
    refs = {r.casefold() for r in re.findall(r"([A-Za-z_][\w]*)\s*\.", cond)}
    unknown += [r for r in refs if r not in in_query | {t}]
    if re.search(r"\.[A-Za-z_]\w*\s+[A-Za-z_]\w*\s*(=|$)", cond):
        unquoted.append(cond.strip()[:60])
    in_query.add(t)
if joins:
    rep.check(not self_joins, "RC8 no table joined to itself", f"{self_joins}")
    rep.check(not unknown, "RC8 joins only reference tables already in the query", f"{sorted(set(unknown))}")
    rep.check(not unquoted, "RC8 column names with spaces are quoted", f"{unquoted[:2]}")

# ---------------------------------------------------------------- RC9 no metric
if not plan.get("detected_metrics"):
    rep.warn("RC9 no metric matched the question",
             f"engine still returned SQL ({'GROUP BY on ' + str(plan.get('detected_dimensions')) if plan.get('detected_dimensions') else 'SELECT *'}); "
             "it should say 'no metric found for ...' or ask which metric was meant")
    rep.check(plan.get("confidence_score", 1) < 0.5, "RC9 low confidence reported when no metric found",
              f"confidence {plan.get('confidence_score')}")

# ---------------------------------------------------------------- RC7 execution
if a.execute:
    e = api.post("/api/ai/execute-query", {"sql": sql, "target": "SNOWFLAKE", "project_id": a.project, "limit": 20})
    ej = e.json() if e.ok else {}
    err = ej.get("error") or ej.get("message") or ""
    cfg = re.search(r"PASSWORD|PRIVATE_KEY|ACCOUNT|AUTHENTICATOR|not set|resolved to an empty", err, re.I)
    rep.check(ej.get("success") and not ej.get("is_simulated"), "RC7 executes on real Snowflake",
              ("CONFIG: " if cfg else "SQL: ") + err[:200])
    trace["execute"] = ej

out = Path("ontology_tests/out"); out.mkdir(parents=True, exist_ok=True)
slug = re.sub(r"[^a-z0-9]+", "_", a.q.casefold())[:40]
(out / f"nl_trace_{slug}.json").write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
print(f"  trace saved: {out / f'nl_trace_{slug}.json'}")
sys.exit(rep.done())
