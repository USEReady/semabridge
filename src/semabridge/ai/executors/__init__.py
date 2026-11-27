"""Truth Executor Layer for cross-platform BI Truth evaluation."""
from semabridge.ai.executors.base import QueryResult, TruthExecutor, get_executor
from semabridge.ai.executors.snowflake_executor import SnowflakeTruthExecutor
from semabridge.ai.executors.databricks_executor import DatabricksTruthExecutor

__all__ = [
    "QueryResult",
    "TruthExecutor",
    "get_executor",
    "SnowflakeTruthExecutor",
    "DatabricksTruthExecutor",
]
