from __future__ import annotations

import asyncio

import pytest


def test_fabric_list_workspaces_returns_empty_list_on_network_error(monkeypatch):
    from semabridge.api.services import connection_domain_service as svc
    import httpx

    monkeypatch.setattr(svc, "_resolve_fabric_access_token", lambda bearer_token, identity_id=None: "token")

    class _FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise httpx.RequestError("network down", request=httpx.Request("GET", "https://api.fabric.microsoft.com/v1/workspaces"))

    monkeypatch.setattr(httpx, "AsyncClient", _FailingAsyncClient)

    payload = asyncio.run(svc.fabric_list_workspaces(identity_id="account-123"))

    assert payload == {"workspaces": []}