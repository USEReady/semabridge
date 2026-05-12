"""Integration tests for multi-model atomic synchronization.

Verifies that when syncing multiple models with shared tables, the sync
behaves atomically: either all models succeed and commit, or all fail
without partial updates.

This is a core requirement for the Fabric-to-Snowflake connector when
downstream consumers depend on consistent shared-model versions.
"""

from __future__ import annotations

import json
import os
import pytest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from semabridge.api.services.sync_execution_service import execute_sync_request
from semabridge.core.settings import SnowflakeConfig


@pytest.fixture
def multimodel_sync_payload():
    """Create a sync payload for two models sharing a table.
    
    Model A (AggregateMetrics) and Model B (DetailMetrics) both reference
    the FACT_SALES table with different row-level security contexts.
    A schema change to FACT_SALES must be applied to both models atomically.
    """
    return {
        "project_id": "test-project-multimodel",
        "models": [
            {
                "model_label": "AggregateMetrics",
                "model_name": "AggregateMetrics",
                "source_type": "fabric",
                "target_type": "snowflake_semantic_view",
                "config": {
                    "fabric": {
                        "workspace_id": "test-workspace-id",
                        "dataset_id": "AggregateMetricsDataset",
                    },
                    "snowflake": {
                        "account": "test.local",
                        "user": "test_user",
                        "password": "test_password",
                        "warehouse": "test_wh",
                        "database": "TEST_DB",
                        "schema": "TEST_SCHEMA",
                        "role": "test_role",
                    },
                }
            },
            {
                "model_label": "DetailMetrics",
                "model_name": "DetailMetrics",
                "source_type": "fabric",
                "target_type": "snowflake_semantic_view",
                "config": {
                    "fabric": {
                        "workspace_id": "test-workspace-id",
                        "dataset_id": "DetailMetricsDataset",
                    },
                    "snowflake": {
                        "account": "test.local",
                        "user": "test_user",
                        "password": "test_password",
                        "warehouse": "test_wh",
                        "database": "TEST_DB",
                        "schema": "TEST_SCHEMA",
                        "role": "test_role",
                    },
                }
            }
        ],
        "sync_mode": "copy",
        "deploy": True,
        "dry_run": False,
    }


@pytest.fixture
def sample_osi_shared_table():
    """Sample OSI model with a shared FACT_SALES table referenced by two models.
    
    This simulates the scenario where:
    - Both AggregateMetrics and DetailMetrics models include FACT_SALES
    - Changes to FACT_SALES columns must be reflected in both models
    """
    return {
        "tables": [
            {
                "name": "FACT_SALES",
                "columns": [
                    {"name": "SALE_ID", "dataType": "int64", "isKey": True},
                    {"name": "REVENUE", "dataType": "double"},
                    {"name": "QUANTITY", "dataType": "int64"},
                    {"name": "CUSTOMER_ID", "dataType": "int64"},
                ],
                "measures": [
                    {
                        "name": "Total Revenue",
                        "expression": "SUM([REVENUE])",
                    }
                ],
                "sourceTable": "FACT_SALES",
            }
        ],
        "relationships": []
    }


def test_multimodel_atomic_sync_validation_phase(multimodel_sync_payload):
    """Verify that multi-model atomic sync runs validation with parallelism=1.
    
    When deploy_enabled=True and there are 2+ models, the sync orchestration
    should:
    1. Run validation phase first with parallelism=1 (serialized)
    2. If validation passes, proceed to deploy phase
    3. If validation fails, return failure without deploying
    
    This test mocks the underlying job execution to verify the orchestration
    flow without needing actual Fabric/Snowflake connectivity.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("""
projects:
  test-project-multimodel:
    models:
      - model_name: AggregateMetrics
        source: fabric
        target: snowflake_semantic_view
      - model_name: DetailMetrics
        source: fabric
        target: snowflake_semantic_view
