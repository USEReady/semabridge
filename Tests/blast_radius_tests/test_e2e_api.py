"""7. End to end, against a running server.

These run only with a base URL and a project that has a deployed view:

    SB_BR_API_BASE=http://localhost:8002 \
    SB_BR_PROJECT=<project id> \
    SB_BR_VIEW=<fully qualified view> \
    SB_BR_METRIC=<a metric that other metrics depend on> \
    pytest blast_radius_tests/test_e2e_api.py -v

Optional: SB_BR_TOKEN for a bearer token, SB_BR_OTHER_PROJECT for the cross-project
check, SB_BR_LONELY_METRIC for an object with no dependants.

Everything here is read-only. A failure means the API answered wrongly, not that the
test wrote something.
"""
from __future__ import annotations

import os

import pytest

requests = pytest.importorskip("requests")

BASE = os.environ.get("SB_BR_API_BASE", "").rstrip("/")
PROJECT = os.environ.get("SB_BR_PROJECT", "")
VIEW = os.environ.get("SB_BR_VIEW", "")
METRIC = os.environ.get("SB_BR_METRIC", "")
OTHER_PROJECT = os.environ.get("SB_BR_OTHER_PROJECT", "")
LONELY = os.environ.get("SB_BR_LONELY_METRIC", "")
TOKEN = os.environ.get("SB_BR_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not (BASE and PROJECT and METRIC),
    reason="set SB_BR_API_BASE, SB_BR_PROJECT and SB_BR_METRIC to run the end-to-end tests",
)

PATH = "/api/governance/blast-radius"
RISK_LEVELS = {"BREAKING", "ADVISORY", "INFO"}


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def call(**body):
    payload = {"project_id": PROJECT}
    if VIEW:
        payload["view"] = VIEW
    payload.update(body)
    resp = requests.post(f"{BASE}{PATH}", json=payload, headers=_headers(), timeout=60)
    return resp


def ok(**body):
    resp = call(**body)
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:400]}"
    return resp.json()


def node_risks(data):
    nodes = data.get("nodes") or data.get("affected_metrics") or []
    return [str(n.get("risk") or n.get("severity") or "") for n in nodes]


# ---------------------------------------------------------------- the four categories

@pytest.mark.parametrize("category,change", [
    ("ADDITION", "ADD"),
    ("DELETION", "DROP"),
    ("EXPRESSION_DRIFT", "MODIFY_EXPRESSION"),
    ("DESCRIPTION_DRIFT", "MODIFY_DESCRIPTION"),
])
def test_7_1_every_category_answers_200(category, change):
    data = ok(target_name=METRIC, target_type="METRIC",
              change_type=change, category=category)
    assert "status" in data


def test_7_2_an_addition_never_reports_not_found():
    data = ok(target_name="OBJECT_THAT_IS_NOT_DEPLOYED", target_type="METRIC",
              change_type="ADD", category="ADDITION",
              expression="SUM(SOME_TABLE.SOME_COLUMN)", table_context="SOME_TABLE")
    assert str(data.get("status", "")).lower() != "not_found", \
        "an addition must not report not_found; it is absent from the model by definition"


def test_7_3_an_addition_suggests_no_other_object():
    data = ok(target_name="OBJECT_THAT_IS_NOT_DEPLOYED", target_type="METRIC",
              change_type="ADD", category="ADDITION")
    assert not data.get("suggestions"), \
        "suggesting a different object on the finding path risks acting on the wrong one"


def test_7_4_a_deletion_reports_dependants_or_says_there_are_none():
    data = ok(target_name=METRIC, target_type="METRIC",
              change_type="DROP", category="DELETION")
    has_nodes = bool(data.get("nodes") or data.get("affected_metrics"))
    has_message = bool((data.get("messages") or data.get("empty_messages") or {}))
    assert has_nodes or has_message, "an empty result must explain itself"


# ---------------------------------------------------------------- risk per node

