"""2 to 5. Tracing, the four categories, consumers, limits and the model resolver."""
from __future__ import annotations

import pytest

from blast_radius_tests import adapter
from blast_radius_tests.conftest import (
    ADDITION,
    DELETION,
    DESCRIPTION_DRIFT,
    EXPRESSION_DRIFT,
    FakeAdapter,
    metric,
    sim,
)

S = adapter.names(adapter.simulator, "BlastRadiusSimulator", "Metric", "build_graph")
RES = adapter.names(adapter.resolver, "resolve_model", "ONTOLOGY", "WAREHOUSE", "NOT_FOUND")
L = adapter.risk.RiskLevel


def names_of(report):
    return sorted(n.name for n in report.nodes)


# ---------------------------------------------------------------- 2. tracing

def test_2_1_a_direct_dependant_is_found(three_level_model):
    r = sim(three_level_model).simulate("T1.AMOUNT", "COLUMN", category=EXPRESSION_DRIFT)
    assert "BASE_TOTAL" in names_of(r)


def test_2_2_the_transitive_chain_is_walked(three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT)
    assert names_of(r) == ["MIDDLE_RATE", "TOP_INDEX"]


def test_2_3_an_unrelated_metric_is_not_listed(three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT)
    assert "UNRELATED" not in names_of(r)


def test_2_4_identifier_boundaries_are_respected():
    metrics = [metric("USES_AMT", "SUM(T1.AMT)"), metric("USES_AMT_NET", "SUM(T1.AMT_NET)")]
    r = sim(metrics).simulate("AMT", "COLUMN", category=EXPRESSION_DRIFT)
    assert names_of(r) == ["USES_AMT"]


def test_2_5_the_target_is_not_listed_as_its_own_dependant(three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT)
    assert "BASE_TOTAL" not in names_of(r)


def test_2_6_a_cycle_does_not_hang():
    metrics = [metric("A", "B + 1", derives_from=["B"]), metric("B", "A + 1", derives_from=["A"])]
    r = sim(metrics).simulate("A", "METRIC", category=EXPRESSION_DRIFT)
    assert "B" in names_of(r)


# ---------------------------------------------------------------- 3. categories

def test_3_1_every_category_returns_the_same_shape(category, three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTAL", "METRIC", category=category,
                                        expression="SUM(T1.AMOUNT)", table="T1")
    for attribute in ("target", "change_type", "category", "status", "risk",
                      "nodes", "edges", "messages"):
        assert hasattr(r, attribute), f"{category} result is missing {attribute}"


def test_3_2_an_addition_never_reports_not_found(three_level_model):
    r = sim(three_level_model).simulate("NEW_IN_WAREHOUSE", "METRIC", category=ADDITION,
                                        expression="SUM(T1.AMOUNT)", table="T1")
    assert r.status == "ok"
    assert "not deployed" in r.messages["summary"]


def test_3_3_an_addition_reports_what_it_references(three_level_model):
    r = sim(three_level_model).simulate("NEW_IN_WAREHOUSE", "METRIC", category=ADDITION,
                                        expression="SUM(T1.AMOUNT) / T1.COUNT", table="T1")
    assert any("T1.AMOUNT" in ref for ref in r.warehouse_references)
    assert any("table T1" in ref for ref in r.warehouse_references)


def test_3_4_an_addition_suggests_no_other_object(three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTALL", "METRIC", category=ADDITION,
                                        expression="SUM(T1.AMOUNT)")
    assert r.suggestions == []


