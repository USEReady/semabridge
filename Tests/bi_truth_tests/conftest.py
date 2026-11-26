"""Shared helpers. Every name is built here; no real project or measure appears.

Nothing in this pack creates a database, a session or a warehouse connection. If a
test ever needs one, the module under test is reaching somewhere it should not.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bi_truth_tests import adapter  # noqa: E402

C = adapter.comparison
W = adapter.windows
G = adapter.gate


def hours_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()


def result(measure, verdict="PASS", window="", expected=None, actual=None,
           variance_pct=None, tolerance_pct=None, reason=""):
    """One measure's outcome inside a run record."""
    return {
        "measure": measure,
        "verdict": verdict,
        "window": window,
        "expected": expected,
        "actual": actual,
        "variance_pct": variance_pct,
        "tolerance_pct": tolerance_pct,
        "reason": reason,
    }


def run(results=None, age_hours=1):
    """A run record as the gate reads it."""
    return {"run_at": hours_ago(age_hours), "results": list(results or [])}


@pytest.fixture
def window_json():
    """Three windows on one case, each with its own expected value."""
    return [
        {"window_key": "w1", "window_label": "First period",
         "filter_clause": "PERIOD = 1", "expected_value": 100},
        {"window_key": "w2", "window_label": "Second period",
         "filter_clause": "PERIOD = 2", "expected_value": 200},
        {"window_key": "w3", "window_label": "Rolling",
         "filter_clause": "PERIOD >= 1", "expected_value": 300},
    ]


@pytest.fixture
def plain_sql():
    return "SELECT SUM(AMOUNT) AS TOTAL FROM T1"


@pytest.fixture
def semantic_view_sql():
    return (
        'SELECT * FROM SEMANTIC_VIEW("DB"."SCH"."V" '
        'METRICS MEASURE_ONE DIMENSIONS DIM_ONE)'
    )
