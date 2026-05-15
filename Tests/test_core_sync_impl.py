from __future__ import annotations

import asyncio

import pytest

from semabridge.api.services import core_sync_impl as csi


def test_sync_models_offloads_blocking_execution(monkeypatch):
    captured = {}

    def fake_execute_sync_request(payload, normalizer, account_id=None, force=False):
        captured["payload"] = payload
        captured["normalizer"] = normalizer
        captured["account_id"] = account_id
        captured["force"] = force
        return {"status": "success"}

    async def fake_to_thread(func, *args, **kwargs):
        captured["to_thread_func"] = func
        captured["to_thread_args"] = args
        captured["to_thread_kwargs"] = kwargs
        return func(*args, **kwargs)

    monkeypatch.setattr(csi, "execute_sync_request", fake_execute_sync_request)
    monkeypatch.setattr(csi.asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(csi.sync_models({"project_id": "project-1", "account_id": "acct-1", "force": True}))

    assert result == {"status": "success"}
    assert captured["to_thread_func"] is fake_execute_sync_request
    assert captured["payload"] == {"project_id": "project-1", "account_id": "acct-1", "force": True}
    assert captured["account_id"] == "acct-1"
    assert captured["force"] is True