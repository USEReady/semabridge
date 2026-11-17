from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from semabridge.sources.fabric.fingerprint import (
    compute_source_fingerprint,
    get_fabric_fingerprint,
    get_pbix_fingerprint,
)


def test_fabric_fingerprint_with_last_modified():
    payload = {
        "id": "model-123",
        "name": "Sales",
        "lastModifiedDateTime": "2026-11-17T12:00:00Z",
        "definition": {"tables": [{"name": "Sales"}]},
    }
    fp = get_fabric_fingerprint(payload)
    assert fp == "fabric-mod:2026-11-17T12:00:00Z"


def test_fabric_fingerprint_fallback_definition_hash():
    payload1 = {
        "id": "model-123",
        "definition": {"tables": [{"name": "Sales"}]},
    }
    payload2 = {
        "id": "model-456",  # Different ID, same definition
        "definition": {"tables": [{"name": "Sales"}]},
    }
    payload3 = {
        "id": "model-123",
        "definition": {"tables": [{"name": "Sales"}, {"name": "Product"}]},
    }
    fp1 = get_fabric_fingerprint(payload1)
    fp2 = get_fabric_fingerprint(payload2)
    fp3 = get_fabric_fingerprint(payload3)

    assert fp1.startswith("fabric-sha256:")
    assert fp1 == fp2
    assert fp1 != fp3


def test_pbix_fingerprint():
    with tempfile.NamedTemporaryFile(suffix=".pbix", delete=False) as f:
        f.write(b"PBIX binary contents test")
        f_path = Path(f.name)

    try:
        fp1 = get_pbix_fingerprint(f_path)
        assert fp1.startswith("pbix-sha256:")
        # Same bytes -> identical hash
        fp2 = compute_source_fingerprint("pbix", str(f_path))
        assert fp1 == fp2
    finally:
        f_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_scheduler_skip_unchanged_logic():
    from semabridge.api.services.run_service import _perform_project_run_impl

    with tempfile.NamedTemporaryFile(suffix=".pbix", delete=False) as f:
        f.write(b"sample pbix data")
        temp_pbix = Path(f.name)

    try:
        from semabridge.sources.fabric.fingerprint import get_pbix_fingerprint
        test_fp = get_pbix_fingerprint(temp_pbix)

        # Setup project config with skip_unchanged_sources enabled
        p_cfg = f"""
project_id: test_skip_proj
source:
  type: pbix
  path: {temp_pbix.as_posix()}
behavior:
  features:
    skip_unchanged_sources: true
"""
        run_rec = {
            "id": "run-scheduled-1",
            "project_id": "test_skip_proj",
            "schedule": "Scheduled 0 2 * * *",
            "status": "queued",
        }

        from unittest.mock import AsyncMock, patch

        with patch("semabridge.api.services.run_service.list_project_runs") as mock_runs, \
             patch("semabridge.api.services.run_service._persist_run") as mock_persist, \
             patch("semabridge.api.services.run_service.save_project_run") as mock_save, \
             patch("semabridge.api.services.core_domain_service.sync_models", new_callable=AsyncMock) as mock_sync:
            mock_runs.return_value = [
                {"id": "run-past", "status": "success", "source_fingerprint": test_fp}
            ]
            mock_sync.return_value = {"status": "success", "summary": {}}

            # 1. Scheduled run should be skipped with "skipped, unchanged"
            res_scheduled = await _perform_project_run_impl(dict(run_rec), p_cfg, 0.0)
            assert res_scheduled["status"] == "skipped"
            assert res_scheduled["message"] == "skipped, unchanged"

            # 2. Manual run must NEVER skip, even if fingerprint is identical
            manual_run = dict(run_rec)
            manual_run["schedule"] = "Manual"
            res_manual = await _perform_project_run_impl(manual_run, p_cfg, 0.0)
            assert res_manual["status"] == "success"
            assert mock_sync.called
    finally:
        temp_pbix.unlink(missing_ok=True)
