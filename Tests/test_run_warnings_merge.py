"""Every model's non-fatal warnings end up on the run-level summary, which is
what the Runs page reads (run["summary"] alone is only the last model's)."""
from semabridge.api.services.run_service import _merge_model_warnings_into_run_summary

STUB = "Structure only (no data): 1 metric view(s) were published without data"


def test_multi_model_warnings_are_merged_with_model_labels():
    last = {"target_warnings": ["Last model warning"]}
    run = {
        "summary": dict(last),
        "results": [
            {"model": "Sales", "summary": {"target_warnings": [STUB], "source_warnings": ["Report X unreadable"]}},
            {"model": "Ops", "summary": last},
        ],
    }

    _merge_model_warnings_into_run_summary(run)

    assert run["summary"]["target_warnings"] == [
        "Last model warning",
        "[Sales] Report X unreadable",
        f"[Sales] {STUB}",
    ]


def test_single_model_run_is_not_labelled_or_duplicated():
    summary = {"target_warnings": [STUB]}
    run = {"summary": summary, "results": [{"model": "Sales", "summary": {"target_warnings": [STUB]}}]}

    _merge_model_warnings_into_run_summary(run)

    assert run["summary"]["target_warnings"] == [STUB]


def test_run_without_warnings_is_left_unchanged():
    run = {"summary": {"status": "SUCCESS"}, "results": [{"model": "Sales", "summary": {}}]}

    _merge_model_warnings_into_run_summary(run)

    assert "target_warnings" not in run["summary"]


def test_missing_summary_is_ignored():
    run = {"summary": None, "results": []}
    _merge_model_warnings_into_run_summary(run)
    assert run["summary"] is None
