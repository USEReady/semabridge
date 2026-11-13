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


# --- E16: arithmetic over a NON ADDITIVE BY metric references it by name ---------
def test_ratio_over_non_additive_metric_becomes_derived_metric_reference():
    from types import SimpleNamespace
    from semabridge.utils.identifiers import IdentifierSanitizer
    b = _b()
    b.identifier_sanitizer = IdentifierSanitizer()
    lines = [
        '  EMP."SEPS" AS COUNT(CASE WHEN EMP.TERM IS NOT NULL THEN EMP.ID END)',
        '  EMP."ACTIVES" NON ADDITIVE BY ( D."PERIOD" ASC NULLS LAST ) AS COUNT(CASE WHEN EMP.TERM IS NULL THEN EMP.ID END)',
        '  EMP."TO_PCT" AS COUNT(CASE WHEN EMP.TERM IS NOT NULL THEN EMP.ID END) / NULLIF(COUNT(CASE WHEN EMP.TERM IS NULL THEN EMP.ID END), 0)',
        '  EMP."TO_GAP" AS 1 - (COUNT(CASE WHEN EMP.TERM IS NOT NULL THEN EMP.ID END) / NULLIF(COUNT(CASE WHEN EMP.TERM IS NULL THEN EMP.ID END), 0))',
        '  EMP."SEPS_X2" AS COUNT(CASE WHEN EMP.TERM IS NOT NULL THEN EMP.ID END) * 2',
    ]
    metrics = [
        SimpleNamespace(unique_name="Seps", expression="COUNT([ID])"),
        SimpleNamespace(unique_name="Actives", expression="CALCULATE(COUNT([ID]))"),
        SimpleNamespace(unique_name="TO %", expression="DIVIDE([Seps], [Actives])"),
        SimpleNamespace(unique_name="TO Gap", expression="1 - [TO %]"),
        SimpleNamespace(unique_name="Seps x2", expression="[Seps] * 2"),
    ]
    out = b._reference_non_additive_dependencies_by_name(lines, metrics)
    assert out[2].strip() == '"TO_PCT" AS EMP."SEPS" / NULLIF(EMP."ACTIVES", 0)'
    # transitive: depends on a derived metric -> derived itself, references it by bare name
    assert out[3].strip().startswith('"TO_GAP" AS') and '"TO_PCT"' in out[3] and "COUNT(" not in out[3]
    # no non-additive dependency -> unchanged
    assert out[4] == lines[4]


# --- E17: re-rendered TI metric's COMMENT names its real anchor ----------------------
def test_time_anchor_advisory_follows_rerendered_sql():
    from types import SimpleNamespace
    from semabridge.connectors.anchor_flag_rerender import sync_time_anchor_advisory
    from semabridge.converter.dax_ast_parser import (
        ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE, ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE,
    )
    m = SimpleNamespace(
        advisory_notes=["other note", ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE],
        advisory_categories=["other", ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE],
    )
    sync_time_anchor_advisory(m, "SUM(CASE WHEN F.IS_YTD THEN F.X END)")
    assert ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE not in m.advisory_notes
    assert "other note" in m.advisory_notes
    assert any("latest date in the data" in n and "IS_YTD" in n for n in m.advisory_notes)
    # downgrade back to the CURRENT_DATE() fallback restores the honest note
    sync_time_anchor_advisory(m, "SUM(CASE WHEN F.D >= DATE_TRUNC('YEAR', CURRENT_DATE()) THEN F.X END)")
    assert ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE in m.advisory_notes
    assert not any("latest date in the data" in n for n in m.advisory_notes)


# --- E18: anchored prior-year metrics state their limitation honestly ------------------
def test_prior_year_flag_note_states_it_does_not_shift_per_period():
    from types import SimpleNamespace
    from semabridge.connectors.anchor_flag_rerender import sync_time_anchor_advisory
    m = SimpleNamespace(advisory_notes=[], advisory_categories=[])
    sync_time_anchor_advisory(m, "SUM(CASE WHEN F.IS_SPLY_YEAR THEN F.X END)")
    assert any("not each row's own prior year" in n for n in m.advisory_notes)
    m2 = SimpleNamespace(advisory_notes=[], advisory_categories=[])
    sync_time_anchor_advisory(m2, "SUM(CASE WHEN F.IS_YTD THEN F.X END)")
    assert not any("prior year" in n for n in m2.advisory_notes)
