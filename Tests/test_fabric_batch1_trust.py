"""Fabric batch 1 (trust): one test per item.

Offline: no Fabric / Power BI / Snowflake call is made; HTTP and token
resolution are replaced at their module boundary.
"""

from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest


def _jwt(exp: float) -> str:
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{seg({'alg': 'none'})}.{seg({'exp': int(exp), 'tid': 't'})}.sig"


class _Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def _extractor(workspace_id="ws-guid"):
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.core.settings import FabricConfig

    ext = FabricExtractor(FabricConfig(workspace_id=workspace_id), access_token="tok")
    ext._token_expires_at = time.time() + 3600
    return ext


DS = "11111111-2222-3333-4444-555555555555"


# ── Item 1: sources/base + tokens ───────────────────────────────────────────
def test_jwt_expiry_reads_exp_and_raises_with_reason_for_non_jwt():
    from semabridge.sources.base import ModelIdentity
    from semabridge.sources.tokens import jwt_expiry

    assert jwt_expiry(_jwt(2_000_000_000)) == 2_000_000_000.0
    with pytest.raises(ValueError, match="not a JWT"):
        jwt_expiry("opaque-token")
    a = ModelIdentity("fabric", "ABC", "Sales", "WS")
    assert a.key == ModelIdentity("fabric", "abc", "other label", "ws").key


# ── Item 2: one credential resolver (extraction uses it) ────────────────────
def test_resolver_order_identity_account_interactive_default_and_raises(monkeypatch):
    import semabridge.auth.token_resolver as tr
    from semabridge.sources.base import SourceAuthError
    from semabridge.sources.fabric.credentials import resolve_fabric_credentials

    ident_tok, acct_tok, inter_tok = _jwt(time.time() + 900), _jwt(time.time() + 800), _jwt(time.time() + 700)
    monkeypatch.setattr(tr, "resolve_fabric_access_token", lambda h, i=None: ident_tok)
    store = SimpleNamespace(get_fabric_auth_method=lambda: "interactive", has_valid_token=lambda: True,
                            get_msal_token=lambda: {"access_token": inter_tok})

    c = resolve_fabric_credentials(identity_id="id-1", account_token=acct_tok, credential_manager=store)
    assert (c.origin, c.access_token) == ("identity", ident_tok)
    assert abs(c.expires_at - (time.time() + 900)) < 5  # real exp, not now+1800
    assert resolve_fabric_credentials(account_token=acct_tok, credential_manager=store).origin == "account"
    assert resolve_fabric_credentials(credential_manager=store).access_token == inter_tok
    none_store = SimpleNamespace(get_fabric_auth_method=lambda: "service_principal", has_valid_token=lambda: False,
                                 get_msal_token=lambda: None)
    assert resolve_fabric_credentials(credential_manager=none_store).origin == "extractor_default"

    def _boom(h, i=None):
        raise RuntimeError("refresh token revoked")

    monkeypatch.setattr(tr, "resolve_fabric_access_token", _boom)
    with pytest.raises(SourceAuthError, match="re-authenticate.*refresh token revoked"):
        resolve_fabric_credentials(identity_id="id-1", credential_manager=store)
    broken = SimpleNamespace(get_fabric_auth_method=lambda: (_ for _ in ()).throw(OSError("db locked")))
    with pytest.raises(SourceAuthError, match="credential store could not be read: db locked"):
        resolve_fabric_credentials(credential_manager=broken)


# ── Item 3: measure sync uses the project's identity, no default workspace ──
def test_measure_sync_uses_project_identity_and_requires_extracted_workspace(monkeypatch):
    import semabridge.auth.token_resolver as tr
    from semabridge.core.engine.deployment.snowflake import _sync_fabric_measures
    from semabridge.core.engine.exceptions import DeploymentError
    from semabridge.core.settings import FabricConfig

    ident_tok = _jwt(time.time() + 900)
    monkeypatch.setattr(tr, "resolve_fabric_access_token", lambda h, i=None: ident_tok if i == "id-proj" else "WRONG")
    seen = {}

    def _sync_all(sml, fabric_extractor, dataset_id):
        seen.update(token=fabric_extractor._access_token, ws=fabric_extractor.config.workspace_id, ds=dataset_id)
        return {"M": {"status": "success"}}

    ctx = SimpleNamespace(
        config=SimpleNamespace(fabric=FabricConfig(workspace_id="env-ws"), source=SimpleNamespace(identity_id="id-proj")),
        source_format=SimpleNamespace(workspace_id="model-ws", dataset_id=DS, dataset_name="Sales"),
        fabric_access_token=None, account_id=None, sml_model=object(),
    )
    _sync_fabric_measures(None, ctx, SimpleNamespace(sync_all_measures=_sync_all))
    assert seen == {"token": ident_tok, "ws": "model-ws", "ds": DS}

    ctx.source_format.workspace_id = ""
    with pytest.raises(DeploymentError, match="needs the workspace the model was extracted from"):
        _sync_fabric_measures(None, ctx, SimpleNamespace(sync_all_measures=_sync_all))


