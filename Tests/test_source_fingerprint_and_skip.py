from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from semabridge.sources.fabric.fingerprint import (
    compute_source_fingerprint,
    compute_combined_fingerprint,
    get_fabric_fingerprint,
    get_pbix_fingerprint,
    get_project_config_fingerprint,
    get_semabridge_version_and_commit,
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


def test_combined_fingerprint_sensitivity():
    """Verify that changes to source, mapping overrides, ontology domains/versions,

    or semabridge version/commit each change the combined fingerprint.
    """
    base_src = "pbix-sha256:abcd1234"
    base_cfg = {
        "mapping_overrides": {"Sales": "FACT_SALES"},
        "ontology": {
            "domains": [{"domain": "finance", "version": "1.0.0"}],
        },
        "target": {"database": "SALES_DB"},
    }
    base_ver = "1.0.0@56025d5"

    fp_base = compute_combined_fingerprint(base_src, base_cfg, semabridge_version=base_ver)

    # 1. Source changes -> fingerprint changes
    fp_new_src = compute_combined_fingerprint("pbix-sha256:different", base_cfg, semabridge_version=base_ver)
    assert fp_new_src != fp_base

    # 2. Mapping overrides change -> fingerprint changes
    cfg_new_mappings = dict(base_cfg)
    cfg_new_mappings["mapping_overrides"] = {"Sales": "FACT_SALES_V2"}
    fp_new_mappings = compute_combined_fingerprint(base_src, cfg_new_mappings, semabridge_version=base_ver)
    assert fp_new_mappings != fp_base

    # 3. Attached ontology domain version changes -> fingerprint changes
    cfg_new_onto_ver = dict(base_cfg)
    cfg_new_onto_ver["ontology"] = {
        "domains": [{"domain": "finance", "version": "2.0.0"}],
    }
    fp_new_onto_ver = compute_combined_fingerprint(base_src, cfg_new_onto_ver, semabridge_version=base_ver)
    assert fp_new_onto_ver != fp_base

    # 4. New ontology domain attached -> fingerprint changes
    cfg_new_onto_domain = dict(base_cfg)
    cfg_new_onto_domain["ontology"] = {
        "domains": [
            {"domain": "finance", "version": "1.0.0"},
            {"domain": "marketing", "version": "1.0.0"},
        ],
    }
    fp_new_onto_domain = compute_combined_fingerprint(base_src, cfg_new_onto_domain, semabridge_version=base_ver)
    assert fp_new_onto_domain != fp_base

    # 5. SemaBridge version/commit changes -> fingerprint changes
    fp_new_ver = compute_combined_fingerprint(base_src, base_cfg, semabridge_version="1.0.1@abcdef0")
    assert fp_new_ver != fp_base

    # 6. Identical inputs -> identical fingerprint
    fp_identical = compute_combined_fingerprint(base_src, base_cfg, semabridge_version=base_ver)
    assert fp_identical == fp_base


@pytest.mark.asyncio
async def test_scheduler_skip_unchanged_logic():
    import yaml
    from semabridge.api.services.run_service import _perform_project_run_impl

    with tempfile.NamedTemporaryFile(suffix=".pbix", delete=False) as f:
        f.write(b"sample pbix data")
        temp_pbix = Path(f.name)

    try:
        raw_fp = get_pbix_fingerprint(temp_pbix)

        # Setup project config with skip_unchanged_sources enabled
        p_cfg_dict = {
            "project_id": "test_skip_proj",
            "source": {
                "type": "pbix",
                "path": temp_pbix.as_posix(),
            },
            "mapping_overrides": {"Sales": "FACT_SALES"},
            "ontology": {
                "domains": [{"domain": "finance", "version": "1.0.0"}],
            },
            "behavior": {
                "features": {
                    "skip_unchanged_sources": True,
                },
            },
        }
        p_cfg = yaml.safe_dump(p_cfg_dict)

        combined_fp = compute_combined_fingerprint(raw_fp, p_cfg_dict)

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
                {"id": "run-past", "status": "success", "source_fingerprint": combined_fp}
            ]
            mock_sync.return_value = {"status": "success", "summary": {}}

            # 1. Scheduled run should be skipped with "skipped, unchanged" when all components match
            res_scheduled = await _perform_project_run_impl(dict(run_rec), p_cfg, 0.0)
            assert res_scheduled["status"] == "skipped"
            assert res_scheduled["message"] == "skipped, unchanged"

            # 2. When mapping overrides change -> scheduled run executes in full
            p_cfg_modified = dict(p_cfg_dict)
            p_cfg_modified["mapping_overrides"] = {"Sales": "FACT_SALES_MODIFIED"}
            res_modified_mapping = await _perform_project_run_impl(dict(run_rec), yaml.safe_dump(p_cfg_modified), 0.0)
            assert res_modified_mapping["status"] == "success"

            # 3. Manual run must NEVER skip, even if fingerprint is identical
            manual_run = dict(run_rec)
            manual_run["schedule"] = "Manual"
            res_manual = await _perform_project_run_impl(manual_run, p_cfg, 0.0)
            assert res_manual["status"] == "success"
            assert mock_sync.called
    finally:
        temp_pbix.unlink(missing_ok=True)
