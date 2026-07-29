"""Tests for semabridge.connectors.ddl_helpers.

format_scalar_sql_literal is used to splice a pre-fetched anchor value
(e.g. a fiscal-period or MAX_DATE anchor) into DDL as a literal instead of
embedding the subquery that produced it. This is a regression test for a
bug where a DATE/TIMESTAMP value fell through to the generic
quoted-string branch, producing a VARCHAR literal that functions like
DATE_TRUNC/DATEADD reject with a type error instead of a properly-typed
date/timestamp literal.
"""
import datetime

from semabridge.connectors.ddl_helpers import format_scalar_sql_literal


def test_date_value_formats_as_typed_date_literal():
    result = format_scalar_sql_literal(datetime.date(2026, 3, 15))
    assert result == "DATE '2026-03-15'"


def test_datetime_value_formats_as_typed_timestamp_literal():
    result = format_scalar_sql_literal(datetime.datetime(2026, 3, 15, 10, 30, 0))
    assert result == "TIMESTAMP '2026-03-15 10:30:00'"


def test_integer_value_formats_as_bare_number():
    assert format_scalar_sql_literal(202603) == "202603"


def test_none_value_formats_as_null():
    assert format_scalar_sql_literal(None) == "NULL"


def test_string_value_formats_as_quoted_and_escaped():
    assert format_scalar_sql_literal("O'Brien") == "'O''Brien'"


def test_bool_value_formats_as_sql_boolean():
    assert format_scalar_sql_literal(True) == "TRUE"
    assert format_scalar_sql_literal(False) == "FALSE"
