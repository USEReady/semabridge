"""
Adapters so the SAME tests run against:
  * the reference implementations in phase_a/ (default), and
  * SemaBridge's real code, once you point these env vars at a wrapper function:

    SEMABRIDGE_NORMALIZER = "package.module:function"
        function(relationships) -> list of kept relationships
        (or (kept, decisions)); wrap ConversionEngine._normalize_relationships here.
    SEMABRIDGE_KEY_PLANNER = "package.module:function"
        same signature as phase_a.relationship_resolver.build_plan

Example (PowerShell):
    $env:SEMABRIDGE_NORMALIZER = "phase_a_adapters:semabridge_normalize"
    uv run python -m pytest phase_a\tests -q
"""
import importlib
import os
import sys
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def _load(env, default):
    target = os.environ.get(env)
    if not target:
        return default, "reference"
    mod, fn = target.split(":")
    return getattr(importlib.import_module(mod), fn), target


@pytest.fixture
def normalize():
    from reference_normalizer import normalize as ref
    fn, name = _load("SEMABRIDGE_NORMALIZER", ref)

    def run(rels):
        out = fn(list(rels))
        return out if isinstance(out, tuple) else (out, None)
    run.impl = name
    return run


@pytest.fixture
def plan_keys():
    from relationship_resolver import build_plan
    fn, name = _load("SEMABRIDGE_KEY_PLANNER", build_plan)
    fn.impl = name
    return fn


def rel(f, fc, t, tc, active=True, card="many-to-one", name=None):
    """Synthetic relationship; names are arbitrary test data, not used by the code under test."""
    fc = fc if isinstance(fc, list) else [fc]
    tc = tc if isinstance(tc, list) else [tc]
    return SimpleNamespace(unique_name=name or f"{f}_{'_'.join(fc)}_{t}_{'_'.join(tc)}",
                           from_dataset=f, from_columns=fc, to_dataset=t, to_columns=tc,
                           is_active=active, cardinality=card)


@pytest.fixture
def R():
    return rel