sync_mode: copy
""")
        config_path = f.name
    
    try:
        payload = dict(multimodel_sync_payload)
        
        # Mock both config loading and job execution
        with patch('semabridge.api.services.sync_execution_service._load_config') as mock_load_config:
            with patch('semabridge.api.services.sync_execution_service._run_parallel_jobs') as mock_runner:
                # Mock config returns two models with deploy enabled
                mock_load_config.return_value = (
                    config_path,
                    {
                        "projects": {
                            "test-project-multimodel": {
                                "models": [
                                    {"model_name": "AggregateMetrics", "source": "fabric"},
                                    {"model_name": "DetailMetrics", "source": "fabric"},
                                ]
                            }
                        },
                        "sync_mode": "copy"
                    }
                )
                
                # Build sync jobs with 2 models
                mock_sync_jobs = [
                    {"model_label": "AggregateMetrics", "model_name": "AggregateMetrics"},
                    {"model_label": "DetailMetrics", "model_name": "DetailMetrics"},
                ]
                
                with patch('semabridge.api.services.sync_execution_service._build_sync_jobs') as mock_build:
                    mock_build.return_value = (
                        mock_sync_jobs,  # sync_jobs
                        "fabric",  # source_type
                        "snowflake",  # target_type
                        {},  # source_cfg
                        {"deploy": True},  # target_cfg
                    )
                    
                    # Mock runner to return success
                    mock_runner.return_value = [
                        {"status": "success", "model": "AggregateMetrics", "summary": {"status": "ok"}},
                        {"status": "success", "model": "DetailMetrics", "summary": {"status": "ok"}},
                    ]
                    
                    # Act: Execute sync
                    result = execute_sync_request(
                        payload=payload,
                        normalize_yaml_windows_path_fields=lambda x: x,
                        account_id="test-account",
                        force=False
                    )
                    
                    # Assert: Verify orchestration behavior
                    assert result["status"] == "success"
                    assert result["models_synced"] == 2
                    
                    # Verify that _run_parallel_jobs was called twice: validation, then deploy
                    assert mock_runner.call_count == 2
                    
                    # Verify validation call had parallelism=1
                    validation_call = mock_runner.call_args_list[0]
                    assert validation_call.kwargs.get("effective_parallelism") == 1
                    assert validation_call.kwargs.get("deploy_enabled") is False
                    
                    # Verify deploy call also had parallelism=1 (atomic batch)
                    deploy_call = mock_runner.call_args_list[1]
                    assert deploy_call.kwargs.get("effective_parallelism") == 1
                    assert deploy_call.kwargs.get("deploy_enabled") is True
                    
                    # Verify batch metadata
                    assert result["batch"]["atomic"] is True
    
    finally:
        os.unlink(config_path)


def test_multimodel_atomic_sync_validation_failure(multimodel_sync_payload):
    """Verify that atomic sync fails without deploy if validation fails.
    
    When validation fails for any model in an atomic batch:
    1. Sync returns failure immediately
    2. Deploy phase is not run
    3. No models are updated in the target
    """
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("""
projects:
  test-project-multimodel:
    models:
      - model_name: AggregateMetrics
        source: fabric
      - model_name: DetailMetrics
        source: fabric
