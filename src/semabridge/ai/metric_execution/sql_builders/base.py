"""Base target SQL builder for metric comparison."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union


class TargetSQLBuilder(ABC):
    """Abstract target SQL builder for metrics."""

    platform: str = "UNKNOWN"

    @abstractmethod
    def build_metric_query(
        self,
        view: str,
        metric: str,
        slice_filters: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build target query for evaluating a metric."""
        raise NotImplementedError
