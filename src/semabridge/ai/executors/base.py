"""Truth Executor Base Interface and QueryResult definition."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semabridge.ai.executors")


@dataclass
class QueryResult:
    rows: List[Dict[str, Any]] = field(default_factory=list)  # column name to value, never positional
    row_count: int = 0
    columns: List[str] = field(default_factory=list)
    error: Optional[str] = None  # a statement error, distinct from no rows
    executed: bool = False  # False means it never reached the warehouse


class TruthExecutor(ABC):
    platform: str = "UNKNOWN"

    def __init__(self, project_id: str, config: Any = None):
        self.project_id = project_id
        self.config = config

    @abstractmethod
    def open(self) -> None:
        """Establish one session/connection for the suite run."""
        raise NotImplementedError

    @abstractmethod
    def execute(self, sql: str, timeout: Optional[int] = None) -> QueryResult:
        """Execute a read query and return QueryResult."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Close connection/session resources."""
        raise NotImplementedError

    def __enter__(self) -> TruthExecutor:
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def get_executor(project_id: Optional[str], owner_id: Optional[str] = None) -> Optional[TruthExecutor]:
    """Resolve the platform from the project's stored warehouse config and return the handler.

    Returns None for a platform with no handler; the caller reports not comparable with
    'no executor for platform X', never an empty result.
    """
    if not project_id:
        return None

    from semabridge.ai.project_connection import _project_meta, resolve_project_warehouse_config
    from semabridge.governance.drift.adapters.registry import resolve_project_platform

    meta = _project_meta(project_id)
    platform = resolve_project_platform(meta)

    if platform in ("UNKNOWN", "NOT_SET", ""):
        tt = str(meta.get("target_type") or "").upper().strip()
        if tt in ("SNOWFLAKE", "DATABRICKS"):
            platform = tt
        else:
            return None

    if platform == "SNOWFLAKE":
        from semabridge.ai.executors.snowflake_executor import SnowflakeTruthExecutor
        cfg = resolve_project_warehouse_config(project_id, "SNOWFLAKE", owner_id=owner_id)
        if not cfg:
            return None
        return SnowflakeTruthExecutor(project_id, cfg)

    elif platform == "DATABRICKS":
        from semabridge.ai.executors.databricks_executor import DatabricksTruthExecutor
        cfg = resolve_project_warehouse_config(project_id, "DATABRICKS", owner_id=owner_id)
        if not cfg:
            return None
        return DatabricksTruthExecutor(project_id, cfg)

    return None
