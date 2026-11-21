"""SemaBridge edge-case suite: shared helpers.

Drop this folder into the repo as Tests/edge_suite/ and run:
    python -m pytest Tests/edge_suite -q -o addopts="" -o log_cli=false
Every test states the REQUIRED behaviour. A failure is a finding, not a test bug:
do not edit expectations to make the suite green.
"""
import os
import types

import pytest

os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")


def ns(**kw):
    return types.SimpleNamespace(**kw)


@pytest.fixture
def make_ns():
    return ns
