"""Reference risk classifier.

A level belongs to each impacted node, not to the report. The report's level is the
highest among its nodes, so an empty report is INFO rather than silently SAFE.
"""
from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    BREAKING = "BREAKING"
    ADVISORY = "ADVISORY"
    INFO = "INFO"


BREAKING = RiskLevel.BREAKING
ADVISORY = RiskLevel.ADVISORY
INFO = RiskLevel.INFO

_ORDER = {RiskLevel.INFO: 0, RiskLevel.ADVISORY: 1, RiskLevel.BREAKING: 2}

# Item kinds the classifier distinguishes.
DEPENDANT = "dependant"          # a metric or view that names the target
CERTIFIED = "certified_query"    # a certified query or BI-truth case
CONSUMER = "consumer_asset"      # a dashboard, space, share or published source
CONTEXT = "context"              # listed for context, not affected


def classify_node_risk(change_type: str, item_type: str,
                       reads_descriptions: bool = False) -> RiskLevel:
    """The level for one impacted node.

    Risk comes from the change and the item together. Dropping or renaming breaks
    anything that names the target. Changing a type breaks a dependant but only
    shifts a query's result. Changing an expression shifts results without breaking
    a reference. Changing a description changes meaning only.
    """
    change = str(change_type or "").strip().upper()
    item = str(item_type or "").strip().lower()

    if item == CONTEXT:
        return RiskLevel.INFO

    if change in ("DROP", "RENAME"):
        return RiskLevel.BREAKING

    if change == "MODIFY_TYPE":
        return RiskLevel.BREAKING if item == DEPENDANT else RiskLevel.ADVISORY

    if change == "MODIFY_EXPRESSION":
        return RiskLevel.ADVISORY

    if change == "MODIFY_DESCRIPTION":
        if item == DEPENDANT and not reads_descriptions:
            return RiskLevel.INFO
        return RiskLevel.ADVISORY

    if change in ("ADD", "ADDITION", "CREATE"):
        return RiskLevel.INFO

    return RiskLevel.ADVISORY


def report_risk(levels) -> RiskLevel:
    """The highest level among a report's nodes; INFO when there are none."""
    best = RiskLevel.INFO
    for level in levels:
        lvl = level if isinstance(level, RiskLevel) else RiskLevel(str(level).upper())
        if _ORDER[lvl] > _ORDER[best]:
            best = lvl
    return best
