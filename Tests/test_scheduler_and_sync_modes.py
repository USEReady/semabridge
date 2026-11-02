"""Comprehensive Verification Tests for Copy, Upsert, and Scheduled Runs."""

import asyncio
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock

from semabridge.api.services.scheduler_service import SchedulerService
from semabridge.core.run_helpers import sync_mode_from_project_config
from semabridge.core.upsert_delta import (
    compute_delta,
    merge_upsert,
)
from semabridge.sml.models import (
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
)


# =============================================================================
# 1. Verification of "Copy" vs "Upsert" Modes
# =============================================================================

def test_sync_mode_parsing():
    """Verify that project configs correctly extract 'copy' and 'upsert' strategies."""
    cfg_copy = "options:\n  write_strategy: copy\n"
    cfg_upsert = "options:\n  write_strategy: upsert\n"
    cfg_default = "name: TestProject\n"

    assert sync_mode_from_project_config(cfg_copy) == "copy"
    assert sync_mode_from_project_config(cfg_upsert) == "upsert"
    assert (sync_mode_from_project_config(cfg_default) or "copy") == "copy"


def test_upsert_delta_merge_functionality():
    """Verify that Upsert merges modified items, adds new items, and preserves target-only items."""
    # Baseline deployed model (e.g., from prior COPY or UPSERT run)
    baseline_model = SMLModel(
        unique_name="TestModel",
        label="Test Model",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="id", data_type=DataType.STRING),
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="TotalSales", expression="SUM(Sales[amount])", dataset="Sales"),
            SMLMetric(unique_name="TargetOnlyMeasure", expression="COUNT(Sales[id])", dataset="Sales"),  # Target-only
        ],
    )

    # New source model (modified TotalSales, added NewMeasure, removed TargetOnlyMeasure from source)
    new_source_model = SMLModel(
        unique_name="TestModel",
        label="Test Model",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="id", data_type=DataType.STRING),
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="TotalSales", expression="SUM(Sales[amount]) * 1.1", dataset="Sales"),  # Modified
            SMLMetric(unique_name="NewMeasure", expression="AVG(Sales[amount])", dataset="Sales"),        # Added
        ],
    )

    # In UPSERT mode:
    baseline_dict = baseline_model.model_dump()
    delta = compute_delta(new_source_model, baseline_dict)
    merged_model = merge_upsert(new_source_model, baseline_dict)

    # 1. Delta correctly identifies additions, modifications, and removals
    metric_delta = delta["kinds"]["metrics"]
    added_names = [m["name"] for m in metric_delta["added"]]
    modified_names = [m["name"] for m in metric_delta["modified"]]
    removed_names = [m["name"] for m in metric_delta["removed"]]

    assert "NewMeasure" in added_names
    assert "TotalSales" in modified_names
    assert "TargetOnlyMeasure" in removed_names

    # 2. Merged model retains target-only items without duplicating
    merged_metric_names = [m.unique_name for m in merged_model.metrics]
    assert "TotalSales" in merged_metric_names
    assert "NewMeasure" in merged_metric_names
    assert "TargetOnlyMeasure" in merged_metric_names  # Preserved from baseline!

    # 3. Modified expression takes precedence from new source
    total_sales_m = next(m for m in merged_model.metrics if m.unique_name == "TotalSales")
    assert total_sales_m.expression == "SUM(Sales[amount]) * 1.1"


# =============================================================================
# 2. Verification of Scheduled Runs (SchedulerService)
# =============================================================================

def test_scheduler_lifecycle_and_cron():
    """Verify that SchedulerService initializes, schedules cron triggers, and calculates next run time."""
    async def _test():
        service = SchedulerService()
        service.start()
        assert service._scheduler is not None
        assert service._scheduler.running is True

        # Configure callback
        callback_mock = AsyncMock()
        cleared_mock = MagicMock()
        service.configure(callback_mock, cleared_mock)

        # Schedule a recurring cron job for project-123 (every Monday at 9 AM UTC)
        schedule_payload = {
            "schedule_type": "cron",
            "cron": "0 9 * * 1",
            "timezone": "UTC",
            "enabled": True,
        }

        result = service.save_project_schedule("project-123", schedule_payload)
        assert result["project_id"] == "project-123"
        assert result["enabled"] is True
        assert result["next_run_at"] is not None

        # Retrieve schedule
        retrieved = service.get_project_schedule("project-123")
        assert retrieved is not None
        assert retrieved["cron"] == "0 9 * * 1"
        assert retrieved["next_run_at"] is not None

        # List schedules
        all_schedules = service.list_schedules()
        assert any(s["project_id"] == "project-123" for s in all_schedules)

        # Delete schedule
        deleted = service.delete_project_schedule("project-123")
        assert deleted is not None
        assert service.get_project_schedule("project-123") is None
        cleared_mock.assert_called_with("project-123")

        service.shutdown()

    asyncio.run(_test())


def test_scheduler_one_time_run():
    """Verify that a one-time scheduled run registers a valid future trigger date."""
    async def _test():
        service = SchedulerService()
        service.start()

        # Schedule a future one-time execution in 2035
        future_time_iso = "2035-01-01T12:00:00Z"
        payload = {
            "schedule_type": "time",
            "scheduled_time": future_time_iso,
            "enabled": True,
        }

        res = service.save_project_schedule("proj-future", payload)
        assert res["project_id"] == "proj-future"
        assert res["next_run_at"] == future_time_iso

        service.shutdown()

    asyncio.run(_test())
