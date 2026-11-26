"""Where these tests get their implementation from.

By default they run against the reference in ``_ref``, which proves the suite is
self consistent. Point them at the product with:

    SB_BT_IMPL=product pytest bi_truth_tests

Module paths can be overridden when they differ:

    SB_BT_COMPARISON_MODULE   default semabridge.ai.bi_truth_comparison
    SB_BT_WINDOWS_MODULE      default semabridge.ai.bi_truth_windows
    SB_BT_GATE_MODULE         default semabridge.ai.bi_truth_gate
    SB_BT_API_BASE            e.g. http://localhost:8002, enables the API tests

A name the contract requires but the module does not provide fails with that name
rather than skipping, because a missing name is a gap in the contract.

Nothing here touches a database or a warehouse. If importing a product module
pulls in the ORM, that import chain is itself the finding.

SEMANTIC VIEW WINDOW FILTERING SPIKE FINDINGS:
Live warehouse execution on Snowflake confirms that Snowflake's SEMANTIC_VIEW table
function does NOT accept trailing WHERE filters on non-projected dimension columns
(fails with: 000904 (42000): invalid identifier).
However, SEMANTIC_VIEW natively accepts an internal WHERE clause inside the function
parentheses (e.g. SEMANTIC_VIEW(... METRICS ... DIMENSIONS ... WHERE COL_DATE.YEAR = 2014)).
This narrows both scalar metrics and group-by metrics across time slices without requiring
the slice dimension to be in the outer projection.
Current status: Step 1 is built with parser and refusal rules, but build_windowed_sql
refuses SEMANTIC_VIEW queries today (returning None) until internal WHERE clause injection
is implemented in the SQL generator.
"""
from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

IMPL = os.environ.get("SB_BT_IMPL", "reference").strip().lower()
is_product = IMPL == "product"

API_BASE = os.environ.get("SB_BT_API_BASE", "").rstrip("/")

_DEFAULTS = {
    "comparison": "semabridge.ai.bi_truth_comparison",
    "windows": "semabridge.ai.bi_truth_windows",
    "gate": "semabridge.ai.bi_truth_gate",
}


def _load(part: str):
    if not is_product:
        return importlib.import_module(f"bi_truth_tests._ref.{part}")
    name = os.environ.get(f"SB_BT_{part.upper()}_MODULE", _DEFAULTS[part])
    return importlib.import_module(name)


comparison = _load("comparison")
windows = _load("windows")
gate = _load("gate")


def names(module, *required) -> SimpleNamespace:
    missing = [n for n in required if not hasattr(module, n)]
    if missing:
        raise AssertionError(
            f"{module.__name__} does not provide {missing}; README.md lists what each "
            f"module must expose"
        )
    return SimpleNamespace(**{n: getattr(module, n) for n in required})
