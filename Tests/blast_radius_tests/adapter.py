"""Where these tests get their implementation from.

By default they run against the reference implementation in ``_ref``, which proves
the suite is self consistent. Point them at the product with:

    SB_BR_IMPL=product pytest blast_radius_tests

Module paths can be overridden when they differ:

    SB_BR_SIMULATOR_MODULE   default semabridge.governance.blast_radius
    SB_BR_RISK_MODULE        default semabridge.governance.risk_classifier
    SB_BR_RESOLVER_MODULE    default semabridge.governance.model_resolver
    SB_BR_API_BASE           e.g. http://localhost:8002, enables the API tests

A name the contract requires but the module does not provide fails with that name,
rather than skipping: a missing name is a gap in the contract.
"""
from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

IMPL = os.environ.get("SB_BR_IMPL", "reference").strip().lower()
is_product = IMPL == "product"

API_BASE = os.environ.get("SB_BR_API_BASE", "").rstrip("/")

_DEFAULTS = {
    "simulator": "semabridge.governance.blast_radius",
    "risk": "semabridge.governance.risk_classifier",
    "resolver": "semabridge.governance.model_resolver",
}


def _load(part: str):
    if not is_product:
        return importlib.import_module(f"blast_radius_tests._ref.{part}")
    name = os.environ.get(f"SB_BR_{part.upper()}_MODULE", _DEFAULTS[part])
    return importlib.import_module(name)


simulator = _load("simulator")
risk = _load("risk")
resolver = _load("resolver")


def names(module, *required) -> SimpleNamespace:
    missing = [n for n in required if not hasattr(module, n)]
    if missing:
        raise AssertionError(
            f"{module.__name__} does not provide {missing}; README.md lists what each "
            f"module must expose"
        )
    return SimpleNamespace(**{n: getattr(module, n) for n in required})
