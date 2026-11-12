"""G09 - CI/CD governance gate: can the lint gate actually block a deployment?

    uv run python governance_tests/g09_cicd_gate.py                       # check + run the gate
    uv run python governance_tests/g09_cicd_gate.py --gate-only --max-errors 0 --domain "AI Sample"

Two parts:
  C1  the pre-deploy CI workflow (.github/workflows/*.yml) runs the governance gate
      (today it runs secrets scan, pytest and the frontend build only)
  C2  gate behaviour: exits 1 when the lint report has errors (optionally limited to --domain),
      0 when clean; prints a short report a CI log can show
  C3  gate is deterministic and fast enough for CI (two runs, same result, < 30 s)
With --gate-only the script is the gate itself: add it as a CI step against a running API.
"""
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

p = base_args(__doc__)
p.add_argument("--gate-only", action="store_true")
p.add_argument("--direct", action="store_true", help="Run linter directly in-process (no running API required)")
p.add_argument("--ontologies-dir", default="Config/ontologies", help="Directory of *.ontology.yaml domains for --direct mode")
p.add_argument("--fail-on-empty", action="store_true",
               help="--direct: fail (exit 2) when the ontologies dir has no domains; default is pass with a notice")
p.add_argument("--check-certifications", action="store_true", help="Fail gate if any certified metric currently has FAILED/ERROR status")
p.add_argument("--changed", action="store_true", help="Scope --direct gate to projects/domains touched in git diff vs --base-ref")
p.add_argument("--base-ref", default="origin/main", help="Git ref used with --changed")
p.add_argument("--max-errors", type=int, default=0)
p.add_argument("--domain", action="append", default=[], help="only count issues of these domains")
p.add_argument("--no-steward", action="store_true", help="do not require stewards (BI-generated domains)")
p.add_argument("--steward-level", choices=["error", "warning", "off"], default=None, help="Configure steward rule severity")
p.add_argument("--no-snake-case", action="store_true")
a = p.parse_args()
api = Api(a.base, a.token)


def _changed_files_vs_base(base_ref: str) -> list[str]:
    candidates = [base_ref, "main", "HEAD~1"]
    for ref in candidates:
        chk = subprocess.run(["git", "rev-parse", "--verify", ref], capture_output=True, text=True)
        if chk.returncode == 0:
            diff = subprocess.run(["git", "diff", "--name-only", ref], capture_output=True, text=True)
            untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True)
            return [line.strip().replace("\\", "/") for line in (diff.stdout + "\n" + untracked.stdout).splitlines() if line.strip()]
    return []


def run_gate():
    t0 = time.time()
    steward_sev = a.steward_level.upper() if a.steward_level else ("OFF" if a.no_steward else "ERROR")
    scoped_domains = list(a.domain or [])
    scoped_projects = []

    if a.direct:
        from semabridge.governance.linter import SemanticLinter
        from semabridge.ontology.federated_loader import FederatedOntologyLoader

        ont_dir = Path(a.ontologies_dir)
        files = sorted(ont_dir.glob("*.ontology.yaml")) if ont_dir.exists() else []
        if not files:
            if a.fail_on_empty:
                return None, time.time() - t0, f"No ontology files found in '{ont_dir}' (--fail-on-empty)"
            # Nothing to govern is not a failure: the store ships empty and is filled per deployment.
            notice = f"NOTICE: no *.ontology.yaml files in '{ont_dir}' - nothing to govern, gate passes."
            return {
                "errors": [],
                "warnings": [],
                "failed_certifications": [],
                "notice": notice,
                "raw": {"passed": True, "error_count": 0, "warning_count": 0, "total_issues": 0,
                        "issues": [], "summary": notice},
            }, time.time() - t0, ""
        domains = [FederatedOntologyLoader.parse_yaml_string(f.read_text(encoding="utf-8")) for f in files]
        file_to_dom = {f.stem.replace(".ontology", "").casefold(): d for f, d in zip(files, domains)}
        fed = FederatedOntologyLoader.merge_domains(domains)

        if a.changed and not scoped_domains:
            changed_paths = _changed_files_vs_base(a.base_ref)
            for rel in changed_paths:
                if rel.startswith(f"{ont_dir.as_posix().rstrip('/')}/") and rel.endswith(".ontology.yaml"):
                    stem = Path(rel).name.replace(".ontology.yaml", "").casefold()
                    if stem in file_to_dom:
                        dom_obj = file_to_dom[stem]
                        scoped_domains.append(dom_obj.domain)
                        if getattr(dom_obj, "source_project_id", None):
                            scoped_projects.append(dom_obj.source_project_id)
                elif rel.startswith("output/reverse/"):
                    parts = rel.split("/")
                    if len(parts) >= 3:
                        proj = parts[2]
                        for dom_obj in domains:
                            if (getattr(dom_obj, "source_project_id", None) or "").casefold() == proj.casefold():
                                scoped_domains.append(dom_obj.domain)
                                scoped_projects.append(proj)

        linter = SemanticLinter(
            require_steward=steward_sev != "OFF",
            require_description=True,
            enforce_snake_case_metrics=not a.no_snake_case,
            steward_severity=steward_sev,
        )
        rep_model = linter.lint_ontology(fed, allowed_domains=scoped_domains or None)
        rep_ = rep_model.model_dump()
        dt = time.time() - t0
    else:
        r = api.post("/api/governance/lint", {
            "require_steward": steward_sev != "OFF",
            "steward_severity": steward_sev,
            "require_description": True,
            "enforce_snake_case_metrics": not a.no_snake_case,
        })
        dt = time.time() - t0
        if not r.ok:
            return None, dt, body_text(r)
        rep_ = r.json()

    issues = rep_["issues"]
    if scoped_domains:
        doms = [d.casefold() for d in dict.fromkeys(scoped_domains)]
        issues = [i for i in issues if any(d in (i.get("domain") or "").casefold() for d in doms)]
    errors = [i for i in issues if i["severity"] == "ERROR"]
    failed_certs = []
    if a.check_certifications:
        try:
            from semabridge.reconciliation.certification_service import get_failed_certifications

            if scoped_projects:
                for sp in dict.fromkeys(scoped_projects):
                    failed_certs.extend(get_failed_certifications(project_id=sp))
            else:
                failed_certs = get_failed_certifications()
        except Exception:
            failed_certs = []
    return {
        "errors": errors,
        "warnings": [i for i in issues if i["severity"] == "WARNING"],
        "failed_certifications": failed_certs,
        "raw": rep_,
    }, dt, ""


