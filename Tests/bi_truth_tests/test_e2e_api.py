"""4. End to end, against a running server.

These run only with a base URL and a project that has cases:

    SB_BT_API_BASE=http://localhost:8002 \
    SB_BT_PROJECT=<project id> \
    pytest bi_truth_tests/test_e2e_api.py -v

Optional: SB_BT_TOKEN for a bearer token, SB_BT_RUN=1 to actually execute the
suite against the warehouse, which takes minutes and is off by default.

Everything here reads. A failure means the API answered wrongly, not that the
test changed something.
"""
from __future__ import annotations

import os

import pytest

requests = pytest.importorskip("requests")

BASE = os.environ.get("SB_BT_API_BASE", "").rstrip("/")
PROJECT = os.environ.get("SB_BT_PROJECT", "")
TOKEN = os.environ.get("SB_BT_TOKEN", "")
ALLOW_RUN = os.environ.get("SB_BT_RUN", "") == "1"

pytestmark = pytest.mark.skipif(
    not (BASE and PROJECT),
    reason="set SB_BT_API_BASE and SB_BT_PROJECT to run the end-to-end tests",
)

VERDICTS = {"PASS", "FAIL", "ROWS_DIFFER", "NOT_COMPARABLE"}


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def get(path, **params):
    params.setdefault("project_id", PROJECT)
    resp = requests.get(f"{BASE}{path}", params=params, headers=_headers(), timeout=60)
    assert resp.status_code == 200, f"{path} gave {resp.status_code}: {resp.text[:300]}"
    return resp.json()


def cases():
    data = get("/api/bi-truth/cases")
    return data.get("cases") or data if isinstance(data, list) else data.get("cases", [])


def runs():
    data = get("/api/bi-truth/runs")
    return data.get("runs") or (data if isinstance(data, list) else [])


# ---------------------------------------------------------------- cases

def test_4_1_cases_load_for_the_project():
    assert isinstance(cases(), list)


def test_4_2_every_case_carries_its_question_and_expected_sql():
    found = cases()
    if not found:
        pytest.skip("this project has no cases; generate some first")
    for c in found[:10]:
        assert c.get("question"), f"case {c.get('id')} has no question"
        assert c.get("expected_sql"), f"case {c.get('id')} has no expected SQL"


def test_4_3_a_case_reports_when_its_expected_value_was_captured():
    """A benchmark nobody has refreshed is the likeliest cause of a false failure."""
    found = [c for c in cases() if c.get("expected_value") is not None]
    if not found:
        pytest.skip("no case has an expected value yet")
    missing = [c.get("id") for c in found[:10] if not c.get("expected_evaluated_at")]
    assert not missing, f"cases with an expected value but no capture date: {missing}"


def test_4_4_windows_round_trip():
    found = [c for c in cases() if c.get("windows")]
    if not found:
        pytest.skip("no case has windows yet")
    for c in found[:5]:
        for w in c["windows"]:
            assert w.get("window_key"), f"a window on case {c.get('id')} has no key"


def test_4_5_every_window_has_its_own_expected_value():
    """A quarter compared against an annual figure always fails."""
    found = [c for c in cases() if c.get("windows")]
    if not found:
        pytest.skip("no case has windows yet")
    bare = [
        (c.get("id"), w.get("window_key"))
        for c in found for w in c["windows"]
        if w.get("expected_value") is None
    ]
    assert not bare, f"windows with no expected value of their own: {bare[:5]}"


# ---------------------------------------------------------------- runs

def test_4_6_run_history_loads():
    assert isinstance(runs(), list)


def test_4_7_every_result_carries_a_known_verdict():
    history = runs()
    if not history:
        pytest.skip("this project has no runs yet")
    results = history[0].get("results") or []
    if not results:
        pytest.skip("the most recent run recorded no results")
    unknown = {
        str(r.get("verdict") or r.get("status") or "")
        for r in results
    } - VERDICTS - {""}
    assert not unknown, f"unexpected verdicts: {sorted(unknown)}"


def test_4_8_a_not_comparable_result_is_not_counted_as_a_pass():
    history = runs()
    if not history:
        pytest.skip("no runs yet")
    r = history[0]
    results = r.get("results") or []
    not_comparable = [x for x in results if str(x.get("verdict", "")).upper() == "NOT_COMPARABLE"]
    if not not_comparable:
        pytest.skip("the most recent run had nothing uncomparable")
    passed = int(r.get("passed") or r.get("passed_cases") or 0)
    compared = len(results) - len(not_comparable)
    assert passed <= compared, (
        "the passed count includes results that were never compared"
    )


def test_4_9_every_result_explains_itself():
    history = runs()
    if not history:
        pytest.skip("no runs yet")
    failures = [
        x for x in (history[0].get("results") or [])
        if str(x.get("verdict", "")).upper() in ("FAIL", "ROWS_DIFFER", "NOT_COMPARABLE")
    ]
    if not failures:
        pytest.skip("the most recent run had no failures")
    silent = [x.get("case_id") for x in failures if not (x.get("reason") or x.get("error"))]
    assert not silent, f"results that failed without saying why: {silent[:5]}"


def test_4_10_the_counts_in_a_run_agree_with_its_results():
    history = runs()
    if not history:
        pytest.skip("no runs yet")
    r = history[0]
    results = r.get("results") or []
    if not results:
        pytest.skip("no results in the most recent run")
    total = r.get("total") or r.get("total_cases")
    if total is None:
        pytest.skip("the run records no total")
    assert int(total) == len(results), (
        f"the run says {total} cases but carries {len(results)} results"
    )


# ---------------------------------------------------------------- running it

@pytest.mark.skipif(not ALLOW_RUN, reason="set SB_BT_RUN=1 to execute the suite")
def test_4_11_running_the_suite_returns_a_result_per_case():
    resp = requests.post(
        f"{BASE}/api/bi-truth/run",
        json={"project_id": PROJECT},
        headers=_headers(),
        timeout=600,
    )
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:300]}"
    data = resp.json()
    results = data.get("results") or []
    assert results, "the run produced no results"
    for r in results:
        assert r.get("verdict") or r.get("passed") is not None, (
            f"a result for {r.get('case_id')} has no verdict"
        )
