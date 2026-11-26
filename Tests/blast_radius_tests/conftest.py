"""Shared helpers. Every name is built here; no real project or metric appears."""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from blast_radius_tests import adapter  # noqa: E402

S = adapter.simulator
R = adapter.risk

ADDITION = "ADDITION"
DELETION = "DELETION"
EXPRESSION_DRIFT = "EXPRESSION_DRIFT"
DESCRIPTION_DRIFT = "DESCRIPTION_DRIFT"
CATEGORIES = [ADDITION, DELETION, EXPRESSION_DRIFT, DESCRIPTION_DRIFT]


def metric(name, expression="", description="", derives_from=None, reads_descriptions=False):
    return S.Metric(name=name, expression=expression, description=description,
                    derives_from=list(derives_from or []),
                    reads_descriptions=reads_descriptions)


def sim(metrics=None, **kwargs):
    return S.BlastRadiusSimulator(metrics=list(metrics or []), **kwargs)


class FakeAdapter:
    """A read-only stand-in for a platform adapter. Records every call."""

    def __init__(self, objects=None, raise_on_fetch=None):
        self.objects = list(objects or [])
        self.raise_on_fetch = raise_on_fetch
        self.calls = []

    def fetch_definition_text(self, view_name, connection_config=None):
        self.calls.append(("fetch", view_name))
        if self.raise_on_fetch:
            raise RuntimeError(self.raise_on_fetch)
        return "definition-text"

    def parse_definition(self, raw_text, view_name, source="live", descriptions=None):
        self.calls.append(("parse", view_name))

        class Def:
            objects = self.objects

        return Def()

    # Anything that would write must not exist on the read path; if the product
    # calls one of these the test fails loudly rather than silently passing.
    def execute(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError("blast radius attempted a write")


@pytest.fixture
def three_level_model():
    """base -> middle -> top, so a transitive walk has something to walk."""
    return [
        metric("BASE_TOTAL", "SUM(T1.AMOUNT)"),
        metric("MIDDLE_RATE", "BASE_TOTAL / NULLIF(T1.COUNT, 0)", derives_from=["BASE_TOTAL"]),
        metric("TOP_INDEX", "MIDDLE_RATE * 100", derives_from=["MIDDLE_RATE"]),
        metric("UNRELATED", "AVG(T2.OTHER)"),
    ]


@pytest.fixture(params=CATEGORIES)
def category(request):
    """Every entry point must answer all four categories."""
    return request.param
