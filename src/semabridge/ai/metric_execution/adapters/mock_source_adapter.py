"""Mock Source Adapter backed by persisted baseline snapshot fixtures."""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from semabridge.ai.metric_execution.adapters.base import SourceAdapter, SourceResult
from semabridge.ai.metric_execution.adapters.dax_shared import build_dax_query, clean_measure_name
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class MockSourceAdapter(SourceAdapter):
    """Loads metric values from persisted baseline snapshot fixtures.
    
    CRITICAL: Never fabricates synthetic values. If a fixture is missing,
    it marks the result as NOT_COMPARABLE.
    """

    platform: str = "MOCK"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.fixtures_dir = Path(config.get("fixtures_dir") or "Tests/fixtures/baselines")
        self.project_id = config.get("project_id") or "default"
        self._fixtures_cache: Optional[Dict[str, Any]] = None

    def _load_fixtures(self) -> Dict[str, Any]:
        if self._fixtures_cache is not None:
            return self._fixtures_cache

        fixtures: Dict[str, Any] = {}
        # Try direct file paths or project fixtures
        paths = [
            self.fixtures_dir / f"{self.project_id}_baseline.json",
            self.fixtures_dir / "baseline_snapshots.json",
            Path("Tests/fixtures/drift/corp_sales_baseline.json"),
        ]
        # Also check custom fixtures map passed directly in config (useful for testing)
        if "fixtures" in self.config and isinstance(self.config["fixtures"], dict):
            fixtures.update(self.config["fixtures"])

        for p in paths:
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        fixtures.update(data)
                except Exception as exc:
                    logger.warning("Failed to load baseline fixture %s: %s", p, exc)

        self._fixtures_cache = fixtures
        return fixtures

    def execute_metric(
        self,
        metric_name: str,
        slice_filters: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 60,
    ) -> SourceResult:
        t0 = time.perf_counter()
        query_text = build_dax_query(metric_name, slice_filters)
        fixtures = self._load_fixtures()

        clean_name = clean_measure_name(metric_name)
        slice_key = ""
        if slice_filters:
            items = sorted(f"{k}={v}" for k, v in slice_filters.items())
            slice_key = f"__slice_{'__'.join(items)}"

        candidate_keys = [
            f"{clean_name}{slice_key}",
            clean_name,
            metric_name,
            f"{metric_name}{slice_key}",
        ]

        match_val = None
        found = False
        for k in candidate_keys:
            if k in fixtures:
                entry = fixtures[k]
                match_val = entry.get("value") if isinstance(entry, dict) else entry
                found = True
                break

        duration_ms = (time.perf_counter() - t0) * 1000

        if not found:
            return SourceResult(
                success=False,
                platform=self.platform,
                query_text=query_text,
                execution_ms=duration_ms,
                error=f"No persisted baseline fixture found for metric '{clean_name}'. Mock mode does not fabricate values.",
                metadata={"not_comparable": True, "reason": "MISSING_BASELINE_FIXTURE"},
            )

        return SourceResult(
            success=True,
            platform=self.platform,
            query_text=query_text,
            raw_value=match_val,
            columns=["Value"],
            rows=[{"Value": match_val}],
            execution_ms=duration_ms,
            metadata={"fixture_matched": True, "source": "persisted_snapshot"},
        )
