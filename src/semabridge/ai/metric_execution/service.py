"""Metric Comparison Service orchestrating real-time source-to-target execution."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import time
from typing import Any, Dict, List, Optional

from semabridge.ai.bi_truth_comparison import Comparison, Verdict, compare_metric_execution, DEFAULT_TOLERANCE_PCT, DEFAULT_FLOOR
from semabridge.ai.executors.base import get_executor
from semabridge.ai.metric_execution.adapters.base import SourceAdapter, SourceResult
from semabridge.ai.metric_execution.adapters.registry import get_source_adapter
from semabridge.ai.metric_execution.result_normalizer import normalize_metric_value
from semabridge.ai.metric_execution.sql_builders import get_target_sql_builder
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)
_EXECUTOR_POOL = ThreadPoolExecutor(max_workers=10, thread_name_prefix="metric_cmp")


class MetricComparisonService:
    """Orchestrates source DAX vs target SQL execution and comparison."""

    def __init__(self, db_session: Optional[Any] = None) -> None:
        self.db_session = db_session

    def resolve_tolerance(
        self,
        project_id: str,
        metric_name: str,
        requested_tolerance: Optional[float] = None,
    ) -> float:
        """Resolve tolerance percentage. Metric-level wins > project-level > default (0.01%)."""
        if requested_tolerance is not None:
            return float(requested_tolerance)

        # Check DB config if session available
        if self.db_session:
            try:
                from semabridge.repository.orm.ai_models import MetricComparisonConfigRow
                row = (
                    self.db_session.query(MetricComparisonConfigRow)
                    .filter(
                        MetricComparisonConfigRow.project_id == project_id,
                        MetricComparisonConfigRow.metric_name == metric_name,
                    )
                    .first()
                )
                if row and row.tolerance_pct is not None:
                    return float(row.tolerance_pct)

                proj_row = (
                    self.db_session.query(MetricComparisonConfigRow)
                    .filter(
                        MetricComparisonConfigRow.project_id == project_id,
                        MetricComparisonConfigRow.metric_name.is_(None),
                    )
                    .first()
                )
                if proj_row and proj_row.tolerance_pct is not None:
                    return float(proj_row.tolerance_pct)
            except Exception as exc:
                logger.debug("Could not resolve tolerance from DB: %s", exc)

        return DEFAULT_TOLERANCE_PCT

    def _execute_target(
        self,
        target_platform: str,
        target_view: str,
        metric_name: str,
        target_config: Dict[str, Any],
        slice_filters: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        """Execute target query against Snowflake or Databricks."""
        t0 = time.perf_counter()
        builder = get_target_sql_builder(target_platform)
        query = builder.build_metric_query(target_view, metric_name, slice_filters)

        try:
            executor = get_executor(target_platform, target_config)
            # Use execute_query from TruthExecutor
            cols, rows = executor.execute_query(query, timeout_seconds=timeout_seconds)
            duration_ms = (time.perf_counter() - t0) * 1000

            raw_val = None
            if rows:
                first_row = rows[0]
                if isinstance(first_row, dict):
                    raw_val = next(iter(first_row.values())) if first_row else None
                elif isinstance(first_row, (list, tuple)):
                    raw_val = first_row[0] if len(first_row) > 0 else None

            return {
                "success": True,
                "platform": target_platform,
                "query_text": query,
                "raw_value": raw_val,
                "columns": cols,
                "rows": rows,
                "execution_ms": duration_ms,
                "error": None,
            }
        except Exception as exc:
            return {
                "success": False,
                "platform": target_platform,
                "query_text": query,
                "raw_value": None,
                "columns": [],
                "rows": [],
                "execution_ms": (time.perf_counter() - t0) * 1000,
                "error": str(exc),
            }

    async def run_comparison_async(
        self,
        project_id: str,
        metric_name: str,
        source_platform: str,
        target_platform: str,
        target_view: str,
        source_config: Dict[str, Any],
        target_config: Dict[str, Any],
        slice_filters: Optional[Dict[str, Any]] = None,
        tolerance_pct: Optional[float] = None,
        floor: Optional[float] = None,
        null_policy: str = "STRICT_NULL",
        mock_mode: bool = False,
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        """Execute source and target queries concurrently and compute comparison."""
        loop = asyncio.get_running_loop()

        # 1. Resolve tolerance (metric-level wins > project-level > default)
        effective_tol = self.resolve_tolerance(project_id, metric_name, tolerance_pct)
        effective_floor = floor if floor is not None else DEFAULT_FLOOR

        # 2. Get source adapter
        src_adapter = get_source_adapter(source_platform, source_config, mock_mode=mock_mode)

        # 3. Concurrently execute source DAX and target SQL
        def run_source():
            return src_adapter.execute_metric(
                metric_name=metric_name,
                slice_filters=slice_filters,
                timeout_seconds=timeout_seconds,
            )

        def run_target():
            return self._execute_target(
                target_platform=target_platform,
                target_view=target_view,
                metric_name=metric_name,
                target_config=target_config,
                slice_filters=slice_filters,
                timeout_seconds=timeout_seconds,
            )

        src_future = loop.run_in_executor(_EXECUTOR_POOL, run_source)
        tgt_future = loop.run_in_executor(_EXECUTOR_POOL, run_target)

        src_res: SourceResult
        tgt_res: Dict[str, Any]
        src_res, tgt_res = await asyncio.gather(src_future, tgt_future)

        # 4. Normalize values (Decimal-safe ROUND(val, 4); COALESCE only if NULL_EQUALS_ZERO)
        norm_src = normalize_metric_value(src_res.raw_value, null_policy=null_policy)
        norm_tgt = normalize_metric_value(tgt_res["raw_value"], null_policy=null_policy)

        # 5. Evaluate comparison
        comp = compare_metric_execution(
            source_val=float(norm_src) if norm_src is not None else None,
            target_val=float(norm_tgt) if norm_tgt is not None else None,
            tolerance_pct=effective_tol,
            floor=effective_floor,
            null_policy=null_policy,
            source_error=src_res.error,
            target_error=tgt_res["error"],
            not_comparable=src_res.is_not_comparable,
        )

        result_payload = {
            "project_id": project_id,
            "metric_name": metric_name,
            "verdict": comp.verdict.value,
            "passed": comp.passed,
            "variance_pct": comp.variance_pct,
            "absolute_difference": comp.absolute_difference,
            "tolerance_pct": effective_tol,
            "floor": effective_floor,
            "used_absolute_rule": comp.used_absolute_rule,
            "reason": comp.reason,
            "null_policy": null_policy,
            "mock_mode": mock_mode,
            "source": {
                "platform": src_res.platform,
                "query": src_res.query_text,
                "raw_value": str(src_res.raw_value) if src_res.raw_value is not None else None,
                "normalized_value": str(norm_src) if norm_src is not None else None,
                "execution_ms": round(src_res.execution_ms, 2),
                "error": src_res.error,
            },
            "target": {
                "platform": target_platform,
                "view": target_view,
                "query": tgt_res["query_text"],
                "raw_value": str(tgt_res["raw_value"]) if tgt_res["raw_value"] is not None else None,
                "normalized_value": str(norm_tgt) if norm_tgt is not None else None,
                "execution_ms": round(tgt_res["execution_ms"], 2),
                "error": tgt_res["error"],
            },
            "slice_filters": slice_filters or {},
            "timestamp": time.time(),
        }

        # 6. Persist run record if session provided
        if self.db_session:
            try:
                from semabridge.repository.orm.ai_models import MetricComparisonRunRow
                import uuid
                run_row = MetricComparisonRunRow(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    metric_name=metric_name,
                    source_platform=src_res.platform,
                    target_platform=target_platform,
                    target_view=target_view,
                    source_query=src_res.query_text,
                    target_query=tgt_res["query_text"],
                    source_value=str(norm_src) if norm_src is not None else None,
                    target_value=str(norm_tgt) if norm_tgt is not None else None,
                    variance_pct=comp.variance_pct,
                    absolute_difference=comp.absolute_difference,
                    tolerance_pct=effective_tol,
                    verdict=comp.verdict.value,
                    reason=comp.reason,
                    execution_ms=src_res.execution_ms + tgt_res["execution_ms"],
                    mock_mode=mock_mode,
                )
                self.db_session.add(run_row)
                self.db_session.commit()
                result_payload["run_id"] = run_row.id
            except Exception as exc:
                logger.warning("Failed to persist MetricComparisonRunRow: %s", exc)
                try:
                    self.db_session.rollback()
                except Exception:
                    pass

        return result_payload

    def run_comparison(
        self,
        project_id: str,
        metric_name: str,
        source_platform: str,
        target_platform: str,
        target_view: str,
        source_config: Dict[str, Any],
        target_config: Dict[str, Any],
        slice_filters: Optional[Dict[str, Any]] = None,
        tolerance_pct: Optional[float] = None,
        floor: Optional[float] = None,
        null_policy: str = "STRICT_NULL",
        mock_mode: bool = False,
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        """Synchronous wrapper for run_comparison_async."""
        return asyncio.run(
            self.run_comparison_async(
                project_id=project_id,
                metric_name=metric_name,
                source_platform=source_platform,
                target_platform=target_platform,
                target_view=target_view,
                source_config=source_config,
                target_config=target_config,
                slice_filters=slice_filters,
                tolerance_pct=tolerance_pct,
                floor=floor,
                null_policy=null_policy,
                mock_mode=mock_mode,
                timeout_seconds=timeout_seconds,
            )
        )
