"""Where the tests get their implementation from.

By default they run against the reference implementation in ``_ref``, which
proves the suite is self consistent. Point them at the product by setting
``SB_DRIFT_IMPL=product`` and, if the module paths differ, the
``SB_DRIFT_*_MODULE`` variables.

    SB_DRIFT_IMPL=product pytest drift_tests

The product modules must expose the names listed in README.md. Anything missing
is reported as a failing test, not skipped, because a missing name is a gap in
the contract.
"""
from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

_IMPL = os.environ.get("SB_DRIFT_IMPL", "reference").strip().lower()

_DEFAULT_MODULES = {
    "models": "semabridge.governance.drift.models",
    "normalize": "semabridge.governance.drift.normalize",
    "comparison": "semabridge.governance.drift.comparison",
    "parsers": "semabridge.governance.drift.adapters",
}


def _load(part: str):
    if _IMPL != "product":
        return importlib.import_module(f"drift_tests._ref.{part}")
    name = os.environ.get(f"SB_DRIFT_{part.upper()}_MODULE", _DEFAULT_MODULES[part])
    return importlib.import_module(name)


models = _load("models")
normalize = _load("normalize")
comparison = _load("comparison")
parsers = _load("parsers")

is_product = _IMPL == "product"


def names(module, *required) -> SimpleNamespace:
    """Fetch the contract names from a module, failing clearly when one is absent."""
    missing = [n for n in required if not hasattr(module, n)]
    if missing:
        raise AssertionError(
            f"{module.__name__} does not provide {missing}; the contract in README.md "
            f"lists what each module must expose"
        )
    return SimpleNamespace(**{n: getattr(module, n) for n in required})
