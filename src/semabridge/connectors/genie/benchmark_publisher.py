"""Benchmark Question Publishing Engine for Genie Spaces."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from semabridge.connectors.genie.client import GenieClient
from semabridge.connectors.genie.models import BenchmarkQuestion, PublishResult

logger = logging.getLogger(__name__)


class GenieBenchmarkPublisher:
    """Publishes approved question-SQL pairs from BI Truth to a Genie Space."""

    def __init__(self, client: GenieClient) -> None:
        self.client = client

    def publish_pairs(
        self,
        space_id: str,
        pairs: List[Dict[str, Any]],
        dry_run: bool = False,
    ) -> PublishResult:
        """Publish verified question/SQL pairs as benchmarks.
        
        Args:
            space_id: The target Genie Space ID.
            pairs: List of verified question/SQL pairs (from BI Truth qualifying cases).
            dry_run: If True, validates without committing changes to Genie.
        """
        if not space_id:
            return PublishResult(
                success=False,
                space_id="",
                reason="Target Genie space_id is required.",
                status="refused",
            )

        if not pairs:
            return PublishResult(
                success=True,
                space_id=space_id,
                injected_count=0,
                status="success",
                reason="No qualifying benchmark pairs provided.",
            )

        # Format benchmark items
        benchmarks_payload: List[Dict[str, Any]] = []
        for p in pairs:
            q_text = str(p.get("question") or "").strip()
            sql_text = str(p.get("sql") or "").strip()
            if not q_text or not sql_text:
                continue

            benchmarks_payload.append({
                "id": str(p.get("case_id") or p.get("id") or ""),
                "question": q_text,
                "sql": sql_text,
                "metric_name": p.get("metric_name") or "",
                "expected_value": p.get("expected_value"),
            })

        if dry_run:
            return PublishResult(
                success=True,
                space_id=space_id,
                injected_count=len(benchmarks_payload),
                published=benchmarks_payload,
                status="dry_run",
                reason=f"Dry run: {len(benchmarks_payload)} benchmark questions ready for publication.",
            )

        try:
            self.client.publish_benchmarks(space_id, benchmarks_payload)
            return PublishResult(
                success=True,
                space_id=space_id,
                injected_count=len(benchmarks_payload),
                published=benchmarks_payload,
                status="success",
                reason=f"Successfully published {len(benchmarks_payload)} benchmarks to Genie Space {space_id}.",
            )
        except Exception as exc:
            logger.error("Failed to publish benchmarks to Genie Space %s: %s", space_id, exc)
            return PublishResult(
                success=False,
                space_id=space_id,
                injected_count=0,
                failed=benchmarks_payload,
                status="failed",
                reason=f"Genie API publish failed: {str(exc)}",
            )
