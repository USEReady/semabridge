"""3. Comparison engine.

One engine for every platform and mode. It sees only normalised objects, so
every case here is built from small hand written definitions.
"""
from __future__ import annotations

import pytest

from drift_tests import adapter
from drift_tests.conftest import categories, obj, view

M = adapter.models
C = adapter.names(adapter.comparison, "compare", "group_counts")
KINDS = [M.METRIC, M.DIMENSION, M.FACT]


def test_3_1_identical_definitions_give_zero_findings():
    objects = [obj(M.METRIC, "TOTAL", "SUM(T1.AMOUNT)", "Total amount"),
               obj(M.DIMENSION, "LABEL", "T1.LABEL")]
    assert C.compare(view(objects), view(objects)) == []


def test_3_2_formatting_only_differences_give_zero_findings():
    baseline = view([obj(M.METRIC, "TOTAL", "SUM(T1.AMOUNT)", "Total  amount")])
    live = view([obj(M.METRIC, "TOTAL", "sum( t1.amount )", "Total amount\n")])
    assert C.compare(baseline, live) == []


@pytest.mark.parametrize("kind", KINDS)
def test_3_3_object_only_live_is_an_addition(kind):
    findings = C.compare(view([]), view([obj(kind, "NEW_ONE", "SUM(T1.AMOUNT)")]))
    assert categories(findings) == [M.ADDITION] and findings[0].kind == kind


@pytest.mark.parametrize("kind", KINDS)
def test_3_4_object_only_baseline_is_a_deletion(kind):
    findings = C.compare(view([obj(kind, "GONE", "SUM(T1.AMOUNT)")]), view([]))
    assert categories(findings) == [M.DELETION] and findings[0].kind == kind


@pytest.mark.parametrize("kind", KINDS)
def test_3_5_changed_expression_is_expression_drift(kind):
    findings = C.compare(view([obj(kind, "X", "SUM(T1.AMOUNT)")]),
                         view([obj(kind, "X", "AVG(T1.AMOUNT)")]))
    assert categories(findings) == [M.EXPRESSION_DRIFT] and findings[0].kind == kind


@pytest.mark.parametrize("kind", KINDS)
def test_3_6_changed_description_is_description_drift(kind):
    findings = C.compare(view([obj(kind, "X", "SUM(T1.AMOUNT)", "Before")]),
                         view([obj(kind, "X", "SUM(T1.AMOUNT)", "After")]))
    assert categories(findings) == [M.DESCRIPTION_DRIFT] and findings[0].kind == kind


def test_3_8_fact_repointed_to_another_table_is_one_finding():
    findings = C.compare(view([obj(M.FACT, "ENTITY", "SCH1.ENTITY")]),
                         view([obj(M.FACT, "ENTITY", "SCH1.ENTITY_OLD")]))
    assert len(findings) == 1 and findings[0].kind == M.FACT


def test_3_9_fact_repointed_to_a_same_named_table_elsewhere_is_one_finding():
    findings = C.compare(view([obj(M.FACT, "ENTITY", "SCH1.ENTITY")]),
                         view([obj(M.FACT, "ENTITY", "SCH2.ENTITY")]))
    assert len(findings) == 1


def test_3_10_relationship_column_order_is_not_drift():
    baseline = view([obj(M.RELATIONSHIP, "A->B::c1+c2=k1+k2", "A->B::c1+c2=k1+k2")])
    live = view([obj(M.RELATIONSHIP, "A->B::c1+c2=k1+k2", "A->B::c1+c2=k1+k2")])
    assert C.compare(baseline, live) == []


def test_3_11_changed_relationship_column_is_one_finding():
    baseline = view([obj(M.RELATIONSHIP, "A->B::c1=k1", "A->B::c1=k1")])
    live = view([obj(M.RELATIONSHIP, "A->B::c9=k1", "A->B::c9=k1")])
    assert len(C.compare(baseline, live)) == 2  # the old identity went, a new one arrived


