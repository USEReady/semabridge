"""G02 - Blast radius: right metrics, right severity, no false hits (POST /api/governance/blast-radius).

    uv run python governance_tests/g02_blast_radius.py

Builds two TEST domains with known dependencies, runs simulations, checks the answers, deletes the domains.

Edge cases covered:
  B1  COLUMN used by a metric                     -> that metric, DIRECT
  B2  column name that is a prefix of another     -> 'amt' must not hit 'amt_net' or 'amt%'
  B3  case-insensitive match                      -> 'AMT' finds 'amt'
  B4  same column name in two tables + table_context -> only that table's metrics
  B5  same column name in another DOMAIN (no context) -> leak? (reported as WARN)
  B6  TABLE target                                -> all metrics on that concept
  B7  METRIC target                               -> the metric itself + metrics built on it (TRANSITIVE)
  B8  metric referenced as [Metric] in another formula -> transitive dependency found
  B9  DROP/RENAME -> BREAKING; MODIFY_EXPRESSION -> DEGRADED; nothing affected -> SAFE
  B10 unknown target -> SAFE, 0 items
  B11 empty target -> 400; invalid target_type -> 400; lower-case target_type accepted
  B12 totals: total_impacted_items == sum of lists; one edge per affected item; stewards carried
  B13 BI reports / AI tools lists                  -> reported as empty (not implemented) = WARN
  B14 stale metrics are ignored
  B15 name with regex characters ('%', '(', '.') does not crash and matches literally
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text, make_domain_yaml, save_domain, unique  # noqa: E402

a = base_args(__doc__).parse_args()
api = Api(a.base, a.token)
rep = Report("G02 blast radius")
tag = unique("x")[-6:]
T1, T2 = f"BRFact{tag}", f"BROther{tag}"
D1, D2 = f"TEST Blast {tag}", f"TEST Blast Foreign {tag}"
M_BASE, M_NET, M_OTHER, M_DERIVED, M_PCT, M_STALE = (f"br_base_{tag}", f"br_net_{tag}", f"br_other_{tag}",
                                                     f"br_derived_{tag}", f"br_pct_{tag}", f"br_stale_{tag}")
created = []


def mk(name, **kw):
    r = save_domain(api, make_domain_yaml(name, **kw))
    if r.ok:
        created.append(name)
    return r


r = mk(D1,
       concepts=[{"name": T1, "attributes": [{"name": "amt"}, {"name": "amt_net"}, {"name": "status"}]},
                 {"name": T2, "attributes": [{"name": "amt"}]}],
       metrics=[
           {"name": M_BASE, "concept": T1, "sql_expression": f"SUM({T1}.amt)", "data_steward": "owner@base"},
           {"name": M_NET, "concept": T1, "sql_expression": f"SUM({T1}.amt_net)"},
           {"name": M_OTHER, "concept": T2, "sql_expression": f"SUM({T2}.amt)"},
           {"name": M_DERIVED, "concept": T1, "sql_expression": f"[{M_BASE}] * 2"},
           {"name": M_PCT, "concept": T1, "sql_expression": f"SUM({T1}.\"amt%\") / 100"},
           {"name": M_STALE, "concept": T1, "sql_expression": f"SUM({T1}.amt)", "stale": True},
       ])
if not rep.check(r.ok, "create test domain", body_text(r) if not r.ok else ""):
    sys.exit(rep.done())
r2 = mk(D2, concepts=[{"name": f"Foreign{tag}", "attributes": [{"name": "amt"}]}],
        metrics=[{"name": f"br_foreign_{tag}", "concept": f"Foreign{tag}", "sql_expression": f"SUM(Foreign{tag}.amt)"}])
rep.check(r2.ok, "create foreign domain", body_text(r2) if not r2.ok else "")


def sim(target, ttype="COLUMN", change="DROP", table=None):
    body = {"target_name": target, "target_type": ttype, "change_type": change}
    if table:
        body["table_context"] = table
    return api.post("/api/governance/blast-radius", body)


def names(rj):
    return {i["name"] for i in rj.get("affected_metrics", [])}


# B1 / B2 / B3 / B4
j = sim("amt", table=T1).json()
rep.check(M_BASE in names(j), "B1 column -> metric using it", str(names(j)))
rep.check(M_NET not in names(j), "B2 'amt' does not hit 'amt_net'", str(names(j)))
rep.check(M_PCT not in names(j), "B2 'amt' does not hit a different column 'amt%'", str(names(j)))
rep.check(M_OTHER not in names(j), "B4 table_context keeps other tables out", str(names(j)))
rep.check(M_STALE not in names(j), "B14 stale metric ignored")
jc = sim("AMT", table=T1).json()
rep.check(M_BASE in names(jc), "B3 case-insensitive")
# B5
jl = sim("amt").json()
leak = {n for n in names(jl) if tag in n} - {M_BASE, M_OTHER, M_DERIVED}
if f"br_foreign_{tag}" in names(jl):
    rep.warn("B5 without table context, metrics from OTHER domains/models are included",
             "blast radius is not scoped to a project/semantic view")
# B6
jt = sim(T1, ttype="TABLE").json()
want = {M_BASE, M_NET, M_DERIVED, M_PCT}
rep.check(want <= names(jt), "B6 TABLE -> all metrics on that table", f"missing {sorted(want - names(jt))}")
rep.check(M_OTHER not in names(jt), "B6 other table's metric not included")
# B7 / B8
jm = sim(M_BASE, ttype="METRIC", change="MODIFY_EXPRESSION").json()
rep.check(M_BASE in names(jm), "B7 METRIC target includes itself")
trans = [i for i in jm.get("affected_metrics", []) if i["name"] == M_DERIVED]
rep.check(bool(trans), "B8 metric built on it ([Metric] reference) found",
          "no transitive impact: dependent metrics are missed")
if trans:
    rep.check(trans[0]["impact_level"] == "TRANSITIVE", "B8 dependent metric marked TRANSITIVE", trans[0]["impact_level"])
jcol = sim("amt", table=T1).json()
rep.check(M_DERIVED in names(jcol), "B8 dropping a column also flags metrics built on affected metrics",
          "only direct users of the column are listed")
# B9
rep.check(sim("amt", table=T1, change="DROP").json()["severity"] == "BREAKING", "B9 DROP with users -> BREAKING")
rep.check(sim("amt", table=T1, change="RENAME").json()["severity"] == "BREAKING", "B9 RENAME with users -> BREAKING")
rep.check(sim("amt", table=T1, change="MODIFY_TYPE").json()["severity"] == "DEGRADED", "B9 MODIFY_TYPE with users -> DEGRADED")
# B10
ju = sim(f"no_such_col_{tag}").json()
rep.check(ju["severity"] in ("NOT_FOUND",) or ju.get("status") == "not_found",
          "B10 unknown target -> NOT_FOUND (never SAFE)", f"{ju['severity']}, {ju['total_impacted_items']} items")
# B11
rep.check(sim("  ").status_code == 400, "B11 blank target -> 400")
rep.check(sim("amt", ttype="VIEW").status_code == 400, "B11 invalid target_type -> 400")
rep.check(sim("amt", ttype="column", table=T1).ok, "B11 lower-case target_type accepted")
bad_change = sim("amt", table=T1, change="EXPLODE")
rep.check(bad_change.status_code == 400, "B11 invalid change_type -> 400",
          f"{bad_change.status_code}: unknown change types are accepted silently")
# B12
for label, rj in (("column", j), ("table", jt), ("metric", jm)):
    tot = len(rj.get("affected_metrics", [])) + len(rj.get("affected_bi_reports", [])) + len(rj.get("affected_ai_tools", []))
    rep.check(rj["total_impacted_items"] == tot, f"B12 {label}: total == sum of lists", f"{rj['total_impacted_items']} vs {tot}")
    rep.check(len(rj.get("dependency_graph_edges", [])) >= len(rj.get("affected_metrics", [])), f"B12 {label}: one edge per item")
st = [i for i in j.get("affected_metrics", []) if i["name"] == M_BASE]
rep.check(bool(st) and st[0].get("steward") == "owner@base", "B12 steward carried to the impacted item")
rem = j.get("remediation_steps", [])
rep.check(any(M_BASE in s for s in rem), "B12 remediation names the affected metrics", str(rem)[:160])
# B13
if not (j.get("affected_bi_reports") or j.get("affected_ai_tools")):
    rep.warn("B13 BI reports and AI tools are always empty", "downstream reports / Cortex verified queries are not tracked")
# B15
jp = sim("amt%", table=T1)
rep.check(jp.ok, "B15 special characters in the target do not crash", body_text(jp)[:120])
if jp.ok:
    rep.check(M_PCT in names(jp.json()), "B15 'amt%' matched literally", str(names(jp.json())))
jdot = sim(f"{T1}.amt", table=None)
rep.check(jdot.ok, "B15 'table.column' form accepted", body_text(jdot)[:120])
if jdot.ok:
    rep.check(M_BASE in names(jdot.json()) and M_OTHER not in names(jdot.json()), "B15 'table.column' scopes to that table",
              str(names(jdot.json())))

for d in created:
    api.delete_domain_quiet(d)
sys.exit(rep.done())