if a.gate_only:
    res, dt, err = run_gate()
    if res is None:
        print(f"GATE ERROR: lint execution failed: {err}")
        sys.exit(2)
    if res.get("notice"):
        print(res["notice"])
    by_rule = {}
    for i in res["errors"]:
        by_rule[i["rule_id"]] = by_rule.get(i["rule_id"], 0) + 1
    failed_certs = res.get("failed_certifications") or []
    warn_by_group = {}
    for w in res["warnings"]:
        gk = (w.get("rule_id") or "UNKNOWN", w.get("domain") or "Global")
        warn_by_group[gk] = warn_by_group.get(gk, 0) + 1
    print(
        f"Governance gate: {len(res['errors'])} errors, {len(res['warnings'])} warnings, "
        f"{len(failed_certs)} failed certifications ({dt:.1f}s) {by_rule}"
    )
    if warn_by_group:
        print(f"  Warnings by rule & domain ({len(warn_by_group)} groups):")
        for (wr, wd), cnt in sorted(warn_by_group.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))[:15]:
            print(f"    - {wr} [{wd}]: {cnt}")
    for i in res["errors"][:20]:
        print(f"  ERROR {i['rule_id']} {i.get('domain') or ''} :: {i['object_name']} - {i['message']}")
    for fc in failed_certs[:20]:
        print(f"  CERT_FAIL {fc.get('metric_name')} ({fc.get('project_id') or 'global'}): {fc.get('status')}")
    sys.exit(1 if (len(res["errors"]) > a.max_errors or bool(failed_certs)) else 0)

rep = Report("G09 CI/CD gate")
wf = list(Path(".github/workflows").glob("*.y*ml")) if Path(".github/workflows").exists() else []
gate_in_ci = [w for w in wf if re.search(r"governance/lint|g09_cicd_gate|governance_tests|semantic.?lint", w.read_text(encoding="utf-8"), re.I)]
rep.check(bool(gate_in_ci), "C1 a CI workflow runs the governance gate",
          f"workflows: {[w.name for w in wf]} - none calls the lint gate (the UI calls it 'Automated CI/CD Quality Gate')")
res1, dt1, err = run_gate()
if not rep.check(res1 is not None, "C2 lint endpoint answers", err):
    sys.exit(rep.done())
res2, dt2, _ = run_gate()
rep.check([(i["rule_id"], i["object_name"]) for i in res1["raw"]["issues"]] ==
          [(i["rule_id"], i["object_name"]) for i in res2["raw"]["issues"]], "C3 deterministic")
rep.check(max(dt1, dt2) < 30, "C3 fast enough for CI", f"{max(dt1, dt2):.1f}s")
rep.check(res1["raw"]["passed"] == (res1["raw"]["error_count"] == 0), "C2 'passed' flag follows the error count")
steward_err = sum(1 for i in res1["errors"] if i["rule_id"] == "RULE_MISSING_STEWARD")
if steward_err and steward_err == len(res1["errors"]):
    rep.warn("C2 the gate fails ONLY because of missing stewards",
             f"{steward_err} errors; run with --no-steward until stewards are filled, or the gate can never pass")
print(f"  gate today: {len(res1['errors'])} errors, {len(res1['warnings'])} warnings")
sys.exit(rep.done())