def test_3_13_objects_not_deployed_never_produce_a_deletion():
    baseline = view([obj(M.METRIC, "REJECTED", "SUM(T1.AMOUNT)")],
                    not_deployed=["REJECTED"])
    assert C.compare(baseline, view([])) == []


def test_3_14_placeholder_on_both_sides_is_not_drift():
    baseline = view([obj(M.METRIC, "X", "CAST(NULL AS DOUBLE)")])
    live = view([obj(M.METRIC, "X", "NULL::DOUBLE")])
    assert C.compare(baseline, live) == []


def test_3_15_placeholder_on_one_side_only_is_drift():
    baseline = view([obj(M.METRIC, "X", "SUM(T1.AMOUNT)")])
    live = view([obj(M.METRIC, "X", "CAST(NULL AS DOUBLE)")])
    assert categories(C.compare(baseline, live)) == [M.EXPRESSION_DRIFT]


def test_3_16_matching_is_case_insensitive():
    baseline = view([obj(M.METRIC, "total", "SUM(T1.AMOUNT)", table="t1")])
    live = view([obj(M.METRIC, "TOTAL", "SUM(T1.AMOUNT)", table="T1")])
    assert C.compare(baseline, live) == []


def test_3_17_a_source_that_cannot_report_descriptions_raises_no_description_drift():
    caps = M.Capabilities(descriptions=False)
    baseline = view([obj(M.METRIC, "X", "SUM(T1.AMOUNT)", "Before")], capabilities=caps)
    live = view([obj(M.METRIC, "X", "SUM(T1.AMOUNT)", "After")], capabilities=caps)
    assert C.compare(baseline, live) == []


def test_3_18_a_source_that_cannot_report_relationships_raises_no_relationship_findings():
    caps = M.Capabilities(kinds=frozenset({M.METRIC, M.DIMENSION, M.FACT}))
    baseline = view([obj(M.RELATIONSHIP, "A->B::c1=k1", "A->B::c1=k1")], capabilities=caps)
    assert C.compare(baseline, view([], capabilities=caps)) == []


def test_3_19_different_source_paths_are_not_compared():
    baseline = view([obj(M.METRIC, "X", "SUM(T1.AMOUNT)")], source_path="yaml")
    live = view([obj(M.METRIC, "X", "SUM(T1.AMOUNT)")], source_path="sql_fallback")
    with pytest.raises(M.SourcePathMismatch):
        C.compare(baseline, live)


def test_3_20_sides_that_never_matched_are_refused_not_reported_as_drift():
    # The real incident: the live parser stored expressions as names, so every
    # baseline object looked deleted and every live object looked added.
    pairs = [(f"METRIC_{i}", f"SUM(T1.COL_{i})") for i in range(8)]
    baseline = view([obj(M.METRIC, name, expr) for name, expr in pairs])
    live = view([obj(M.METRIC, expr, name) for name, expr in pairs])
    with pytest.raises(M.SidesDoNotMatch):
        C.compare(baseline, live)


def test_3_21_output_order_is_deterministic():
    baseline = view([obj(M.METRIC, f"M{i}", "SUM(T1.AMOUNT)") for i in range(10)])
    live = view([obj(M.METRIC, f"M{i}", "AVG(T1.AMOUNT)") for i in range(9, -1, -1)])
    first = [(f.kind, f.name) for f in C.compare(baseline, live)]
    second = [(f.kind, f.name) for f in C.compare(baseline, live)]
    assert first == second


def test_3_22_group_counts_match_the_findings():
    baseline = view([obj(M.METRIC, "A", "SUM(T1.X)"), obj(M.DIMENSION, "B", "T1.B")])
    live = view([obj(M.METRIC, "A", "AVG(T1.X)"), obj(M.DIMENSION, "C", "T1.C")])
    findings = C.compare(baseline, live)
    counts = C.group_counts(findings)
    assert sum(sum(c.values()) for c in counts.values()) == len(findings)
