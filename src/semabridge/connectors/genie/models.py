"""Pydantic and dataclass models for Databricks Genie Space integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class GenieSpace:
    """Represents a Databricks Genie Space."""
    space_id: str
    title: str
    description: str = ""
    warehouse_id: Optional[str] = None
    tables: List[str] = field(default_factory=list)
    metric_views: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    status: str = "ACTIVE"
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_dict(cls, data: Dict[str, Any]) -> GenieSpace:
        space_id = str(data.get("id") or data.get("space_id") or "")
        title = str(data.get("title") or data.get("name") or data.get("display_name") or space_id)
        description = str(data.get("description") or "")
        warehouse_id = data.get("warehouse_id") or data.get("sql_warehouse_id")
        
        # Tables and metric views
        tables = []
        for t in data.get("tables") or data.get("table_names") or []:
            if isinstance(t, str):
                tables.append(t)
            elif isinstance(t, dict):
                tbl_name = t.get("name") or t.get("full_name") or t.get("table_name")
                if tbl_name:
                    tables.append(tbl_name)

        metric_views = []
        for mv in data.get("metric_views") or []:
            if isinstance(mv, str):
                metric_views.append(mv)
            elif isinstance(mv, dict):
                mv_name = mv.get("name") or mv.get("view_name")
                if mv_name:
                    metric_views.append(mv_name)

        return cls(
            space_id=space_id,
            title=title,
            description=description,
            warehouse_id=warehouse_id,
            tables=tables,
            metric_views=metric_views,
            created_at=data.get("created_at") or data.get("create_time"),
            updated_at=data.get("updated_at") or data.get("update_time"),
            status=str(data.get("status") or "ACTIVE").upper(),
            raw_payload=data,
        )


@dataclass
class BenchmarkQuestion:
    """A verified benchmark question-SQL pair published to a Genie Space."""
    id: str
    question: str
    sql: str
    metric_name: Optional[str] = None
    status: str = "approved"
    expected_value: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    """Outcome of publishing benchmark questions to a Genie Space."""
    success: bool
    space_id: str
    injected_count: int = 0
    published: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)
    reason: Optional[str] = None
    status: str = "success"  # success | partial | refused | failed | dry_run
    published_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class EvaluationItemResult:
    """Evaluation result for one benchmark question in a Genie Space."""
    question_id: str
    question: str
    expected_sql: str
    generated_sql: Optional[str] = None
    status: str = "PASS"  # PASS | FAIL | ERROR | NEEDS_REVIEW
    verdict: str = "GOOD"  # GOOD | BAD | NEEDS_REVIEW | PASS | FAIL
    accuracy_match: bool = True
    variance_pct: Optional[float] = 0.0
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    run_id: Optional[str] = None
    case_id: Optional[str] = None


@dataclass
class EvaluationResult:
    """Outcome of evaluating benchmarks in a Genie Space."""
    evaluation_id: str
    space_id: str
    status: str = "SUCCESS"  # SUCCESS | FAILED | RUNNING | PARTIAL
    accuracy_score: float = 100.0
    total_benchmarks: int = 0
    passed_benchmarks: int = 0
    failed_benchmarks: int = 0
    results: List[EvaluationItemResult] = field(default_factory=list)
    duration_ms: float = 0.0
    error: Optional[str] = None
    run_id: Optional[str] = None
    case_id: Optional[str] = None
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class GenieHealthCheck:
    """Connectivity and permission status for Databricks Genie APIs."""
    connected: bool
    host: str
    spaces_count: int = 0
    has_write_scope: bool = False
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
