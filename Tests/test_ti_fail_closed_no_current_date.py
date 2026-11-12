"""Time intelligence fails closed when no data-based period flag/anchor exists.

DAX anchors SAMEPERIODLASTYEAR/TOTALYTD/... to the last date with data. A
CURRENT_DATE() window silently returns different numbers whenever the data
does not extend to today, so such a metric must become a placeholder with a
reason -- never a CURRENT_DATE() expression.
"""

from __future__ import annotations

from semabridge.connectors.semantic_ddl_sanitizer import (
    PERIOD_ANCHOR_UNAVAILABLE_REASON,
    SemanticDDLSanitizer,
)
from semabridge.converter.dax_ast_parser import TI_CURRENT_DATE_ANCHOR_REASON
from semabridge.converter.dax_translator import DAXTranslationResult, DAXTranslator
from semabridge.utils.identifiers import IdentifierSanitizer


_DDL = """CREATE OR REPLACE SEMANTIC VIEW "DB"."SCH"."HR_SEMANTIC"
TABLES (
  EMPLOYEE AS "DB"."SCH"."EMPLOYEE"
)
METRICS (
  EMPLOYEE."NEW_HIRES" AS SUM(EMPLOYEE."ISNEWHIRE"::FLOAT),
  EMPLOYEE."NEW_HIRES_SPLY" AS SUM(CASE WHEN YEAR(EMPLOYEE."HIREDATE") = YEAR(DATEADD(YEAR, -1, EMPLOYEE."MAX_DATE")) THEN EMPLOYEE."ISNEWHIRE" END),
  EMPLOYEE."ROLLING" AS SUM(CASE WHEN EMPLOYEE."MONTHINDEX" > MAX_MONTHINDEX - 12 THEN EMPLOYEE."ISNEWHIRE" END)
);"""


def _sanitizer() -> SemanticDDLSanitizer:
    return SemanticDDLSanitizer(IdentifierSanitizer())


def _metric_line(ddl: str, name: str) -> str:
    return next(line for line in ddl.splitlines() if f'."{name}" AS' in line)


def test_missing_max_date_anchor_nulls_metric_instead_of_current_date():
    fixed, changed, nulled = _sanitizer().remediate_invalid_identifier(_DDL, "MAX_DATE")

    assert changed
    assert nulled == ["NEW_HIRES_SPLY"]
    assert "CURRENT_DATE" not in fixed.upper()
    line = _metric_line(fixed, "NEW_HIRES_SPLY")
    assert "CAST(NULL AS DOUBLE)" in line
    assert "data-based period anchor" in line
    # Metrics that don't use the anchor are untouched.
    assert _metric_line(fixed, "NEW_HIRES") == _metric_line(_DDL, "NEW_HIRES")


def test_qualified_max_date_rejection_is_treated_the_same():
    fixed, changed, nulled = _sanitizer().remediate_invalid_identifier(_DDL, "EMPLOYEE.MAX_DATE")

    assert changed and nulled == ["NEW_HIRES_SPLY"]
    assert "CURRENT_DATE" not in fixed.upper()
    assert "data-based period anchor" in _metric_line(fixed, "NEW_HIRES_SPLY")


def test_missing_monthindex_anchor_nulls_metric_instead_of_current_date():
    fixed, changed, nulled = _sanitizer().remediate_invalid_identifier(_DDL, "MAX_MONTHINDEX")

    assert changed
    assert nulled == ["ROLLING"]
    assert "CURRENT_DATE" not in fixed.upper()
    assert "CAST(NULL AS DOUBLE)" in _metric_line(fixed, "ROLLING")
    assert PERIOD_ANCHOR_UNAVAILABLE_REASON.split(";")[0].replace("'", "''") in fixed


def test_translator_turns_current_date_window_into_failure_with_reason(monkeypatch):
    """Whatever tier produced it, a CURRENT_DATE() window for DAX that has no
    TODAY()/NOW() is a failure (placeholder) with the reason, not SQL."""
    dax = "CALCULATE(SUM(Employee[IsNewHire]), SAMEPERIODLASTYEAR('Date'[Date]))"
    wall_clock_sql = (
        'SUM(CASE WHEN YEAR(EMPLOYEE."HIREDATE") = YEAR(CURRENT_DATE()) - 1 '
        'THEN EMPLOYEE."ISNEWHIRE" END)'
    )
    monkeypatch.setattr(
        DAXTranslator,
        "_try_tiered_translation",
        lambda self, clean_dax, *a, **k: DAXTranslationResult(wall_clock_sql, 3, clean_dax),
    )

    res = DAXTranslator().translate(dax, table_alias="EMPLOYEE", dataset_name="Employee")

    assert not res.is_success
    assert res.sql is None
    assert res.failure_reason == TI_CURRENT_DATE_ANCHOR_REASON


def test_today_in_dax_may_still_use_current_date(monkeypatch):
    """TODAY()/NOW() really are wall-clock in DAX; those stay translatable."""
    dax = "CALCULATE(SUM(Employee[IsNewHire]), Employee[HireDate] <= TODAY())"
    sql = 'SUM(CASE WHEN EMPLOYEE."HIREDATE" <= CURRENT_DATE() THEN EMPLOYEE."ISNEWHIRE" END)'
    monkeypatch.setattr(
        DAXTranslator,
        "_try_tiered_translation",
        lambda self, clean_dax, *a, **k: DAXTranslationResult(sql, 3, clean_dax),
    )

    res = DAXTranslator().translate(dax, table_alias="EMPLOYEE", dataset_name="Employee")

    assert res.is_success
    assert "CURRENT_DATE()" in res.sql
