"""Multi-model NL-to-SQL gold question benchmark runner & accuracy gate.

Evaluates every model's gold question set locally against NLQueryEngine,
reporting per-model accuracy %, refusal accuracy, and exact mismatches.
Exits non-zero if any model drops below its configured `min_accuracy`.

Usage:
    .venv/Scripts/python.exe ontology_tests/nl_gold_benchmark.py
    .venv/Scripts/python.exe ontology_tests/nl_gold_benchmark.py --json-out ontology_tests/out/nl_gold_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.ontology.federated_loader import FederatedOntologyLoader


def _norm_set(values: List[str]) -> set[str]:
    return {str(v or "").strip().casefold() for v in (values or []) if str(v or "").strip()}


def evaluate_gold_suite(
    suite_path: Path = REPO_ROOT / "ontology_tests" / "gold_questions" / "nl_gold_questions.yaml",
) -> Dict[str, Any]:
    """Run all gold questions per model and compute per-model and overall accuracy."""
    cfg = yaml.safe_load(suite_path.read_text(encoding="utf-8")) or {}
    models_cfg = cfg.get("models") or []

    model_reports: List[Dict[str, Any]] = []
    total_cases = 0
    total_passed = 0
    any_threshold_failed = False

    for entry in models_cfg:
        model_id = entry["model_id"]
        ont_path = REPO_ROOT / entry["ontology_file"]
        min_acc = float(entry.get("min_accuracy", 80.0))
        cases = entry.get("cases") or []

        dom = FederatedOntologyLoader.load_from_file(ont_path)
        fed = FederatedOntologyLoader.merge_domains([dom])
        idx = SemanticSearchIndex()
        idx.index_ontology(fed)
        engine = NLQueryEngine(idx, fed)

        passed_cnt = 0
        case_results: List[Dict[str, Any]] = []

        for case in cases:
            cid = case["id"]
            q = case["question"]
            expect_refusal = bool(case.get("expect_refusal", False))
            exp_metrics = _norm_set(case.get("expected_metrics") or [])
            exp_dims = _norm_set(case.get("expected_dimensions") or [])
            exp_filters = [str(f).strip().upper() for f in (case.get("expected_filters") or [])]

            plan = engine.resolve_query(q, semantic_view=dom.semantic_view_name)
            got_metrics = _norm_set(plan.detected_metrics)
            got_dims = _norm_set(plan.detected_dimensions)
            got_filters_upper = " ".join(plan.filters).upper()
            sql_upper = plan.generated_sql.upper()

            errors: List[str] = []
            if "FROM ANALYTICS" in sql_upper:
                errors.append("dummy 'FROM ANALYTICS' fallback emitted")

            if expect_refusal:
                if plan.answerable:
                    errors.append(f"expected refusal, got answerable=True (metrics={plan.detected_metrics})")
                if got_metrics:
                    errors.append(f"expected 0 metrics on refusal, got {plan.detected_metrics}")
            else:
                if not plan.answerable:
                    errors.append(f"expected answerable=True, got refusal ({plan.warnings})")
                if got_metrics != exp_metrics:
                    errors.append(f"metrics mismatch: expected {sorted(exp_metrics)}, got {sorted(got_metrics)}")

            if got_dims != exp_dims:
                errors.append(f"dims mismatch: expected {sorted(exp_dims)}, got {sorted(got_dims)}")

            for ef in exp_filters:
                if ef not in got_filters_upper and ef not in sql_upper:
                    errors.append(f"missing expected filter substring {ef!r} in {plan.filters}")

            ok = len(errors) == 0
            if ok:
                passed_cnt += 1
            case_results.append(
                {
                    "id": cid,
                    "question": q,
                    "passed": ok,
                    "detected_metrics": plan.detected_metrics,
                    "detected_dimensions": plan.detected_dimensions,
                    "filters": plan.filters,
                    "answerable": plan.answerable,
                    "errors": errors,
                }
            )

        accuracy = round((passed_cnt / len(cases) * 100.0) if cases else 100.0, 2)
        meets_threshold = accuracy >= min_acc
        if not meets_threshold:
            any_threshold_failed = True

        total_cases += len(cases)
        total_passed += passed_cnt
        model_reports.append(
            {
                "model_id": model_id,
                "total": len(cases),
                "passed": passed_cnt,
                "failed": len(cases) - passed_cnt,
                "accuracy_pct": accuracy,
                "min_accuracy_pct": min_acc,
                "meets_threshold": meets_threshold,
                "cases": case_results,
            }
        )

    overall_acc = round((total_passed / total_cases * 100.0) if total_cases else 100.0, 2)
    return {
        "overall_total": total_cases,
        "overall_passed": total_passed,
        "overall_accuracy_pct": overall_acc,
        "all_thresholds_met": not any_threshold_failed,
        "models": model_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        type=Path,
        default=REPO_ROOT / "ontology_tests" / "gold_questions" / "nl_gold_questions.yaml",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    report = evaluate_gold_suite(args.suite)
    for m in report["models"]:
        status = "PASS" if m["meets_threshold"] else "FAIL"
        print(
            f"[{status}] {m['model_id']}: {m['passed']}/{m['total']} ({m['accuracy_pct']:.1f}% vs min {m['min_accuracy_pct']:.1f}%)"
        )
        for c in m["cases"]:
            if not c["passed"]:
                print(f"    [MISS] {c['id']} {c['question']!r} -> {'; '.join(c['errors'])}")

    print(
        f"OVERALL ACCURACY: {report['overall_passed']}/{report['overall_total']} ({report['overall_accuracy_pct']:.1f}%)"
    )

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return 0 if report["all_thresholds_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