def test_3_5_a_deletion_lists_what_breaks(three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTAL", "METRIC", category=DELETION)
    assert names_of(r) == ["MIDDLE_RATE", "TOP_INDEX"]
    assert r.risk == L.BREAKING


def test_3_6_an_expression_change_is_advisory_not_breaking(three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT)
    assert r.risk == L.ADVISORY


def test_3_7_a_description_change_lists_semantic_dependants():
    metrics = [
        metric("TARGET", "SUM(T1.X)", "A metric."),
        metric("REFERS", "SUM(T1.Y)", "Depends on TARGET for its meaning."),
        metric("ELSEWHERE", "SUM(T1.Z)", "Unrelated."),
    ]
    r = sim(metrics).simulate("TARGET", "METRIC", category=DESCRIPTION_DRIFT)
    assert names_of(r) == ["REFERS"]


def test_3_8_a_description_change_lists_certified_queries():
    metrics = [metric("TARGET", "SUM(T1.X)")]
    queries = [{"question": "How much target?", "sql": "SELECT TARGET FROM V"}]
    r = sim(metrics, certified_queries=queries).simulate(
        "TARGET", "METRIC", category=DESCRIPTION_DRIFT)
    assert "How much target?" in names_of(r)


def test_3_9_a_description_change_is_not_breaking():
    metrics = [metric("TARGET", "SUM(T1.X)"),
               metric("REFERS", "SUM(T1.Y)", "Uses TARGET.")]
    r = sim(metrics).simulate("TARGET", "METRIC", category=DESCRIPTION_DRIFT)
    assert r.risk != L.BREAKING


# ---------------------------------------------------------------- 4. empty and consumers

def test_4_1_an_object_with_no_dependants_says_so():
    r = sim([metric("LONELY", "SUM(T1.X)")]).simulate("LONELY", "METRIC",
                                                      category=EXPRESSION_DRIFT)
    assert r.total == 0
    assert "Nothing depends on" in r.messages["summary"]
    assert r.status == "ok"


def test_4_2_an_empty_result_is_not_an_error():
    r = sim([metric("LONELY", "SUM(T1.X)")]).simulate("LONELY", "METRIC",
                                                      category=EXPRESSION_DRIFT)
    assert r.risk == L.INFO


def test_4_3_no_consumer_resolver_gives_a_reason_not_silence(three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT)
    assert "consumers" in r.messages


def test_4_4_a_consumer_resolver_adds_its_assets(three_level_model):
    r = sim(three_level_model,
            consumer_resolver=lambda v: ["Dashboard one"]).simulate(
        "BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT, view_name="DB.SCH.V")
    assert "Dashboard one" in names_of(r)


def test_4_5_a_failing_consumer_resolver_is_reported_not_swallowed(three_level_model):
    def boom(view):
        raise RuntimeError("workspace unreachable")

    r = sim(three_level_model, consumer_resolver=boom).simulate(
        "BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT, view_name="DB.SCH.V")
    assert "workspace unreachable" in r.messages["consumers"]


def test_4_6_nothing_is_invented_when_no_consumer_is_found(three_level_model):
    r = sim(three_level_model,
            consumer_resolver=lambda v: []).simulate(
        "BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT, view_name="DB.SCH.V")
    assert not any(n.item_type == "consumer_asset" for n in r.nodes)
    assert "no downstream assets" in r.messages["consumers"]


# ---------------------------------------------------------------- 5. resolver

def test_5_1_an_object_in_the_ontology_resolves_there(three_level_model):
    res = RES.resolve_model("BASE_TOTAL", ontology_objects=three_level_model)
    assert res.source == RES.ONTOLOGY


def test_5_2_an_object_only_in_the_warehouse_is_found_through_the_adapter():
    fake = FakeAdapter(objects=[metric("ONLY_LIVE", "SUM(T1.X)")])
    res = RES.resolve_model("ONLY_LIVE", ontology_objects=[], adapter=fake,
                            view_name="DB.SCH.V")
    assert res.source == RES.WAREHOUSE
    assert ("fetch", "DB.SCH.V") in fake.calls


def test_5_3_an_object_in_neither_place_is_not_found():
    fake = FakeAdapter(objects=[])
    res = RES.resolve_model("MISSING", ontology_objects=[], adapter=fake, view_name="DB.SCH.V")
    assert res.source == RES.NOT_FOUND
    assert res.reason


def test_5_4_a_warehouse_failure_is_a_reason_not_a_crash():
    fake = FakeAdapter(raise_on_fetch="authentication failed")
    res = RES.resolve_model("ANY", ontology_objects=[], adapter=fake, view_name="DB.SCH.V")
    assert res.source == RES.NOT_FOUND
    assert "authentication failed" in res.reason


def test_5_5_no_adapter_means_no_warehouse_call():
    res = RES.resolve_model("MISSING", ontology_objects=[])
    assert res.source == RES.NOT_FOUND


def test_5_6_nothing_in_this_path_writes_to_the_warehouse(three_level_model):
    fake = FakeAdapter(objects=three_level_model)
    sim(three_level_model, adapter=fake).simulate(
        "BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT, view_name="DB.SCH.V")
    assert all(call[0] in ("fetch", "parse") for call in fake.calls)


# ---------------------------------------------------------------- 6. limits and graph

def test_6_1_a_deep_chain_stops_at_the_depth_limit():
    metrics = [metric("M0", "SUM(T1.X)")]
    for i in range(1, 12):
        metrics.append(metric(f"M{i}", f"M{i - 1} + 1", derives_from=[f"M{i - 1}"]))
    r = sim(metrics, max_depth=3).simulate("M0", "METRIC", category=EXPRESSION_DRIFT)
    assert r.truncated
    assert r.total < 11


def test_6_2_a_wide_result_stops_at_the_node_limit():
    metrics = [metric("HUB", "SUM(T1.X)")]
    metrics += [metric(f"USER_{i}", "HUB + 1", derives_from=["HUB"]) for i in range(60)]
    r = sim(metrics, max_nodes=10).simulate("HUB", "METRIC", category=EXPRESSION_DRIFT)
    assert r.truncated


def test_6_3_the_graph_carries_every_node_and_edge(three_level_model):
    r = sim(three_level_model).simulate("BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT)
    g = S.build_graph(r)
    assert len(g["nodes"]) == r.total + 1       # the target plus its dependants
    assert len(g["edges"]) == len(r.edges)


def test_6_4_every_graph_node_carries_a_risk_level(three_level_model):
    g = S.build_graph(sim(three_level_model).simulate(
        "BASE_TOTAL", "METRIC", category=DELETION))
    assert all(n["risk"] in ("BREAKING", "ADVISORY", "INFO") for n in g["nodes"])


def test_6_5_the_target_is_marked_in_the_graph(three_level_model):
    g = S.build_graph(sim(three_level_model).simulate(
        "BASE_TOTAL", "METRIC", category=EXPRESSION_DRIFT))
    targets = [n for n in g["nodes"] if n["is_target"]]
    assert len(targets) == 1 and targets[0]["id"] == "BASE_TOTAL"


def test_6_6_an_empty_result_still_builds_a_graph():
    g = S.build_graph(sim([metric("LONELY", "SUM(T1.X)")]).simulate(
        "LONELY", "METRIC", category=EXPRESSION_DRIFT))
    assert len(g["nodes"]) == 1 and g["edges"] == []


def test_6_7_a_missing_target_is_refused():
    with pytest.raises(ValueError):
        sim([]).simulate("", "METRIC", category=EXPRESSION_DRIFT)