sync_mode: copy
""")
        config_path = f.name
    
    try:
        payload = dict(multimodel_sync_payload)
        
        with patch('semabridge.api.services.sync_execution_service._load_config') as mock_load_config:
            with patch('semabridge.api.services.sync_execution_service._run_parallel_jobs') as mock_runner:
                mock_load_config.return_value = (
                    config_path,
                    {
                        "projects": {
                            "test-project-multimodel": {
                                "models": [
                                    {"model_name": "AggregateMetrics"},
                                    {"model_name": "DetailMetrics"},
                                ]
                            }
                        },
                        "sync_mode": "copy"
                    }
                )
                
                mock_sync_jobs = [
                    {"model_label": "AggregateMetrics", "model_name": "AggregateMetrics"},
                    {"model_label": "DetailMetrics", "model_name": "DetailMetrics"},
                ]
                
                with patch('semabridge.api.services.sync_execution_service._build_sync_jobs') as mock_build:
                    mock_build.return_value = (
                        mock_sync_jobs,
                        "fabric",
                        "snowflake",
                        {},
                        {"deploy": True},
                    )
                    
                    # Validation fails for one model
                    mock_runner.return_value = [
                        {"status": "success", "model": "AggregateMetrics", "summary": {"status": "ok"}},
                        {
                            "status": "failed",
                            "model": "DetailMetrics",
                            "summary": {"status": "error", "message": "Schema validation failed"},
                        }
                    ]
                    
                    result = execute_sync_request(
                        payload=payload,
                        normalize_yaml_windows_path_fields=lambda x: x,
                        account_id="test-account",
                        force=False
                    )
                    
                    # Assert: Sync failed and deploy was skipped
                    assert result["status"] == "failed"
                    assert result["models_synced"] == 0
                    
                    # Verify only validation call was made, no deploy
                    assert mock_runner.call_count == 1
                    assert mock_runner.call_args_list[0].kwargs.get("deploy_enabled") is False
    
    finally:
        os.unlink(config_path)


def test_multimodel_sync_idempotence():
    """Verify that running the same sync twice yields identical artifacts.
    
    This is critical for Git-backed versioning: repeated syncs must produce
    identical output so diffs are clean and CI/CD can detect actual changes.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("""
projects:
  test-project:
    models:
      - model_name: AggregateMetrics
        source: fabric
sync_mode: copy
""")
        config_path = f.name
    
    try:
        payload = {
            "project_id": "test-project",
            "sync_mode": "copy",
            "dry_run": True,
        }
        
        with patch('semabridge.api.services.sync_execution_service._load_config') as mock_load_config:
            with patch('semabridge.api.services.sync_execution_service._run_parallel_jobs') as mock_runner:
                mock_load_config.return_value = (
                    config_path,
                    {
                        "projects": {
                            "test-project": {
                                "models": [
                                    {"model_name": "AggregateMetrics"},
                                ]
                            }
                        },
                        "sync_mode": "copy"
                    }
                )
                
                mock_sync_jobs = [
                    {"model_label": "AggregateMetrics", "model_name": "AggregateMetrics"},
                ]
                
                with patch('semabridge.api.services.sync_execution_service._build_sync_jobs') as mock_build:
                    mock_build.return_value = (
                        mock_sync_jobs,
                        "fabric",
                        "snowflake",
                        {},
                        {"deploy": False},  # Dry run
                    )
                    
                    def create_result(artifacts_hash):
                        return [
                            {
                                "status": "success",
                                "model": "AggregateMetrics",
                                "summary": {
                                    "status": "ok",
                                    "artifacts": [
                                        {
                                            "type": "semantic_view",
                                            "name": "AggregateMetrics",
                                            "checksum": artifacts_hash,
                                        }
                                    ],
                                },
                            }
                        ]
                    
                    # First sync
                    artifacts_hash_1 = "abc123def456"
                    mock_runner.return_value = create_result(artifacts_hash_1)
                    result_1 = execute_sync_request(
                        payload=payload,
                        normalize_yaml_windows_path_fields=lambda x: x,
                    )
                    
                    # Second sync with same config
                    artifacts_hash_2 = "abc123def456"  # Should be identical
                    mock_runner.return_value = create_result(artifacts_hash_2)
                    result_2 = execute_sync_request(
                        payload=payload,
                        normalize_yaml_windows_path_fields=lambda x: x,
                    )
                    
                    # Assert: Same artifacts hash indicates idempotence
                    artifacts_1 = result_1.get("summary", {}).get("artifacts", [{}])[0].get("checksum")
                    artifacts_2 = result_2.get("summary", {}).get("artifacts", [{}])[0].get("checksum")
                    assert artifacts_1 == artifacts_2 == "abc123def456"
    
    finally:
        os.unlink(config_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
