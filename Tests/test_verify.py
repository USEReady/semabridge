"""Verify: certified measures are re-checked against Power BI after a sync;
mismatches become a run warning; nothing certified means nothing to do."""
import pytest

from semabridge.reconciliation import post_sync_verify as psv
from semabridge.reconciliation import project_verify as pv

CERTS = [
    {"metric_name": "Revenue", "status": "CERTIFIED", "dimensions": ["Region"],
     "dax_query": "EVALUATE ...", "sql_query": "SELECT region, MEASURE(revenue) ..."},
    {"metric_name": "Units", "status": "CERTIFIED", "dimensions": [],
     "dax_query": "EVALUATE ...", "sql_query": "SELECT MEASURE(units) ..."},
    {"metric_name": "Draft", "status": "FAILED", "dimensions": [], "dax_query": "x", "sql_query": "y"},
]


@pytest.fixture
def wired(monkeypatch):
    import semabridge.ai.bi_truth_service as bts
    import semabridge.reconciliation.certification_service as cs

    monkeypatch.setattr(cs, "get_latest_certifications", lambda pid: CERTS)
    monkeypatch.setattr(bts, "_detect_project_target", lambda pid: "DATABRICKS")
    dax = {"Revenue": [{"Region": "N", "Revenue": 10.0}], "Units": [{"Units": 5.0}]}
    sql = {"Revenue": [{"region": "N", "revenue": 10.0}], "Units": [{"units": 4.0}]}
    order = iter(["Revenue", "Units"])
    current = {}

    def fake_dax(pid, q):
        current["m"] = next(order)
        return dax[current["m"]]

    monkeypatch.setattr(pv, "run_project_dax", fake_dax)
    monkeypatch.setattr(psv, "_run_sql", lambda pid, q, target, owner: sql[current["m"]])


def test_mismatch_is_reported_and_warned(wired):
    summary = psv.verify_certified_measures("p1")
    verdicts = {r["metric_name"]: r["verdict"] for r in summary["results"]}
    assert verdicts == {"Revenue": "matched", "Units": "mismatch"}
    assert summary["target"] == "DATABRICKS"
    assert psv.warning_for(summary).startswith("Verify: 1 of 2 certified measure(s) no longer match Power BI")


def test_power_bi_unreachable_is_unverified_not_failed(monkeypatch, wired):
    def no_pbi(pid, q):
        raise pv.VerificationUnavailable("no Power BI source reachable")

    monkeypatch.setattr(pv, "run_project_dax", no_pbi)
    summary = psv.verify_certified_measures("p1")
    assert {r["verdict"] for r in summary["results"]} == {"unverified"}
    assert psv.warning_for(summary) is None


def test_nothing_certified_means_nothing_to_do(monkeypatch):
    import semabridge.reconciliation.certification_service as cs

    monkeypatch.setattr(cs, "get_latest_certifications", lambda pid: [])
    assert psv.verify_certified_measures("p1") is None


def test_column_count_mismatch_is_unverified():
    with pytest.raises(pv.VerificationUnavailable):
        psv._positional_rows([{"a": 1, "b": 2, "c": 3}], ["Region", "Revenue"])


def test_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_VERIFY_AFTER_SYNC", "false")
    assert psv.enabled() is False
