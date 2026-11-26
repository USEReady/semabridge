"""1. Risk levels.

A level belongs to each node. The report's level is the highest among them, so a
report never reads safer than its worst node.
"""
from __future__ import annotations

import pytest

from blast_radius_tests import adapter

R = adapter.names(adapter.risk, "classify_node_risk", "report_risk", "RiskLevel",
                  "DEPENDANT", "CERTIFIED", "CONSUMER", "CONTEXT")
L = R.RiskLevel


@pytest.mark.parametrize("change", ["DROP", "RENAME"])
@pytest.mark.parametrize("item", ["DEPENDANT", "CERTIFIED", "CONSUMER"])
def test_1_1_dropping_or_renaming_breaks_anything_that_names_it(change, item):
    assert R.classify_node_risk(change, getattr(R, item)) == L.BREAKING


def test_1_2_changing_a_type_breaks_a_dependant_but_advises_a_query():
    assert R.classify_node_risk("MODIFY_TYPE", R.DEPENDANT) == L.BREAKING
    assert R.classify_node_risk("MODIFY_TYPE", R.CERTIFIED) == L.ADVISORY


@pytest.mark.parametrize("item", ["DEPENDANT", "CERTIFIED", "CONSUMER"])
def test_1_3_changing_an_expression_is_advisory(item):
    assert R.classify_node_risk("MODIFY_EXPRESSION", getattr(R, item)) == L.ADVISORY


def test_1_4_description_change_is_info_for_something_that_ignores_descriptions():
    assert R.classify_node_risk("MODIFY_DESCRIPTION", R.DEPENDANT,
                                reads_descriptions=False) == L.INFO


def test_1_5_description_change_is_advisory_where_meaning_is_read():
    assert R.classify_node_risk("MODIFY_DESCRIPTION", R.DEPENDANT,
                                reads_descriptions=True) == L.ADVISORY
    assert R.classify_node_risk("MODIFY_DESCRIPTION", R.CERTIFIED) == L.ADVISORY


def test_1_6_context_nodes_are_always_info():
    assert R.classify_node_risk("DROP", R.CONTEXT) == L.INFO


def test_1_7_report_level_is_the_highest_node_level():
    assert R.report_risk([L.INFO, L.ADVISORY, L.BREAKING]) == L.BREAKING
    assert R.report_risk([L.INFO, L.ADVISORY]) == L.ADVISORY
    assert R.report_risk([L.INFO]) == L.INFO


def test_1_8_a_report_with_no_nodes_is_info_not_breaking():
    assert R.report_risk([]) == L.INFO


def test_1_9_levels_are_the_three_the_criterion_names():
    assert {l.value for l in L} == {"BREAKING", "ADVISORY", "INFO"}
