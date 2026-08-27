"""Regression test for a real incident: a metric appearing EARLIER in the
model's metric list, whose own SQL references ANOTHER metric by name that
appears LATER in the list (e.g. a selector-table metric referencing
[Total Units YTD]), had that reference left completely unqualified in the
emitted DDL -- metrics_clause_builder.py's _build_metrics builds
metric_to_alias incrementally, in plain source order, so the referenced
metric simply isn't in the map yet when the referencing metric is
processed. translator.py's _qualify_bare_metric_references then has no
substitution rule for that name and leaves the bare token untouched.

Confirmed against a real deploy: Snowflake rejected the whole DDL
statement with "invalid identifier 'TOTAL_UNITS_YTD'", and the
deploy-time auto-remediation sweep then nulled out that metric AND every
other, perfectly valid metric whose own (correctly-qualified) SQL happened
to contain that same bare token as a substring -- collateral damage to
metrics that were never actually broken.

The fix: pre-seed metric_to_alias with every metric's own dataset alias
before the main per-metric loop runs, so a reference to ANY metric --
regardless of where it sits in the source order -- is already qualifiable
against a real, existing table alias the first time it's encountered.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer


class DummySanitizer:
    def format_physical_column_ref(self, alias: str, col: str, model_name=None):
        return f'{alias}."{col}"'


def _builder():
    return MetricsClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=None,
        sanitizer=DummySanitizer(),
        translator=MetricExpressionTranslator(identifier_sanitizer=IdentifierSanitizer()),
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
    )


def _model(metrics):
    return SimpleNamespace(metrics=metrics, unique_name="model", label="model")


def _metric(**overrides):
    defaults = dict(
        source_column=None,
        aggregation=None,
        sync_enabled=True,
        sync_failure_reason=None,
        advisory_notes=[],
        advisory_categories=[],
        synonyms=[],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_DATASET_ALIASES = {"Chooser": "CHOOSER", "SalesFact": "SALESFACT"}
_DATASET_BY_NAME = {
    "Chooser": SimpleNamespace(is_fact=False),
    "SalesFact": SimpleNamespace(is_fact=True),
}
_DATASET_COL_LOOKUP = {"Chooser": {"CHOICE"}, "SalesFact": {"UNITS"}}
_ALL_PHYSICAL_COL_NAMES = {"CHOICE", "UNITS"}


def test_forward_reference_to_a_later_metric_gets_qualified_not_left_bare():
    """The core fix: 'Switcher' is FIRST in the metric list and its SQL
    contains a bare "TOTAL_UNITS_YTD" reference to 'Total Units YTD',
    which is declared LATER in the list."""
    switcher = _metric(
        unique_name="Switcher",
        dataset="Chooser",
        expression="IF(SUM('Chooser'[Choice])=1, [Total Units YTD])",
        sql_expression='CASE WHEN SUM(CHOOSER."CHOICE") = 1 THEN ("TOTAL_UNITS_YTD") ELSE NULL END',
    )
    total_units_ytd = _metric(
        unique_name="Total Units YTD",
        dataset="SalesFact",
        expression="SUM([Units])",
        sql_expression='SUM(SALESFACT."UNITS")',
    )
    builder = _builder()

    lines = builder.build_for_sml(
        _model([switcher, total_units_ytd]),
        dataset_aliases=_DATASET_ALIASES,
        dataset_by_name=_DATASET_BY_NAME,
        dataset_col_lookup=_DATASET_COL_LOOKUP,
        alias_by_raw={},
        all_physical_col_names=_ALL_PHYSICAL_COL_NAMES,
        emittable_metric_name_set=set(),
    )

    switcher_line = next(line for line in lines if "SWITCHER" in line.upper())
    # Must be qualified with SalesFact's own alias or inlined -- not left as a bare,
    # unqualified "TOTAL_UNITS_YTD" token (which Snowflake would reject
    # with "invalid identifier").
    assert 'SALESFACT."TOTAL_UNITS_YTD"' in switcher_line or 'SALESFACT.UNITS' in switcher_line.upper().replace('"', ''), switcher_line
    assert '("TOTAL_UNITS_YTD")' not in switcher_line, switcher_line


def test_total_units_ytd_itself_is_unaffected_and_not_collateral_damage():
    """The other half of the incident: TOTAL_UNITS_YTD (and anything else
    that legitimately references it) must deploy with its own correct SQL
    intact -- this fix must never touch a metric that wasn't the one with
    the actual bare reference."""
    switcher = _metric(
        unique_name="Switcher",
        dataset="Chooser",
        expression="IF(SUM('Chooser'[Choice])=1, [Total Units YTD])",
        sql_expression='CASE WHEN SUM(CHOOSER."CHOICE") = 1 THEN ("TOTAL_UNITS_YTD") ELSE NULL END',
    )
    total_units_ytd = _metric(
        unique_name="Total Units YTD",
        dataset="SalesFact",
        expression="SUM([Units])",
        sql_expression='SUM(SALESFACT."UNITS")',
    )
    builder = _builder()

    lines = builder.build_for_sml(
        _model([switcher, total_units_ytd]),
        dataset_aliases=_DATASET_ALIASES,
        dataset_by_name=_DATASET_BY_NAME,
        dataset_col_lookup=_DATASET_COL_LOOKUP,
        alias_by_raw={},
        all_physical_col_names=_ALL_PHYSICAL_COL_NAMES,
        emittable_metric_name_set=set(),
    )

    ytd_line = next(line for line in lines if "TOTAL_UNITS_YTD" in line.upper() and "SWITCHER" not in line.upper())
    assert "SALESFACT.UNITS" in ytd_line.upper().replace('"', '')
    assert builder.drop_ledger.to_json() == []


def test_backward_reference_to_an_earlier_metric_still_works_as_before():
    """Negative control: the ALREADY-working case (a metric referencing
    one declared earlier in the list) must be completely unaffected by
    the pre-seed change."""
    total_units_ytd = _metric(
        unique_name="Total Units YTD",
        dataset="SalesFact",
        expression="SUM([Units])",
        sql_expression='SUM(SALESFACT."UNITS")',
    )
    variance = _metric(
        unique_name="Total Units YTD Var",
        dataset="SalesFact",
        expression="[Total Units YTD]-[Total Units YTD SPLY]",
        sql_expression='("TOTAL_UNITS_YTD")-(SUM(SALESFACT."UNITS"))',
    )
    builder = _builder()

    lines = builder.build_for_sml(
        _model([total_units_ytd, variance]),
        dataset_aliases=_DATASET_ALIASES,
        dataset_by_name=_DATASET_BY_NAME,
        dataset_col_lookup=_DATASET_COL_LOOKUP,
        alias_by_raw={},
        all_physical_col_names=_ALL_PHYSICAL_COL_NAMES,
        emittable_metric_name_set=set(),
    )

    var_line = next(line for line in lines if "VAR" in line.upper())
    assert 'SALESFACT."TOTAL_UNITS_YTD"' in var_line or 'SALESFACT.UNITS' in var_line.upper().replace('"', ''), var_line
