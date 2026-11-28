"""Metric execution adapters for source semantic models."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod


@dataclass
class SourceResult:
    """Structured result of executing a metric on a source platform."""
    success: bool
    platform: str
    query_text: str = ""
    raw_value: Any = None
    normalized_value: Optional[Decimal] = None
    columns: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    execution_ms: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_not_comparable(self) -> bool:
        return bool(self.metadata.get("not_comparable", False)) or (not self.success and "NO_BASELINE" in (self.error or ""))


class SourceAdapter(ABC):
    """Abstract base class for source semantic model adapters (Fabric, Power BI, Mock)."""

    platform: str = "UNKNOWN"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}

    @abstractmethod
    def execute_metric(
        self,
        metric_name: str,
        slice_filters: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 60,
    ) -> SourceResult:
        """Execute a DAX metric evaluation on the source platform."""
        raise NotImplementedError
