"""Pytest gate running the multi-model NL gold question benchmark.

Fails the test suite (and blocks CI merge) if any semantic model drops below
its configured minimum accuracy floor or emits dummy fallback SQL.
"""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ontology_tests"))

from nl_gold_benchmark import evaluate_gold_suite  # noqa: E402


def test_nl_gold_benchmark_meets_all_model_accuracy_thresholds():
    report = evaluate_gold_suite()
    failures = []
    for m in report["models"]:
        if not m["meets_threshold"]:
            misses = [f"{c['id']}: {'; '.join(c['errors'])}" for c in m["cases"] if not c["passed"]]
            failures.append(
                f"{m['model_id']} accuracy {m['accuracy_pct']}% < min {m['min_accuracy_pct']}% | misses={misses}"
            )
    assert report["all_thresholds_met"], "NL benchmark regression detected:\n" + "\n".join(failures)

