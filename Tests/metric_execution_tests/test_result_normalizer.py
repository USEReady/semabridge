"""Tests for Decimal-safe normalization and explicit NULL policies."""
from decimal import Decimal
import pytest
from semabridge.ai.metric_execution.result_normalizer import normalize_metric_value


def test_round_four_decimal_places():
    val = 123.456789
    norm = normalize_metric_value(val)
    assert norm == Decimal("123.4568")

    val_dec = Decimal("100.12344")
    assert normalize_metric_value(val_dec) == Decimal("100.1234")


def test_strict_null_policy():
    # STRICT_NULL: None remains None, never converted globally
    assert normalize_metric_value(None, null_policy="STRICT_NULL") is None
    assert normalize_metric_value("", null_policy="STRICT_NULL") is None
    assert normalize_metric_value("null", null_policy="STRICT_NULL") is None


def test_null_equals_zero_policy():
    # NULL_EQUALS_ZERO: Explicitly coalesces None to 0.0000
    assert normalize_metric_value(None, null_policy="NULL_EQUALS_ZERO") == Decimal("0.0000")
    assert normalize_metric_value("null", null_policy="NULL_EQUALS_ZERO") == Decimal("0.0000")
    assert normalize_metric_value("0", null_policy="NULL_EQUALS_ZERO") == Decimal("0.0000")


def test_invalid_values():
    assert normalize_metric_value("not_a_number") is None
    assert normalize_metric_value(True) is None
    assert normalize_metric_value(False) is None