# ── Item 4: execute_dax_query_strict ─────────────────────────────────────────
def test_strict_dax_is_workspace_scoped_and_raises_instead_of_empty(monkeypatch):
    import semabridge.connectors.fabric_extractor as fe
    from semabridge.sources.base import SourceAuthError, SourceConfigError, SourceQueryError

    calls = []
    replies = iter([
        _Resp(200, {"results": [{"tables": [{"rows": [{"[v]": 1}]}]}]}),
        _Resp(200, {"results": [{"tables": [{"rows": []}]}]}),
        _Resp(400, "DatasetExecuteQueriesError: syntax"),
        _Resp(403, "forbidden"),
        _Resp(200, {"results": [{"error": {"code": "DAX", "message": "bad column"}}]}),
    ])

    def _req(method, url, **kw):
        calls.append(url)
        return next(replies)

    monkeypatch.setattr(fe.requests, "request", _req)
    ext = _extractor()
    assert ext.execute_dax_query_strict(DS, "EVALUATE {1}") == [{"[v]": 1}]
    assert calls[0].endswith(f"/v1.0/myorg/groups/ws-guid/datasets/{DS}/executeQueries")
    assert ext.execute_dax_query_strict(DS, "EVALUATE {}") == []  # genuinely no rows
    with pytest.raises(SourceQueryError, match="HTTP 400"):
        ext.execute_dax_query_strict(DS, "EVALUATE x")
    with pytest.raises(SourceAuthError, match="HTTP 403"):
        ext.execute_dax_query_strict(DS, "EVALUATE x")
    with pytest.raises(SourceQueryError, match="bad column"):
        ext.execute_dax_query_strict(DS, "EVALUATE x")
    with pytest.raises(SourceConfigError, match="no default workspace"):
        _extractor(workspace_id=None).execute_dax_query_strict(DS, "EVALUATE x")
    # lenient original is unchanged for existing callers
    monkeypatch.setattr(fe.requests, "post", lambda *a, **k: (_ for _ in ()).throw(fe.RequestException("down")))
    assert ext.execute_dax_query(DS, "EVALUATE x", silent=True) == []


# ── Item 5: measure sync records real failures (not "empty") ────────────────
def test_measure_sync_marks_failed_measures_with_reason(monkeypatch):
    import semabridge.converter.materialization_builder as mb
    import semabridge.converter.measure_triage as mt
    from semabridge.connectors.measure_sync import MeasureSynchronizer
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.sources.base import SourceQueryError
    from semabridge.utils.identifiers import IdentifierSanitizer

    monkeypatch.setattr(mt.MeasureTriage, "classify_all", lambda self, ms: {})
    monkeypatch.setattr(mb.MaterializationQueryBuilder, "build_query",
                        lambda self, **k: (_ for _ in ()).throw(ValueError("no batch")))

    class _Ext:
        def execute_measure_sync_query(self, dataset_id, measure_name, group_by_dimensions, strict=False, **k):
            if strict:
                raise SourceQueryError("DAX query failed (HTTP 400): Column 'Year' not found")
            return []  # what the lenient path used to return on the same failure

    metric = SimpleNamespace(unique_name="Total Units", sync_enabled=True, is_hidden=False,
                             group_by_dimensions=None, requires_time_intel=False, partition_dimension=None)
    sync = MeasureSynchronizer(SimpleNamespace(database="D", schema_name="S"), ConnectorBehavior(), IdentifierSanitizer())
    results = sync.sync_all_measures(SimpleNamespace(metrics=[metric], unique_name="M", label="M"), _Ext(), DS)
    assert results["Total Units"]["status"] == "failed"
    assert "Column 'Year' not found" in results["Total Units"]["error"]


# ── Item 6: Fabric projects certify against their own model ──────────────────
def test_fabric_project_dax_never_falls_back_to_local_desktop(monkeypatch):
    from fastapi import HTTPException

    import semabridge.api.routers.reconciliation_router as rr
    import semabridge.sources.fabric.dax_target as dt
    from semabridge.sources.base import SourceConfigError

    single = {"source": {"type": "fabric", "workspace_id": "ws-1", "identity_id": "id-1", "models": [DS]}}
    multi = {"source": {"type": "fabric", "workspace_id": "ws-1", "models": [DS, "Other Model"]}}
    configs = {"p-fab": single, "p-multi": multi, "p-pbix": {"source": {"type": "pbix"}}, "p-notype": {"source": {}}}
    monkeypatch.setattr(dt, "load_project_config", lambda pid: configs.get(pid))
    ran = {}

    def _run(target, query, creds=None):
        ran.update(ws=target.workspace_id, ds=target.dataset_id, identity=target.identity_id)
        return {"backend": "fabric_project", "columns": ["v"], "rows": [{"v": 1}], "metadata": {}}

    monkeypatch.setattr(dt, "run_fabric_dax", _run)
    monkeypatch.setattr(rr, "execute_dax", lambda *a, **k: (_ for _ in ()).throw(AssertionError("local auto-detect used")))

    out = rr.execute_dax_endpoint(rr.RunDaxRequest(query="EVALUATE {1}", project_id="p-fab"))
    assert out["backend"] == "fabric_project" and ran == {"ws": "ws-1", "ds": DS, "identity": "id-1"}

    with pytest.raises(HTTPException) as e:
        rr.execute_dax_endpoint(rr.RunDaxRequest(query="EVALUATE {1}", project_id="p-multi"))
    assert e.value.status_code == 400 and "pass dax_dataset_id" in e.value.detail
    with pytest.raises(SourceConfigError, match="no source.type"):
        dt.resolve_fabric_dax_target("p-notype")
    assert dt.resolve_fabric_dax_target("p-pbix") is None  # PBIX keeps its existing backend selection
    assert rr._fabric_project_dax_target("p-fab", "xmla", None) is None  # explicit backend respected
