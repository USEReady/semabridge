from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re


@dataclass(frozen=True)
class MetricClassification:
    category: str
    deploy: bool
    reasons: list[str] = field(default_factory=list)


class MetricClassifier:
    def classify(self, metric: Any) -> MetricClassification:
        name = str(getattr(metric, "unique_name", "") or getattr(metric, "label", "") or "").strip()
        expr = str(getattr(metric, "expression", "") or getattr(metric, "sql_expression", "") or "").strip()

        reasons: list[str] = []
        if not name:
            reasons.append("missing metric name")
        if not expr:
            reasons.append("missing expression")

        if name.startswith(("@", "#")):
            return MetricClassification("presentation", False, ["presentation suffix"])
        if re.fullmatch(r"['\"]([^'\"]*)['\"]", expr):
            return MetricClassification("label", False, ["constant string measure"])
        if re.search(r"\b(CONCATENATE|FORMAT|UNICHAR|REPT)\s*\(", expr, re.IGNORECASE):
            return MetricClassification("formatting", False, ["formatting heavy"])
        if re.search(r"\b(INDICATOR|SPACER|LABEL|TITLE|HEADER)\b", name, re.IGNORECASE):
            return MetricClassification("presentation", False, ["presentation naming"])
        if re.search(r"\b(KPI|HELPER|HIDDEN)\b", name, re.IGNORECASE):
            return MetricClassification("kpi_helper", False, ["kpi/helper naming"])
        if re.search(r"\b(CONCATENATE|FORMAT|IF\s*\([^,]+,[^,]+,[^\)]+\))", expr, re.IGNORECASE):
            reasons.append("presentation-like expression")

        if reasons:
            return MetricClassification("analytical", True, reasons)
        return MetricClassification("analytical", True, [])
