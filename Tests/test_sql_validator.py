from __future__ import annotations

from semabridge.compiler.sql_validator import SQLValidator
from semabridge.connectors.sql_validator import validate_sql_expression as validate_deployment_sql


def test_validator_rejects_null_and_raw_dax() -> None:
    validator = SQLValidator()

    invalid_null = validator.validate('SUM("FACT"."VALUE") AS NULL')
    invalid_dax = validator.validate('CALCULATE(SUM("FACT"."VALUE"))')

    assert invalid_null.is_valid is False
    assert any("AS NULL" in error.upper() for error in invalid_null.errors)
    assert invalid_dax.is_valid is False
    assert any("RAW DAX" in error.upper() or "DAX" in error.upper() for error in invalid_dax.errors)


def test_validator_accepts_valid_sql() -> None:
    validator = SQLValidator()
    valid = validator.validate('CASE WHEN "FACT"."VALUE" > 0 THEN "FACT"."VALUE" ELSE 0 END')

    assert valid.is_valid is True
    assert not valid.errors


def test_validator_rejects_unresolved_metric_identifier() -> None:
    validator = SQLValidator()

    invalid = validator.validate('SUM(SALESFACT."TOTAL_UNITS_SPLY")')

    assert invalid.is_valid is False
    assert any("UNRESOLVED METRIC" in error.upper() for error in invalid.errors)


def test_deployment_validator_rejects_malformed_window_and_raw_dax() -> None:
    invalid_window = validate_deployment_sql('SUM(CASE WHEN "FACT"."VALUE" > 0 THEN "FACT"."VALUE" ELSE 0 END) OVER ()')
    invalid_raw = validate_deployment_sql('IF(COUNTBLANK(PRODUCT.PRODUCT) > 0, 1, 0)')

    assert invalid_window.is_valid is False
    assert any("WINDOW" in error.upper() or "OVER" in error.upper() for error in invalid_window.errors)
    assert invalid_raw.is_valid is False
    assert any("RAW DAX" in error.upper() or "DAX" in error.upper() for error in invalid_raw.errors)
