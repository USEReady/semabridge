"""Dry-run pipeline: real engine verdict, plan + hashes, backend suggestions."""
from __future__ import annotations

import asyncio
import os
import sys
import types
from types import SimpleNamespace

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_dry_run_plan_pipeline.sqlite")
os.environ.setdefault("AUTH_ENABLED", "false")

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

import semabridge.api.controllers.mappings_controller as mc
import semabridge.api.services.core_domain_service as core_domain_service
import semabridge.api.services.project_shared as project_shared
import semabridge.utils.synonyms as synonyms_mod

SML = {
    "unique_name": "Sales",
    "datasets": [
        {"unique_name": "Customer", "columns": [{"unique_name": "Name", "data_type": "string"}]},
        {"unique_name": "Product", "columns": [
            {"unique_name": "Name", "data_type": "string"},
            {"unique_name": "Margin %", "data_type": "string"},
            {"unique_name": "Margin PCT", "data_type": "string", "type": "calculated"},
        ]},
    ],
    "metrics": [],
}


def _run(monkeypatch, tmp_path, *, result_status="success", errors=None, project_id="preview", project_cfg=None):
    pbix = tmp_path / ("0" * 32 + "_Sales.pbix")
    pbix.write_bytes(b"bytes")
    seen = {}

    async def _fake_sync_models(payload):
        seen["payload"] = payload
        return {"status": result_status, "results": [{
            "model": "Sales", "status": result_status,
            "summary": {"sml_snapshot_id": "snap", "errors": errors or []},
            "model_plan": {"view_name": "SALES_SEMANTIC", "physical_namespace": None,
                           "config_hash": "1" * 64, "overrides_hash": "2" * 64},
        }]}

    monkeypatch.setattr(core_domain_service, "sync_models", _fake_sync_models)
    monkeypatch.setattr(project_shared.db_manager, "get_snapshot", lambda sid: SimpleNamespace(sml_blob=SML))
    monkeypatch.setattr(mc, "_project_sync_settings", lambda pid: ("copy", dict(project_cfg or {})))
    monkeypatch.setattr(synonyms_mod, "load_synonym_overrides", lambda pid: seen.setdefault("synonyms_pid", pid) and {})
    out = asyncio.run(mc._run_dry_run_pipeline(
        project_id=project_id, request_user_id="u1", source_config={"type": "pbix"},
        target_config={"type": "snowflake"}, selected_sources=[str(pbix)],
    ))
    return out, seen, pbix


def test_engine_failure_makes_the_dry_run_fail(monkeypatch, tmp_path):
    out, _seen, _ = _run(monkeypatch, tmp_path, result_status="failed",
                         errors=[{"message": "Prefixed object name for 'X' would be 300 characters"}])
    assert out["success"] is False
    assert "300 characters" in out["error"]


def test_success_carries_plan_and_hashes(monkeypatch, tmp_path):
    import hashlib

    out, seen, pbix = _run(monkeypatch, tmp_path)
    assert out["success"] is True and out["error"] is None
    assert out["model_plan"]["view_name"] == "SALES_SEMANTIC"
    assert out["config_hash"] == "1" * 64
    assert out["content_hash"] == hashlib.sha256(b"bytes").hexdigest()
    assert seen["payload"]["scope_source_refs"] == []  # single wizard file: nothing to scope


def test_rows_carry_backend_suggestions_and_per_table_namespace(monkeypatch, tmp_path):
    out, _seen, _ = _run(monkeypatch, tmp_path)
    rows = {tuple(r["source_path"].split(".")[1::2]): r for r in out["entity_mappings"]}
    # Customer.Name vs Product.Name: different logical tables -> no collision.
    assert rows[("Customer", "Name")]["collision_detected"] is False
    assert rows[("Product", "Name")]["collision_detected"] is False
    pair = [rows[("Product", "Margin %")], rows[("Product", "Margin PCT")]]
    firsts = set()
    for r in pair:
        assert r["target_name"] == "MARGIN_PCT" and r["collision_detected"] is True
        assert r["resolution_suggestions"], r
        firsts.add(r["resolution_suggestions"][0])
    assert len(firsts) == 2


def test_existing_project_dry_run_uses_saved_config_and_real_synonyms(monkeypatch, tmp_path):
    cfg = {
        "source": {"type": "pbix", "mode": "multiple", "models": ["/old/" + "0" * 32 + "_Sales.pbix", "/old/Fin.pbix"],
                   "model_views": {"/old/" + "0" * 32 + "_Sales.pbix": "SALES", "/old/Fin.pbix": "FIN"}},
        "targets": [{"type": "snowflake", "database": "D", "schema": "S"}],
    }
    out, seen, pbix = _run(monkeypatch, tmp_path, project_id="proj-real", project_cfg=cfg)
    import yaml

    sent = yaml.safe_load(seen["payload"]["content"])
    assert sent["source"]["mode"] == "multiple"
    assert sent["source"]["models"][0] == str(pbix)
    assert sent["source"]["model_views"][str(pbix)] == "SALES"
    assert seen["payload"]["scope_source_refs"] == [str(pbix)]
    assert sent["targets"] == [{"type": "snowflake", "database": "D", "schema": "S"}]
    assert seen["synonyms_pid"] == "proj-real"