def test_7_5_every_node_carries_one_of_the_three_levels():
    data = ok(target_name=METRIC, target_type="METRIC",
              change_type="DROP", category="DELETION")
    risks = node_risks(data)
    if not risks:
        pytest.skip(f"'{METRIC}' has no dependants; set SB_BR_METRIC to one that does")
    unknown = [r for r in risks if r.upper() not in RISK_LEVELS]
    assert not unknown, f"unexpected risk levels: {sorted(set(unknown))}"


def test_7_6_dropping_is_at_least_as_severe_as_changing_an_expression():
    dropped = ok(target_name=METRIC, target_type="METRIC",
                 change_type="DROP", category="DELETION")
    changed = ok(target_name=METRIC, target_type="METRIC",
                 change_type="MODIFY_EXPRESSION", category="EXPRESSION_DRIFT")
    order = {"INFO": 0, "ADVISORY": 1, "BREAKING": 2}

    def level(d):
        top = str(d.get("risk") or d.get("severity") or "INFO").upper()
        return order.get(top, 0)

    assert level(dropped) >= level(changed)


# ---------------------------------------------------------------- the graph

def test_7_7_the_graph_edges_match_the_nodes():
    data = ok(target_name=METRIC, target_type="METRIC",
              change_type="DROP", category="DELETION")
    edges = data.get("edges") or data.get("dependency_graph_edges") or []
    nodes = data.get("nodes") or data.get("affected_metrics") or []
    if not nodes:
        pytest.skip("no dependants to graph")
    named = {str(n.get("name") or n.get("id")) for n in nodes} | {METRIC}
    dangling = [e for e in edges
                if str(e.get("to")) not in named and str(e.get("from")) not in named]
    assert not dangling, f"edges point at nodes that are not in the result: {dangling[:3]}"


# ---------------------------------------------------------------- empty and errors

def test_7_8_an_object_with_no_dependants_explains_itself():
    if not LONELY:
        pytest.skip("set SB_BR_LONELY_METRIC to an object nothing depends on")
    data = ok(target_name=LONELY, target_type="METRIC",
              change_type="MODIFY_EXPRESSION", category="EXPRESSION_DRIFT")
    messages = data.get("messages") or data.get("empty_messages") or {}
    assert messages, "an empty result must carry a message, not just zero nodes"


def test_7_9_an_empty_target_is_refused():
    resp = call(target_name="", target_type="METRIC", change_type="DROP")
    assert resp.status_code in (400, 422), f"expected a refusal, got {resp.status_code}"


def test_7_10_unknown_extra_fields_do_not_break_the_request():
    resp = call(target_name=METRIC, target_type="METRIC", change_type="DROP",
                category="DELETION", some_future_field="value")
    assert resp.status_code == 200, \
        "the schema must tolerate extra context fields rather than returning 422"


def test_7_11_another_project_is_not_read():
    if not OTHER_PROJECT:
        pytest.skip("set SB_BR_OTHER_PROJECT to check cross-project isolation")
    mine = ok(target_name=METRIC, target_type="METRIC",
              change_type="DROP", category="DELETION")
    resp = requests.post(
        f"{BASE}{PATH}",
        json={"project_id": OTHER_PROJECT, "target_name": METRIC,
              "target_type": "METRIC", "change_type": "DROP", "category": "DELETION"},
        headers=_headers(), timeout=60,
    )
    if resp.status_code == 403:
        return  # refused outright, which is the stronger answer
    assert resp.status_code == 200
    theirs = resp.json()
    assert theirs != mine, \
        "a different project returned an identical report; the lookup is not project scoped"


# ---------------------------------------------------------------- read only

def test_7_12_the_call_is_repeatable():
    first = ok(target_name=METRIC, target_type="METRIC",
               change_type="DROP", category="DELETION")
    second = ok(target_name=METRIC, target_type="METRIC",
                change_type="DROP", category="DELETION")
    assert first.get("total_impacted_items") == second.get("total_impacted_items"), \
        "two identical read-only calls returned different results"
