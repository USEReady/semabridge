"""Reference comparison engine: one engine for every platform and mode."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .models import (
    ADDITION,
    DELETION,
    DESCRIPTION_DRIFT,
    EXPRESSION_DRIFT,
    Capabilities,
    DriftFinding,
    SidesDoNotMatch,
    SourcePathMismatch,
    ViewDefinition,
)
from .normalize import comparable, normalize_description

logger = logging.getLogger(__name__)


def _shared(baseline: ViewDefinition, live: ViewDefinition) -> Capabilities:
    """What both sides could report. Anything else is out of scope for findings."""
    b, l = baseline.capabilities, live.capabilities
    return Capabilities(
        kinds=frozenset(b.kinds & l.kinds),
        expressions=b.expressions and l.expressions,
        descriptions=b.descriptions and l.descriptions,
    )


def compare(baseline: ViewDefinition, live: ViewDefinition,
            dialect: Optional[str] = None) -> List[DriftFinding]:
    """Findings between the deployed definition and what the warehouse has now."""
    if baseline.source_path != live.source_path:
        raise SourcePathMismatch(
            f"baseline read as {baseline.source_path}, live read as {live.source_path}"
        )
    dialect = dialect or live.platform
    caps = _shared(baseline, live)
    not_deployed = {n.casefold() for n in (baseline.not_deployed or [])}

    b_map: Dict[tuple, object] = baseline.by_key()
    l_map: Dict[tuple, object] = live.by_key()
    findings: List[DriftFinding] = []

    for key, obj in l_map.items():
        if not caps.reports(obj.kind):
            continue
        if key not in b_map:
            findings.append(DriftFinding(ADDITION, obj.kind, obj.table, obj.name,
                                         "", obj.expression or obj.description))

    for key, obj in b_map.items():
        if not caps.reports(obj.kind):
            continue
        if obj.name.casefold() in not_deployed:
            continue  # the deploy could not create it, so the warehouse is right
        if key not in l_map:
            findings.append(DriftFinding(DELETION, obj.kind, obj.table, obj.name,
                                         obj.expression or obj.description, ""))

    for key, b_obj in b_map.items():
        l_obj = l_map.get(key)
        if l_obj is None or not caps.reports(b_obj.kind):
            continue
        if caps.expressions:
            b_expr, l_expr = comparable(b_obj.expression, l_obj.expression, dialect)
            if b_expr.text != l_expr.text:
                findings.append(DriftFinding(EXPRESSION_DRIFT, b_obj.kind, b_obj.table,
                                             b_obj.name, b_obj.expression, l_obj.expression))
        if caps.descriptions:
            b_desc = normalize_description(b_obj.description)
            l_desc = normalize_description(l_obj.description)
            if b_desc != l_desc:
                findings.append(DriftFinding(DESCRIPTION_DRIFT, b_obj.kind, b_obj.table,
                                             b_obj.name, b_obj.description, l_obj.description))

    _guard_sides_match(baseline, live, findings)
    findings.sort(key=lambda f: f.sort_key)
    return findings


MIN_OBJECTS_FOR_GUARD = 5  # below this a full mismatch can be a genuine rewrite


def _guard_sides_match(baseline: ViewDefinition, live: ViewDefinition,
                       findings: List[DriftFinding]) -> None:
    """Refuse a result where nothing matched at all.

    Every baseline object deleted and every live object added, with no expression
    drift, means the two sides were never aligned: a parser fault, not drift.
    """
    if (len(baseline.objects) < MIN_OBJECTS_FOR_GUARD
            or len(live.objects) < MIN_OBJECTS_FOR_GUARD):
        return
    deletions = sum(1 for f in findings if f.category == DELETION)
    additions = sum(1 for f in findings if f.category == ADDITION)
    drifts = sum(1 for f in findings
                 if f.category in (EXPRESSION_DRIFT, DESCRIPTION_DRIFT))
    comparable_baseline = [o for o in baseline.objects
                           if o.name.casefold() not in {n.casefold() for n in baseline.not_deployed}]
    if (drifts == 0
            and deletions == len(comparable_baseline)
            and additions == len(live.objects)
            and deletions > 0 and additions > 0):
        raise SidesDoNotMatch(
            "no object matched between the deployed definition and the warehouse; "
            "the two sides were not aligned"
        )


def group_counts(findings: List[DriftFinding]) -> Dict[str, Dict[str, int]]:
    """Counts by kind then category, for the paged response and the page headers."""
    out: Dict[str, Dict[str, int]] = {}
    for f in findings:
        out.setdefault(f.kind, {}).setdefault(f.category, 0)
        out[f.kind][f.category] += 1
    return out
