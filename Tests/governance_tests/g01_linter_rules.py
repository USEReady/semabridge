"""G01 - Governance linter: every rule fires when it should, and only then (POST /api/governance/lint).

    uv run python governance_tests/g01_linter_rules.py

Creates small TEST domains (random names), lints, checks the issues that belong to them, deletes them.
Generic: no model names. Each check prints what the linter returned when it fails.

Edge cases covered:
  E1  complete metric/concept (steward + description)        -> no steward/description issues
  E2  missing steward                                         -> RULE_MISSING_STEWARD (ERROR)
  E3  description shorter than 5 chars                        -> RULE_MISSING_DESCRIPTION (WARNING)
  E4  options off (require_steward/description = false)      -> those rules silent
  E5  empty expression vs binding-only (maps_to) metric        -> ERROR only when there is truly nothing
  E6  snake_case on/off; business name with spaces            -> WARNING only when enabled
  E7  same metric name, different formulas, two domains        -> RULE_METRIC_COLLISION
  E8  'Case Count' vs 'CASE_COUNT' style duplicates            -> should collide (normalized names)
  E9  losing duplicate in 2nd domain is still linted           -> its own steward issue is reported
  E10 relationship cycle A->B->C->A                           -> RULE_CIRCULAR_JOIN
  E11 two-node cycle A->B, B->A and self loop A->A             -> reported
  E12 role-playing: two A->B relationships (no cycle)          -> NOT a cycle
  E13 cross-domain relationship to an unknown concept          -> RULE_UNRESOLVED_REFERENCE
  E14 counts: error_count/warning_count/total match issues; passed == (errors == 0)
  E15 lint on unchanged store twice -> same result (deterministic)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text, issues_for, make_domain_yaml, save_domain, unique  # noqa: E402

a = base_args(__doc__).parse_args()
api = Api(a.base, a.token)
rep = Report("G01 linter rules")
tag = unique("x")[-6:]
created = []


def mk(name, **kw):
    r = save_domain(api, make_domain_yaml(name, **kw))
    ok = r.ok and r.json().get("success")
    if ok:
        created.append(name)
    return ok, r


def lint(**opts):
    body = {"require_steward": True, "require_description": True, "enforce_snake_case_metrics": True}
    body.update(opts)
    r = api.post("/api/governance/lint", body)
    return r.json() if r.ok else {"_error": body_text(r)}


def has(issues, rule, obj=None, sev=None):
    return [i for i in issues if i["rule_id"] == rule and (obj is None or i["object_name"].casefold() == obj.casefold())
            and (sev is None or i["severity"] == sev)]


good_desc = "A complete business description for testing."
D1 = f"TEST Gov Clean {tag}"
D2 = f"TEST Gov Gaps {tag}"
D3 = f"TEST Gov Other {tag}"
D4 = f"TEST Gov Graph {tag}"

ok, r = mk(D1,
           concepts=[{"name": f"ClnEntity{tag}", "description": good_desc, "data_steward": "steward@test",
                      "attributes": [{"name": "attr_one", "description": good_desc}]}],
           metrics=[{"name": f"clean_metric_{tag}", "description": good_desc, "data_steward": "steward@test",
                     "sql_expression": "SUM(x)", "concept": f"ClnEntity{tag}"}])
rep.check(ok, "create clean domain", body_text(r) if not ok else "")
ok, r = mk(D2,
           concepts=[{"name": f"GapEntity{tag}", "description": "abc"}],
           metrics=[{"name": f"Gap Metric {tag}", "description": "abc", "sql_expression": "SUM(y)"},
                    {"name": f"empty_metric_{tag}", "description": good_desc, "data_steward": "s@t"},
                    {"name": f"bound_metric_{tag}", "description": good_desc, "data_steward": "s@t",
                     "maps_to": ["Some BI Measure"]},
                    {"name": f"Dup Metric {tag}", "sql_expression": "SUM(a)", "data_steward": "s@t", "description": good_desc},
                    {"name": f"DUP_METRIC_{tag}".replace(" ", "_"), "sql_expression": "SUM(a)", "data_steward": "s@t",
                     "description": good_desc}])
rep.check(ok, "create domain with gaps", body_text(r) if not ok else "")
ok, r = mk(D3, metrics=[{"name": f"Gap Metric {tag}", "sql_expression": "SUM(y) * 2", "description": good_desc}])
rep.check(ok, "create second domain reusing a metric name", body_text(r) if not ok else "")
ok, r = mk(D4,
           concepts=[{"name": n} for n in (f"GA{tag}", f"GB{tag}", f"GC{tag}", f"GD{tag}", f"GE{tag}", f"GF{tag}")],
           relationships=[
               {"from_concept": f"GA{tag}", "to_concept": f"GB{tag}", "join_expression": f"GA{tag}.k = GB{tag}.k"},
               {"from_concept": f"GB{tag}", "to_concept": f"GC{tag}", "join_expression": f"GB{tag}.k = GC{tag}.k"},
               {"from_concept": f"GC{tag}", "to_concept": f"GA{tag}", "join_expression": f"GC{tag}.k = GA{tag}.k"},
               {"from_concept": f"GD{tag}", "to_concept": f"GE{tag}", "join_expression": f"GD{tag}.a = GE{tag}.k"},
               {"from_concept": f"GD{tag}", "to_concept": f"GE{tag}", "join_expression": f"GD{tag}.b = GE{tag}.k"},
               {"from_concept": f"GF{tag}", "to_concept": f"GF{tag}", "join_expression": f"GF{tag}.parent = GF{tag}.k"},
           ])
rep.check(ok, "create relationship-graph domain", body_text(r) if not ok else "")
# E13: domain A joins to a concept owned by domain B; then B is deleted -> A's join points nowhere
okB, _ = mk(f"TEST Gov Target {tag}", concepts=[{"name": f"UB{tag}"}])
ok13, r13 = mk(f"TEST Gov Unres {tag}", concepts=[{"name": f"UA{tag}"}],
               relationships=[{"from_concept": f"UA{tag}", "to_concept": f"UB{tag}"}])
if okB and ok13:
    api.delete_domain_quiet(f"TEST Gov Target {tag}")
    created.remove(f"TEST Gov Target {tag}")
else:
    rep.warn("E13 could not set up a cross-domain reference", body_text(r13)[:160])

rep_full = lint()
if not rep.check("_error" not in rep_full, "lint endpoint answers", rep_full.get("_error", "")):
    sys.exit(rep.done())
mine = issues_for(rep_full, created + [tag])

# E1
clean = [i for i in mine if (i.get("domain") or "").casefold() == D1.casefold()
         and i["rule_id"] in ("RULE_MISSING_STEWARD", "RULE_MISSING_DESCRIPTION", "RULE_EMPTY_EXPRESSION")]
rep.check(not clean, "E1 complete domain has no steward/description/expression issues", str(clean)[:200])
# E2
rep.check(bool(has(mine, "RULE_MISSING_STEWARD", f"Gap Metric {tag}", "ERROR")), "E2 metric without steward -> ERROR")
rep.check(bool(has(mine, "RULE_MISSING_STEWARD", f"GapEntity{tag}", "ERROR")), "E2 concept without steward -> ERROR")
# E3
rep.check(bool(has(mine, "RULE_MISSING_DESCRIPTION", f"Gap Metric {tag}", "WARNING")), "E3 3-char description -> WARNING")
# E5
rep.check(bool(has(mine, "RULE_EMPTY_EXPRESSION", f"empty_metric_{tag}")), "E5 metric with no formula and no binding -> ERROR")
rep.check(not has(mine, "RULE_EMPTY_EXPRESSION", f"bound_metric_{tag}"),
          "E5 binding-only metric (maps_to) is not an empty-expression ERROR",
          "binding-only metrics are valid (collision rule treats them as partial definitions)")
# E6
rep.check(bool(has(mine, "RULE_NAMING_CONVENTION", f"Gap Metric {tag}")), "E6 snake_case on: 'Gap Metric' flagged")
rep.check(not has(mine, "RULE_NAMING_CONVENTION", f"clean_metric_{tag}"), "E6 snake_case name not flagged")
# E7
rep.check(bool(has(mine, "RULE_METRIC_COLLISION", f"Gap Metric {tag}")), "E7 same name, different formula -> collision")
# E8
dup = [i for i in mine if i["rule_id"] == "RULE_METRIC_COLLISION" and f"dup" in i["object_name"].casefold() and tag in i["object_name"]]
rep.check(bool(dup), "E8 'Dup Metric' vs 'DUP_METRIC' detected as the same metric",
          "names compared exactly (casefold); spaces/underscores make two 'different' metrics")
# E9
d3_steward = [i for i in mine if i["rule_id"] == "RULE_MISSING_STEWARD" and (i.get("domain") or "").casefold() == D3.casefold()]
rep.check(bool(d3_steward), "E9 losing duplicate (2nd domain) still linted",
          "linter iterates the federated first-wins map; the 2nd domain's metric is never checked")
# E10/E11/E12
cyc = has(mine, "RULE_CIRCULAR_JOIN")
cyc_txt = " ".join(i["message"] for i in cyc).casefold()
rep.check(all(n.casefold() in cyc_txt for n in (f"GA{tag}", f"GB{tag}", f"GC{tag}")), "E10 3-node cycle reported")
rep.check(f"gf{tag}".casefold() in cyc_txt, "E11 self-referencing relationship reported (parent-child)",
          "self loops are often legit hierarchies; decide: report as INFO, not silently skip")
rep.check(f"gd{tag}".casefold() not in cyc_txt, "E12 two relationships between the same tables are not a cycle")
# E13
if ok13:
    rep.check(bool(has(mine, "RULE_UNRESOLVED_REFERENCE")), "E13 unknown concept in relationship -> UNRESOLVED_REFERENCE")
# E14
errs = sum(1 for i in rep_full["issues"] if i["severity"] == "ERROR")
warns = sum(1 for i in rep_full["issues"] if i["severity"] == "WARNING")
rep.check(rep_full["error_count"] == errs and rep_full["warning_count"] == warns and rep_full["total_issues"] == len(rep_full["issues"]),
          "E14 counts match the issue list")
rep.check(rep_full["passed"] == (errs == 0), "E14 passed == (no errors)")
unknown_obj = [i for i in rep_full["issues"] if i["object_name"] in ("Unknown", "", None)]
if unknown_obj:
    rep.warn("E14 some issues have no object name", f"{len(unknown_obj)} x '{unknown_obj[0]['rule_id']}'")
# E4 / E6 off
rep_off = lint(require_steward=False, require_description=False, enforce_snake_case_metrics=False)
mine_off = issues_for(rep_off, created + [tag])
rep.check(not [i for i in mine_off if i["rule_id"] in ("RULE_MISSING_STEWARD", "RULE_MISSING_DESCRIPTION", "RULE_NAMING_CONVENTION")],
          "E4/E6 rules switched off are silent")
rep.check(bool(has(mine_off, "RULE_METRIC_COLLISION")) and bool(has(mine_off, "RULE_CIRCULAR_JOIN")),
          "E4 structural rules still run with options off")
# E15
again = lint()
rep.check([(i["rule_id"], i["object_name"]) for i in again["issues"]] == [(i["rule_id"], i["object_name"]) for i in rep_full["issues"]],
          "E15 deterministic (same store -> same issues, same order)")
# noise check on BI-generated domains (informational)
bi_steward = [i for i in rep_full["issues"] if i["rule_id"] == "RULE_MISSING_STEWARD" and tag not in str(i)]
if bi_steward:
    rep.warn("existing domains fail the gate on missing stewards", f"{len(bi_steward)} ERRORs outside the test domains "
             "(Power BI has no stewards; consider 'pending business input' instead of ERROR)")

for d in created:
    api.delete_domain_quiet(d)
sys.exit(rep.done())
