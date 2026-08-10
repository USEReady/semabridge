"""Thin wrapper over the existing, unchanged Tier 1-4 deterministic translators.

Calls converter.dax_translator.DAXTranslator's existing tier methods and
converter.dax_ast_parser.try_ast_translate exactly as
converter/dax_translator.py:DAXTranslator.translate() does today — same
functions, same order, same inputs, same outputs. No logic changes.

Two things are deliberately NOT reproduced here, per the approved
consolidation design, because they are pure waste rather than logic:

- The inert DeterministicTranslator detour (dax_translator.py:174-189):
  it always fails against a real (non-5-column-demo) schema and is
  swallowed by its own try/except, contributing nothing.
- The verbatim double-pass: DAXTranslator.translate() runs
  _try_tiered_translation() first, and if that returns None, re-runs the
  identical Tier 1-4 sequence inline a second time before falling through
  to the general AST fallback and Tier 5. Since none of these checks have
  side effects, the second pass can only ever reproduce the first pass's
  result — this wrapper runs the sequence once.

Everything else — the exact tier functions called, the exact gating
conditions (STRICT_BLOCKED_FUNCTIONS, UNSUPPORTED_PATTERNS,
_has_unresolved_bracket_reference), and the exact dispatch order — is
unchanged from converter/dax_translator.py.
"""
from __future__ import annotations

import re
from typing import Optional

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.dax_translation.types import TranslationRequest, TranslationResult

# Single stateless instance — DAXTranslator has no __init__/instance state,
# it's pure logic on class-level regex patterns, so one shared instance is
# safe to reuse across every call.
_translator = DAXTranslator()


def translate_tiers_1_4(request: TranslationRequest) -> Optional[TranslationResult]:
    """Run the existing Tier 1-4 deterministic translators once, in order.

    Returns a TranslationResult on success, or None if every tier declined
    (signal to the caller to fall through to Tier 5).
    """
    clean_dax = (request.dax or "").strip()
    if not clean_dax:
        return None

    table_alias = request.table_alias
    dataset_name = request.dataset_name
    metrics_context = request.metrics_context or None  # DAXTranslator checks truthiness

    # Tier 1: Direct aggregations — SUM/AVERAGE/MIN/MAX/COUNT/DISTINCTCOUNT(col)
    tier1_sql = _translator._try_tier1(clean_dax, table_alias)
    if tier1_sql:
        return TranslationResult(sql=tier1_sql, tier=1, original_dax=clean_dax)

    # Tier 2a: Strict single-predicate CALCULATE — skipped for time-intelligence
    # functions, which the AST renderer (Tier 3) handles correctly; this is
    # the fixed "gate blocks a correct renderer" bug (Bug B).
    strict_blocked = any(
        re.search(rf"\b{pattern}\b", clean_dax, re.IGNORECASE)
        for pattern in _translator.STRICT_BLOCKED_FUNCTIONS
    )
    if not strict_blocked:
        strict_sql = _translator._try_strict_translation(clean_dax, table_alias, metrics_context)
        if strict_sql:
            return TranslationResult(sql=strict_sql, tier=2, original_dax=clean_dax)

    # Tier 2b: Measure-dependency resolution, then binary arithmetic/branching
    # between measures — both require metrics_context to resolve references.
    if metrics_context:
        dependency_sql = _translator._try_dependency_translation(
            clean_dax, table_alias, dataset_name, metrics_context,
        )
        if dependency_sql:
            return TranslationResult(sql=dependency_sql, tier=2, original_dax=clean_dax)

        branching_sql = _translator._try_branching(clean_dax, metrics_context)
        if branching_sql:
            return TranslationResult(sql=branching_sql, tier=2, original_dax=clean_dax)

    resolved_measures = _translator._build_resolved_measures_map(table_alias, metrics_context)
    all_measure_names = _translator._all_measure_names(metrics_context)

    # Tier 3: Time intelligence (TOTALYTD/MTD/QTD, SAMEPERIODLASTYEAR, etc.)
    # via the AST renderer's CASE-WHEN-bounded translation.
    is_time_intel = any(
        func.upper() in clean_dax.upper() for func in _translator.TIME_INTEL_FUNCTIONS
    )
    if is_time_intel:
        from semabridge.converter.dax_ast_parser import try_ast_translate

        ast_sql = try_ast_translate(
            clean_dax,
            table_alias=table_alias,
            date_alias=_translator._get_date_alias(),
            measure_sql_map=resolved_measures,
            known_measure_names=all_measure_names,
            anchor_flag_map=request.anchor_flag_map,
            primary_table_name=dataset_name,
        )
        if ast_sql:
            return TranslationResult(sql=ast_sql, tier=3, original_dax=clean_dax)

    # Tier 4a: Complex CALCULATE/FILTER/ALL/ALLEXCEPT/iterators via the AST renderer.
    is_complex = any(
        re.search(pattern, clean_dax, re.IGNORECASE) for pattern in _translator.UNSUPPORTED_PATTERNS
    )
    if is_complex:
        from semabridge.converter.dax_ast_parser import try_ast_translate

        ast_sql = try_ast_translate(
            clean_dax,
            table_alias=table_alias,
            date_alias=_translator._get_date_alias(),
            measure_sql_map=resolved_measures,
            known_measure_names=all_measure_names,
            anchor_flag_map=request.anchor_flag_map,
            primary_table_name=dataset_name,
        )
        if ast_sql:
            return TranslationResult(sql=ast_sql, tier=4, original_dax=clean_dax)

    # Tier 4b: General AST fallback — CALCULATE-wrapping-a-measure inside
    # IF/arithmetic, binary arithmetic between measure refs, nested IF/SWITCH.
    # Only attempted if every bracket reference in the expression already
    # resolves to a known measure — a measure reference the renderer can't
    # distinguish from a column must never be silently treated as one.
    if not _translator._has_unresolved_bracket_reference(clean_dax, resolved_measures):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        general_ast_sql = try_ast_translate(
            clean_dax,
            table_alias=table_alias,
            date_alias=_translator._get_date_alias(),
            measure_sql_map=resolved_measures,
            known_measure_names=all_measure_names,
            anchor_flag_map=request.anchor_flag_map,
            primary_table_name=dataset_name,
        )
        if general_ast_sql:
            return TranslationResult(sql=general_ast_sql, tier=4, original_dax=clean_dax)

    return None
