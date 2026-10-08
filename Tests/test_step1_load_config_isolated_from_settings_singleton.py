from __future__ import annotations

from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.settings import get_settings


def test_step1_load_config_does_not_mutate_shared_settings_singleton():
    """get_settings() is @lru_cache()'d -- a genuine process-wide singleton
    (core/settings.py). _step1_load_config used to mutate it directly via
    object.__setattr__ for source/target/options, which is exactly the kind
    of shared state multi-file PBIX dispatch's concurrent jobs
    (ThreadPoolExecutor, up to DEFAULT_MAX_PARALLEL_MODELS=10 at once --
    sync_execution_service.py) race on: one job's Step 1 rebinding
    config.source out from under a sibling job's own in-flight run.
    _step1_load_config must hand back its own private copy so mutating it
    never touches the shared instance other threads/runs still hold.
    """
    engine = ExecutionEngine()
    singleton_before = get_settings()

    config = engine._step1_load_config(
        config_path=None,
        source="pbix",
        target=None,
        config_dict={"source": {"pbix_path": "a.pbix"}},
    )

    assert config is not singleton_before
    assert config.source.pbix_path == "a.pbix"
    # The shared singleton itself was never touched.
    assert get_settings() is singleton_before
    assert getattr(singleton_before, "source", None) is None


def test_step1_load_config_isolates_two_concurrent_calls_from_each_other():
    """Simulates two multi-file jobs racing through Step 1: each call's
    resulting config must own its own source namespace, not share one that
    a later call could still rebind underneath the earlier caller.
    """
    engine = ExecutionEngine()

    config_a = engine._step1_load_config(
        config_path=None, source="pbix", target=None,
        config_dict={"source": {"pbix_path": "fileA.pbix"}},
    )
    config_b = engine._step1_load_config(
        config_path=None, source="pbix", target=None,
        config_dict={"source": {"pbix_path": "fileB.pbix"}},
    )

    assert config_a is not config_b
    assert config_a.source is not config_b.source
    # config_b's Step 1 call must not have altered config_a's already-resolved path.
    assert config_a.source.pbix_path == "fileA.pbix"
    assert config_b.source.pbix_path == "fileB.pbix"
