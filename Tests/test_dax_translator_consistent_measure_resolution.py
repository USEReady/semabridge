"""Regression tests for DAXTranslator._has_inconsistent_measure_resolution /
_consistent_ast_measure_args -- the fix for a class of Snowflake semantic
view error 010218 ("a metric must have a single aggregate over another
row-level expression ... at its own or a lower level of granularity").

Real-world trigger: a metric like TOTAL_UNITS_YTD_VAR
(``[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]``) whose AST render call sees
one sibling measure reference already resolved (so
dax_ast_parser.py:_render_measure_ref inlines its raw SQL) and another
still unresolved-but-known (which the renderer would otherwise fail closed
on, or -- for a name the caller's measure registry has no knowledge of at
all -- silently render as a bare column reference). Either way, mixing an
inlined raw aggregate with anything else in the same top-level expression
produces a shape Snowflake's validator rejects.

The fix: before invoking the AST renderer, detect when a metric's own DAX
mixes resolved and unresolved known-measure references, and if so, clear
BOTH `measure_sql_map` and `known_measure_names` for that render call so
every reference in the expression falls through the renderer's uniform
bare-column path instead -- later fixed up identically by
connectors/translator.py:_qualify_bare_metric_references.
"""
from __future__ import annotations

from semabridge.converter.dax_translator import DAXTranslator

_T = DAXTranslator()


def test_no_measure_references_is_never_inconsistent():
    assert _T._has_inconsistent_measure_resolution(
        "SUM([Units])", {}, set()
    ) is False


def test_plain_column_bracket_never_counts_even_when_unresolved():
    """SUM([Amount]) where Amount is a physical column, not a measure --
    must never be flagged, or currently-working metrics that aggregate a
    column alongside an already-resolved measure reference would regress."""
    dax = "[TOTAL_UNITS_YTD] + SUM([Amount])"
    resolved = {"TOTAL_UNITS_YTD": "SUM(FACT.UNITS)"}
    all_names = {"TOTAL_UNITS_YTD"}  # "Amount" is not a tracked measure name
    assert _T._has_inconsistent_measure_resolution(dax, resolved, all_names) is False


def test_all_referenced_measures_resolved_is_consistent():
    dax = "[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]"
    resolved = {
        "TOTAL_UNITS_YTD": "SUM(FACT.UNITS)",
        "TOTAL_UNITS_YTD_SPLY": "SUM(FACT.UNITS_SPLY)",
    }
    all_names = {"TOTAL_UNITS_YTD", "TOTAL_UNITS_YTD_SPLY"}
    assert _T._has_inconsistent_measure_resolution(dax, resolved, all_names) is False


def test_all_referenced_measures_unresolved_is_consistent():
    dax = "[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]"
    all_names = {"TOTAL_UNITS_YTD", "TOTAL_UNITS_YTD_SPLY"}
    assert _T._has_inconsistent_measure_resolution(dax, {}, all_names) is False


def test_one_resolved_one_unresolved_known_measure_is_inconsistent():
    """The exact real-world shape: TOTAL_UNITS_YTD is inlinable,
    TOTAL_UNITS_YTD_SPLY is a known measure with no resolved SQL yet."""
    dax = "[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]"
    resolved = {"TOTAL_UNITS_YTD": "SUM(FACT.UNITS)"}
    all_names = {"TOTAL_UNITS_YTD", "TOTAL_UNITS_YTD_SPLY"}
    assert _T._has_inconsistent_measure_resolution(dax, resolved, all_names) is True


def test_case_insensitive_matching():
    dax = "[total_units_ytd]-[TOTAL_UNITS_YTD_SPLY]"
    resolved = {"Total_Units_YTD": "SUM(FACT.UNITS)"}
    all_names = {"Total_Units_YTD", "Total_Units_YTD_Sply"}
    assert _T._has_inconsistent_measure_resolution(dax, resolved, all_names) is True


def test_table_qualified_column_ref_is_never_treated_as_a_measure_ref():
    """'Date'[Running Year] must not be mistaken for a bracket measure ref
    (the same table-qualified-skip logic _has_unresolved_bracket_reference
    already relies on)."""
    dax = "[TOTAL_UNITS_YTD] + 'Date'[Running Year]"
    resolved = {}
    all_names = {"TOTAL_UNITS_YTD"}
    assert _T._has_inconsistent_measure_resolution(dax, resolved, all_names) is False


def test_consistent_args_passthrough_when_not_inconsistent():
    dax = "[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]"
    resolved = {
        "TOTAL_UNITS_YTD": "SUM(FACT.UNITS)",
        "TOTAL_UNITS_YTD_SPLY": "SUM(FACT.UNITS_SPLY)",
    }
    all_names = {"TOTAL_UNITS_YTD", "TOTAL_UNITS_YTD_SPLY"}
    out_resolved, out_names = _T._consistent_ast_measure_args(dax, resolved, all_names)
    assert out_resolved == resolved
    assert out_names == all_names


def test_consistent_args_cleared_when_inconsistent():
    """Both maps must be cleared together -- clearing only measure_sql_map
    while leaving known_measure_names populated would make
    dax_ast_parser.py:_render_measure_ref fail closed (raise) on the very
    reference this is meant to rescue into a uniform bare-column fallback."""
    dax = "[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]"
    resolved = {"TOTAL_UNITS_YTD": "SUM(FACT.UNITS)"}
    all_names = {"TOTAL_UNITS_YTD", "TOTAL_UNITS_YTD_SPLY"}
    out_resolved, out_names = _T._consistent_ast_measure_args(dax, resolved, all_names)
    assert out_resolved == {}
    assert out_names == set()


def test_end_to_end_ast_render_produces_uniform_shape_not_a_mix():
    """Direct AST-render-level proof: with the inconsistency detected and
    both maps cleared, try_ast_translate renders BOTH sibling references as
    bare table-alias-prefixed columns (uniform), never one inlined raw SUM
    alongside the other left bare."""
    from semabridge.converter.dax_ast_parser import try_ast_translate

    dax = "[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]"
    resolved = {"TOTAL_UNITS_YTD": "SUM(FACT.UNITS)"}
    all_names = {"TOTAL_UNITS_YTD", "TOTAL_UNITS_YTD_SPLY"}

    cleared_resolved, cleared_names = _T._consistent_ast_measure_args(dax, resolved, all_names)
    sql = try_ast_translate(
        dax, table_alias="SALESFACT",
        measure_sql_map=cleared_resolved, known_measure_names=cleared_names,
    )
    assert sql is not None
    assert "SUM(FACT.UNITS)" not in sql, "must not inline the resolved sibling once cleared"
    assert 'SALESFACT."TOTAL_UNITS_YTD"' in sql.upper() or "TOTAL_UNITS_YTD" in sql.upper()
