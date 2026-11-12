"""Measure-translation fidelity fixes (E1, E2, ...): generic, no model names."""
from __future__ import annotations

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder


def _b() -> MetricsClauseBuilder:
    return MetricsClauseBuilder.__new__(MetricsClauseBuilder)


# --- E1: structurally invalid SQL never reaches the DDL ----------------------------
def test_predeploy_rejects_else_outside_case():
    cols = {"T": {"A", "B"}}
    reason = _b()._run_predeploy_checks(
        "M", "T", "COALESCE(SUM(T.A) - SUM(T.B) ELSE 0, 0)", None, cols, {"T": "T"}, None, {"M"}
    )
    assert reason and "not valid SQL" in reason


def test_predeploy_accepts_valid_case():
    cols = {"T": {"A", "B"}}
    assert _b()._run_predeploy_checks(
        "M", "T", "SUM(CASE WHEN T.B > 0 THEN T.A ELSE 0 END)", None, cols, {"T": "T"}, None, {"M"}
    ) is None


# --- E2: whole-table ALL helper carries the FILTER predicate ------------------------
def test_whole_table_filter_helper_is_not_flagged_as_lost_filter():
    dax = "CALCULATE(COUNTA([Kind]), FILTER(ALL(Thing), [Kind]=\"Special\"))"
    cols = {"THING": {"KIND", "_ALL_COUNTA_KIND_SPECIAL"}}
    reason = _b()._run_predeploy_checks(
        "SPECIAL_THINGS", "THING", 'MAX(THING._ALL_COUNTA_KIND_SPECIAL)', dax,
        cols, {"THING": "THING"}, None, {"SPECIAL_THINGS"},
    )
    assert reason is None


def test_bare_aggregate_without_helper_is_still_flagged():
    dax = "CALCULATE(COUNTA([Kind]), FILTER(ALL(Thing), [Kind]=\"Special\"))"
    cols = {"THING": {"KIND"}}
    reason = _b()._run_predeploy_checks(
        "SPECIAL_THINGS", "THING", 'COUNT(THING.KIND)', dax,
        cols, {"THING": "THING"}, None, {"SPECIAL_THINGS"},
    )
    assert reason and "filter condition lost" in reason
