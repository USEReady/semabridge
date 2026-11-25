"""Phase 3: the sync readiness gate blocks on a failed dry run, never falls
back to stale mappings, reuses a clean result for identical content, and a
scheduled run of an unchanged source is skipped before the gate."""
import asyncio

import pytest
from fastapi import HTTPException

from semabridge.api.services import mapping_service as ms
from semabridge.api.services import run_service as rs
from semabridge.repository import state_store


@pytest.fixture
def gate(monkeypatch):
    """_assert_sync_ready with a controllable auto-map and fingerprint."""
    state_store.ensure_table()
    state_store.apply_changes([("readiness", "p1", None)])
    calls = {"auto_map": 0}
    result = {"value": {"project_id": "p1", "entity_mappings": [], "dry_run_status": "success", "dry_run_errors": []}}

    async def fake_auto_map(payload):
        calls["auto_map"] += 1
        calls["payload"] = payload
        return result["value"]

    import sys
    pri = sys.modules.get("semabridge.api.services.project_runs_impl")
    if pri is not None:
        monkeypatch.setattr(pri, "auto_map_compat", fake_auto_map, raising=False)
    monkeypatch.setattr(ms, "auto_map_compat", fake_auto_map)
    monkeypatch.setattr(rs, "_project_fingerprint", lambda pid, cfg: "fp-A")
    monkeypatch.setattr(rs, "_compat_parse_project_cfg_dict", lambda *a, **k: {"source": {"type": "pbix"}})
    monkeypatch.setattr("semabridge.utils.project_models.is_multi_mode", lambda cfg: False)
    yield calls, result
    state_store.apply_changes([("readiness", "p1", None)])


def _run(coro):
    return asyncio.run(coro)


def test_gate_asks_auto_map_for_gate_mode(gate):
    calls, _ = gate
    _run(rs._assert_sync_ready("p1", "cfg"))
    assert calls["payload"]["gate"] is True


def test_failed_dry_run_blocks_the_sync(gate):
    calls, result = gate
    result["value"] = {**result["value"], "dry_run_status": "failed", "dry_run_errors": ["Sales: table missing"]}
    with pytest.raises(HTTPException) as exc:
        _run(rs._assert_sync_ready("p1", "cfg"))
    assert exc.value.status_code == 409
    assert exc.value.detail["blockers"][0]["validation_code"] == "DRY_RUN_FAILED"
    assert "Sales: table missing" in exc.value.detail["message"]


def test_clean_result_is_reused_for_identical_content(gate, monkeypatch):
    calls, _ = gate
    _run(rs._assert_sync_ready("p1", "cfg"))
    _run(rs._assert_sync_ready("p1", "cfg"))
    assert calls["auto_map"] == 1  # second sync reused the stored result

    monkeypatch.setattr(rs, "_project_fingerprint", lambda pid, cfg: "fp-B")
    _run(rs._assert_sync_ready("p1", "cfg"))
    assert calls["auto_map"] == 2  # content changed -> dry run again


def test_blocked_or_failed_results_are_not_reused(gate):
    calls, result = gate
    _run(rs._assert_sync_ready("p1", "cfg"))
    rs._forget_readiness("p1")
    result["value"] = {**result["value"], "dry_run_status": "failed"}
    with pytest.raises(HTTPException):
        _run(rs._assert_sync_ready("p1", "cfg"))
    with pytest.raises(HTTPException):
        _run(rs._assert_sync_ready("p1", "cfg"))
    assert calls["auto_map"] == 3


def test_no_fingerprint_means_no_reuse(gate, monkeypatch):
    calls, _ = gate
    monkeypatch.setattr(rs, "_project_fingerprint", lambda pid, cfg: None)
    _run(rs._assert_sync_ready("p1", "cfg"))
    _run(rs._assert_sync_ready("p1", "cfg"))
    assert calls["auto_map"] == 2


def test_force_skips_the_gate(gate):
    calls, _ = gate
    assert _run(rs._assert_sync_ready("p1", "cfg", force=True)) is None
    assert calls["auto_map"] == 0


@pytest.mark.asyncio
async def test_auto_map_in_gate_mode_raises_instead_of_falling_back(monkeypatch):
    from semabridge.api.services import core_domain_service
    from semabridge.domain.exceptions import ExternalServiceError

    async def failing(payload):
        raise RuntimeError("source unreachable")

    monkeypatch.setattr(core_domain_service, "sync_models", failing)
    monkeypatch.setattr(ms, "_compat_mappings", {"m1": {"project_id": "p-gate"}})
    with pytest.raises(ExternalServiceError, match="Dry run before sync failed"):
        await ms.auto_map_compat({"project_id": "p-gate", "dry_run": True, "gate": True})


def test_dry_run_status_helpers():
    assert ms._dry_run_status({}) is None
    assert ms._dry_run_status({"status": "success"}) == "success"
    assert ms._dry_run_status({"status": "error"}) == "failed"
    errs = ms._dry_run_errors({"results": [{"model": "Sales", "summary": {"errors": [{"message": "boom"}]}},
                                           {"status": "failed", "error": "no tables"}]})
    assert errs == ["Sales: boom", "no tables"]


def test_scheduled_unchanged_source_is_skipped_before_the_gate(monkeypatch):
    monkeypatch.setattr(rs, "_compat_parse_project_cfg_dict",
                        lambda *a, **k: {"behavior": {"features": {"skip_unchanged_sources": True}}})
    monkeypatch.setattr(rs, "_project_fingerprint", lambda pid, cfg: "fp-same")
    monkeypatch.setattr(rs, "list_project_runs", lambda pid: [{"status": "success", "source_fingerprint": "fp-same"}])
    persisted = []
    monkeypatch.setattr(rs, "_persist_run", lambda run: persisted.append(run["status"]))
    run = {"project_id": "p1", "schedule": "Scheduled", "logs": []}
    assert rs._skip_unchanged_scheduled_run(run, "cfg", 0.0) is True
    assert run["status"] == "skipped" and persisted == ["skipped"]

    manual = {"project_id": "p1", "schedule": "Manual", "logs": []}
    assert rs._skip_unchanged_scheduled_run(manual, "cfg", 0.0) is False
