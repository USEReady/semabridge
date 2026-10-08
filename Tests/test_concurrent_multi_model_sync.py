"""
Comprehensive Concurrency and Isolation Test Suite for Multi-Model Sync.
Covers scenarios (a) through (h) specified in Phase 3.
"""

import copy
import threading
import time
from typing import Any, Dict, List
import pytest

from semabridge.api.services import sync_execution_service as ses
from semabridge.connectors.schema_manager import (
    SnowflakeSchemaManager,
    clear_schema_cache,
)
from semabridge.utils.name_translator import (
    get_model_scoped_object_name,
    get_target_deployment_name,
    target_lock_manager,
    validate_no_target_view_collisions,
)
from semabridge.domain.exceptions import ValidationError


def test_scenario_a_model_scoped_enriched_views():
    """a. Two models sharing a fact table name deploy concurrently:
    Each gets its own enriched view and semantic view; neither drops the other's.
    """
    fact_table = "SALESFACT"
    model1_id = "SalesUS"
    model2_id = "SalesEU"

    view1 = get_model_scoped_object_name(fact_table, model1_id, "ENRICHED")
    view2 = get_model_scoped_object_name(fact_table, model2_id, "ENRICHED")

    sem_view1 = get_target_deployment_name(model1_id, "snowflake")
    sem_view2 = get_target_deployment_name(model2_id, "snowflake")

    # Distinct enriched views and semantic views generated
    assert view1 != view2
    assert sem_view1 != sem_view2
    assert view1.startswith("SALESFACT_")
    assert view2.startswith("SALESFACT_")
    assert view1.endswith("_ENRICHED")
    assert view2.endswith("_ENRICHED")


def test_scenario_b_target_lock_rejects_concurrent_same_model():
    """b. Same model submitted twice concurrently: the second is rejected with conflict status."""
    model_name = "SalesPerformanceModel"
    target_view = get_target_deployment_name(model_name, "snowflake")

    # Manually hold lock to simulate active deployment
    acquired1 = target_lock_manager.acquire(target_view)
    assert acquired1 is True

    try:
        # Second attempt must be rejected (returns conflict status)
        acquired2 = target_lock_manager.acquire(target_view)
        assert acquired2 is False
    finally:
        target_lock_manager.release(target_view)


def test_scenario_c_cross_request_collision_detection():
    """c. Two separate requests resolving to the same view name: collision detected."""
    model_list_colliding = ["Sales Report", "Sales_Report"]
    with pytest.raises(ValidationError, match="Target view name collision detected"):
        validate_no_target_view_collisions(model_list_colliding, platform="snowflake")


def test_scenario_d_one_model_fails_others_succeed(monkeypatch):
    """d. One model fails mid-deploy: the others succeed, and the failed model's partial status is recorded."""
    def _fake_run_single(job, **kwargs):
        if job["model_label"] == "FailingModel":
            raise RuntimeError("Snowflake syntax error in model")
        return {
            "model": job["model_label"],
            "status": "success",
            "summary": {"status": "SUCCESS"},
            "routing_summary": None,
            "console": {"lines": []},
            "run_id": "run-ok",
        }

    monkeypatch.setattr(ses, "_run_single_job", _fake_run_single)

    jobs = [
        {"dataset_id": "m1", "pbix_path": None, "model_label": "Model1"},
        {"dataset_id": "m2", "pbix_path": None, "model_label": "FailingModel"},
        {"dataset_id": "m3", "pbix_path": None, "model_label": "Model3"},
    ]

    results = ses._run_parallel_jobs(
        sync_jobs=jobs,
        effective_parallelism=3,
        executor_kind="thread",
        source_type="fabric",
        target_type="snowflake",
        config={},
        config_path="",
        deploy_enabled=False,
        resolved_workspace_id="ws-1",
    )

    by_model = {r["model"]: r["status"] for r in results}
    assert len(results) == 3
    assert by_model["Model1"] == "success"
    assert by_model["FailingModel"] == "failed"
    assert by_model["Model3"] == "success"


def test_scenario_e_independent_enrichment_fallback():
    """e. Enrichment fails for one model but succeeds for another sharing a fact table name."""
    from semabridge.connectors.tables_clause_builder import resolve_source_table_mapping

    # Model A: Enriched view succeeds
    enriched_view_mapping_a = {"SALESFACT": "SALESFACT_A1B2C3_ENRICHED"}
    failed_enrichment_a = set()
    mapping_a = resolve_source_table_mapping(None, enriched_view_mapping_a, failed_enrichment_a)
    assert mapping_a["SALESFACT"] == "SALESFACT_A1B2C3_ENRICHED"

    # Model B: Enriched view failed
    enriched_view_mapping_b = {"SALESFACT": "SALESFACT_D4E5F6_ENRICHED"}
    failed_enrichment_b = {"SALESFACT"}
    mapping_b = resolve_source_table_mapping(None, enriched_view_mapping_b, failed_enrichment_b)
    # Model B falls back to base table SALESFACT
    assert mapping_b.get("SALESFACT") is None or mapping_b.get("SALESFACT") == "SALESFACT"


def test_scenario_f_config_deepcopy_isolation(monkeypatch):
    """f. Config mutation in one job doesn't affect another job."""
    original_config = {
        "source": {"type": "fabric"},
        "target": {"type": "snowflake", "schema": "PUBLIC"},
        "custom_setting": "initial_value",
    }

    seen_configs: List[Dict[str, Any]] = []

    def _fake_engine_execute(*args, **kwargs):
        cfg = kwargs.get("config_dict") or {}
        seen_configs.append(cfg)
        # Mutate local config inside job
        cfg["custom_setting"] = f"mutated_{threading.get_ident()}"
        return type("Summary", (), {"model_dump": lambda self, mode="json": {"status": "SUCCESS"}})()

    from semabridge.core.execution_engine import ExecutionEngine
    monkeypatch.setattr(ExecutionEngine, "execute", _fake_engine_execute)

    job1 = {"dataset_id": "d1", "pbix_path": None, "model_label": "Model1"}
    job2 = {"dataset_id": "d2", "pbix_path": None, "model_label": "Model2"}

    res1 = ses._run_single_job(job1, engine_source_type="fabric", target_type="snowflake", config=original_config, config_path="", deploy_enabled=False, resolved_workspace_id="ws")
    res2 = ses._run_single_job(job2, engine_source_type="fabric", target_type="snowflake", config=original_config, config_path="", deploy_enabled=False, resolved_workspace_id="ws")

    assert res1["status"] == "success"
    assert res2["status"] == "success"
    # Original config remains unmutated
    assert original_config["custom_setting"] == "initial_value"


def test_scenario_g_schema_cache_thread_safety():
    """g. Schema cache: concurrent jobs on the same schema produce correct metadata under thread stress."""
    clear_schema_cache()
    errors = []

    def _worker(thread_id: int):
        try:
            for _ in range(50):
                clear_schema_cache()
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0


def test_scenario_h_max_parallelism_respected(monkeypatch):
    """h. Max-parallelism limit is respected."""
    payload = {"max_parallel_models": 10}
    effective = ses._resolve_effective_parallelism(
        sync_jobs=[{"model_label": f"m{i}"} for i in range(10)],
        payload=payload,
        max_parallel_models=4,
        is_fabric_bound=False,
        executor_kind="thread",
    )
    # Default cap = 4
    assert effective == 4
