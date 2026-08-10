"""Regression tests for SnowflakeEmitter._log_dropped_metrics_summary.

Context: the final run-summary log for auto-remediation-dropped metrics runs
*after* DDL execution has already succeeded, inside
_execute_deployment_pipeline's outer try block. If this purely-cosmetic
logging call ever raises, the outer `except Exception` handler reports an
otherwise-successful (real-money) deploy as FAILED. These tests exercise the
0/1/many count cases plus the defensive coercions that make that impossible.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter


def _fake_self(dropped_metrics):
    return SimpleNamespace(_dropped_metrics=dropped_metrics)


def test_zero_dropped_metrics_logs_nothing(caplog):
    fake_self = _fake_self([])
    with caplog.at_level(logging.WARNING):
        SnowflakeEmitter._log_dropped_metrics_summary(fake_self, "SML", "MyModel")
    assert caplog.records == []


def test_one_dropped_metric_uses_singular_word(caplog):
    fake_self = _fake_self([{"metric": "TOTAL_UNITS_YTD", "reason": "boom"}])
    with caplog.at_level(logging.WARNING):
        SnowflakeEmitter._log_dropped_metrics_summary(fake_self, "SML", "MyModel")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "1 metric DROPPED" in text
    assert "metrics DROPPED" not in text
    assert "TOTAL_UNITS_YTD" in text
    assert "boom" in text


def test_many_dropped_metrics_uses_plural_word(caplog):
    dropped = [
        {"metric": "M1", "reason": "r1"},
        {"metric": "M2", "reason": "r2"},
        {"metric": "M3", "reason": "r3"},
    ]
    fake_self = _fake_self(dropped)
    with caplog.at_level(logging.WARNING):
        SnowflakeEmitter._log_dropped_metrics_summary(fake_self, "SML", "MyModel")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "3 metrics DROPPED" in text
    for m in ("M1", "M2", "M3"):
        assert m in text


def test_malformed_entry_does_not_crash_and_still_logs_the_rest(caplog):
    """A non-dict entry (or one missing expected keys) must degrade to a
    fallback line instead of raising and aborting the whole summary."""
    dropped = [
        "not-a-dict",
        {"metric": "GOOD_METRIC", "reason": "still shown"},
        {},  # missing both keys
    ]
    fake_self = _fake_self(dropped)
    with caplog.at_level(logging.WARNING):
        SnowflakeEmitter._log_dropped_metrics_summary(fake_self, "SML", "MyModel")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "3 metrics DROPPED" in text
    assert "GOOD_METRIC" in text
    assert "could not format one dropped-metric entry" in text
    assert "<unknown metric>" in text
    assert "<no reason recorded>" in text


class _LenRaises:
    """Object whose len() raises, to prove the count coercion can't crash
    the summary even if _dropped_metrics is ever replaced by something
    that isn't a plain list."""

    def __len__(self):
        raise TypeError("boom")

    def __bool__(self):
        return True

    def __iter__(self):
        return iter([{"metric": "X", "reason": "Y"}])


def test_uncoercible_count_is_swallowed_not_raised(caplog):
    fake_self = _fake_self(_LenRaises())
    with caplog.at_level(logging.WARNING):
        # Must not raise.
        SnowflakeEmitter._log_dropped_metrics_summary(fake_self, "SML", "MyModel")
    text = "\n".join(r.getMessage() for r in caplog.records)
    # dropped_count falls back to 0 -> plural word, but the call still
    # completes and reports the failure via logger.error rather than raising.
    assert "could not be logged" in text or "0 metrics DROPPED" in text


def test_summary_never_raises_even_on_total_failure(caplog, monkeypatch):
    """Force every inner branch to fail (bad logger) and confirm the method
    still returns normally instead of propagating."""
    fake_self = _fake_self([{"metric": "M", "reason": "R"}])

    import semabridge.connectors.snowflake_emitter as emitter_module

    class ExplodingLogger:
        def warning(self, *args, **kwargs):
            raise RuntimeError("logging backend down")

        def error(self, *args, **kwargs):
            pass  # the final fallback must still not raise

    monkeypatch.setattr(emitter_module, "logger", ExplodingLogger())
    # Must not raise despite every logger.warning call failing.
    SnowflakeEmitter._log_dropped_metrics_summary(fake_self, "SML", "MyModel")
