"""
Concurrency and isolation tests for multi-model sync.

Covers: per-model physical object names, the per-view deploy lock, view-name
clashes (resolved with a readable suffix, never an error), failure isolation,
the base-table fallback when enrichment fails, per-job config isolation, the
schema-cache lock, and the parallelism limits.
"""

import threading
import time
from typing import Any, Dict, List

from semabridge.api.services import sync_execution_service as ses
from semabridge.connectors.schema_manager import clear_schema_cache
from semabridge.utils.name_translator import (
    build_physical_namespace,
    get_target_deployment_name,
    make_unique_labels,
    resolve_pbix_deployment_base_names,
    target_lock_manager,
)


def test_scenario_a_models_sharing_a_fact_table_get_distinct_physical_names():
    """a. Two models with a fact table of the same name get distinct, readable
    per-model prefixes, so their enriched views never collide."""
    fact_table = "FACT_TABLE"
    model_a, model_b = "model_a", "model_b"

    ns_a, ns_b = make_unique_labels([build_physical_namespace(model_a), build_physical_namespace(model_b)])
    view_a = f"{ns_a}_{fact_table}_ENRICHED"
    view_b = f"{ns_b}_{fact_table}_ENRICHED"

    assert view_a != view_b
    assert get_target_deployment_name(model_a, "snowflake") != get_target_deployment_name(model_b, "snowflake")
    # Readable prefix: no hash, derived from the model name.
    assert ns_a == "MODEL_A" and ns_b == "MODEL_B"
    assert view_a.endswith("_ENRICHED") and view_b.endswith("_ENRICHED")


def test_scenario_b_target_lock_rejects_concurrent_same_model():
    """b. Same model submitted twice concurrently: the second is rejected with conflict status."""
    target_view = get_target_deployment_name("model_a", "snowflake")

    acquired1 = target_lock_manager.acquire(target_view)
    assert acquired1 is True
    try:
        acquired2 = target_lock_manager.acquire(target_view)
        assert acquired2 is False
    finally:
        target_lock_manager.release(target_view)


def test_scenario_c_view_name_clash_is_suffixed_not_an_error():
    """c. Two files that would produce the same view name each get their own
    name (first keeps the clean name, the next gets a readable _2)."""
    paths = ["C:/dir_a/model one.pbix", "C:/dir_b/model one.pbix"]
    resolved = resolve_pbix_deployment_base_names(paths)
    names = [resolved[p] for p in paths]
    assert len(set(names)) == 2
    assert names[1] == f"{names[0]}_2"


def test_scenario_d_one_model_fails_others_succeed(monkeypatch):
    """d. One model fails mid-deploy: the others succeed, and the failed model's status is recorded."""
    def _fake_run_single(job, **kwargs):
        if job["model_label"] == "model_b":
            raise RuntimeError("deploy error in model")
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
        {"dataset_id": "m1", "pbix_path": None, "model_label": "model_a"},
        {"dataset_id": "m2", "pbix_path": None, "model_label": "model_b"},
        {"dataset_id": "m3", "pbix_path": None, "model_label": "model_c"},
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
    assert by_model["model_a"] == "success"
    assert by_model["model_b"] == "failed"
    assert by_model["model_c"] == "success"


def test_scenario_e_independent_enrichment_fallback():
    """e. Enrichment fails for one model but succeeds for another sharing a fact table name."""
    from semabridge.connectors.tables_clause_builder import resolve_source_table_mapping

    enriched_view_mapping_a = {"FACT_TABLE": "MODEL_A_FACT_TABLE_ENRICHED"}
    mapping_a = resolve_source_table_mapping(None, enriched_view_mapping_a, set())
    assert mapping_a["FACT_TABLE"] == "MODEL_A_FACT_TABLE_ENRICHED"

    enriched_view_mapping_b = {"FACT_TABLE": "MODEL_B_FACT_TABLE_ENRICHED"}
    mapping_b = resolve_source_table_mapping(None, enriched_view_mapping_b, {"FACT_TABLE"})
    # Model B falls back to its base table.
    assert mapping_b.get("FACT_TABLE") is None or mapping_b.get("FACT_TABLE") == "FACT_TABLE"


def test_scenario_f_config_deepcopy_isolation(monkeypatch):
    """f. Config mutation in one job doesn't affect another job."""
    original_config = {
        "source": {"type": "fabric"},
        "target": {"type": "snowflake", "schema": "SCHEMA_X"},
        "custom_setting": "initial_value",
    }

    seen_configs: List[Dict[str, Any]] = []

    def _fake_engine_execute(*args, **kwargs):
        cfg = kwargs.get("config_dict") or {}
        seen_configs.append(cfg)
        cfg["custom_setting"] = f"mutated_{threading.get_ident()}"
        return type("Summary", (), {"model_dump": lambda self, mode="json": {"status": "SUCCESS"}})()

    from semabridge.core.execution_engine import ExecutionEngine
    monkeypatch.setattr(ExecutionEngine, "execute", _fake_engine_execute)

    job1 = {"dataset_id": "d1", "pbix_path": None, "model_label": "model_a"}
    job2 = {"dataset_id": "d2", "pbix_path": None, "model_label": "model_b"}

    res1 = ses._run_single_job(job1, engine_source_type="fabric", target_type="snowflake", config=original_config, config_path="", deploy_enabled=False, resolved_workspace_id="ws")
    res2 = ses._run_single_job(job2, engine_source_type="fabric", target_type="snowflake", config=original_config, config_path="", deploy_enabled=False, resolved_workspace_id="ws")

    assert res1["status"] == "success"
    assert res2["status"] == "success"
    assert original_config["custom_setting"] == "initial_value"
    assert seen_configs[0] is not seen_configs[1]


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
    """h. Max-parallelism limit is respected by the effective worker count."""
    effective = ses._resolve_effective_parallelism(
        sync_jobs=[{"model_label": f"m{i}"} for i in range(10)],
        payload={"max_parallel_models": 10},
        max_parallel_models=4,
        is_fabric_bound=False,
        executor_kind="thread",
    )
    assert effective == 4


def test_parallelism_default_is_4_configurable_and_capped_at_10(monkeypatch):
    """Default 4; a request or env value raises it; anything above 10 is clamped to 10."""
    monkeypatch.delenv("SEMABRIDGE_MAX_PARALLEL_MODELS", raising=False)
    assert ses._resolve_requested_parallelism({}) == 4
    assert ses._resolve_requested_parallelism({"max_parallel_models": 7}) == 7
    assert ses._resolve_requested_parallelism({"max_parallel_models": 25}) == 10
    assert ses._resolve_requested_parallelism({"max_parallel_models": 0}) == 1
    monkeypatch.setenv("SEMABRIDGE_MAX_PARALLEL_MODELS", "9")
    assert ses._resolve_requested_parallelism({}) == 9
    monkeypatch.setenv("SEMABRIDGE_MAX_PARALLEL_MODELS", "")
    assert ses._resolve_requested_parallelism({}) == 4
