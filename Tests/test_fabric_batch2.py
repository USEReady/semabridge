"""Fabric batch 2: one test per item. Offline (no Fabric / Snowflake calls)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

DS = "11111111-2222-3333-4444-555555555555"


# ── 1. Fabric job rules: dataset_id/models conflict names the field; dedupe; cap ──
def test_fabric_jobs_conflict_dedupe_and_cap():
    from semabridge.api.services.sync_execution_service import MAX_BATCH_MODELS, _build_sync_jobs
    from semabridge.domain.exceptions import ValidationError

    base = {"target": {"type": "snowflake"}}
    kw = {"model_resolver": _echo_resolver}
    with pytest.raises(ValidationError, match=r"Clear source\.dataset_id .* or clear source\.models"):
        _build_sync_jobs({**base, "source": {"type": "fabric", "workspace_id": "ws-1", "dataset_id": "old-ds", "models": ["Sales", "Finance"]}}, **kw)
    jobs = _build_sync_jobs({**base, "source": {"type": "fabric", "workspace_id": "ws-1", "models": ["Sales", "sales", {"id": DS}, "Finance"]}}, **kw)[0]
    assert [j["dataset_id"] for j in jobs] == ["Sales", DS, "Finance"]
    same = _build_sync_jobs({**base, "source": {"type": "fabric", "workspace_id": "ws-1", "dataset_id": DS, "models": [DS]}}, **kw)[0]
    assert [j["dataset_id"] for j in same] == [DS]  # consistent config is not a conflict
    with pytest.raises(ValidationError, match=f"at most {MAX_BATCH_MODELS}"):
        _build_sync_jobs({**base, "source": {"type": "fabric", "workspace_id": "ws-1", "models": [f"M{i}" for i in range(MAX_BATCH_MODELS + 1)]}}, **kw)


# ── 2. Fabric source workspace: never "default"; .env fallback only when not strict ──
def test_fabric_source_workspace_has_no_default(caplog):
    from semabridge.api.services.sync_execution_service import _fabric_source_workspace
    from semabridge.domain.exceptions import ValidationError

    assert _fabric_source_workspace({}, {"workspace_id": "ws-src"}, "ws-env") == "ws-src"
    assert _fabric_source_workspace({"fabric": {"workspace_id": "ws-proj"}}, {}, "ws-env") == "ws-proj"
    with caplog.at_level(logging.WARNING):
        assert _fabric_source_workspace({}, {}, "ws-env") == "ws-env"
    assert "falling back to the server's FABRIC_WORKSPACE_ID" in caplog.text
    strict = {"behavior": {"features": {"fabric_strict_workspace": True}}}
    with pytest.raises(ValidationError, match=r"set source\.workspace_id"):
        _fabric_source_workspace(strict, {}, "ws-env")
    with pytest.raises(ValidationError, match=r"set source\.workspace_id"):
        _fabric_source_workspace({}, {}, "")  # was the literal "default"


# ── 3. Extraction resolves models by exact match only ────────────────────────
def test_ambiguous_display_name_is_an_error_naming_the_ids():
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.core.settings import FabricConfig
    from semabridge.sources.base import SourceConfigError, SourceNotFoundError

    ext = FabricExtractor(FabricConfig(workspace_id="ws"), access_token="t")
    ext.list_semantic_models = lambda: [
        {"id": "id-a", "displayName": "Sales"}, {"id": "id-b", "displayName": "sales"}, {"id": "id-c", "displayName": "Finance"},
    ]
    assert ext._resolve_model_id_exact(DS) == DS
    assert ext._resolve_model_id_exact("finance") == "id-c"
    with pytest.raises(SourceConfigError, match="matches 2 semantic models.*id-a, id-b"):
        ext._resolve_model_id_exact("Sales")
    with pytest.raises(SourceNotFoundError):
        ext._resolve_model_id_exact("Missing")


# ── 4. Row counts: COUNTROWS, batched, failures visible, never fact/dimension ──
def test_row_counts_batched_failures_logged_and_kept_out_of_classification(caplog):
    from semabridge.sources.base import SourceQueryError
    from semabridge.sources.fabric.stats import build_countrows_query, fabric_row_counts

    tmsl = {"model": {"tables": [
        {"name": "Sales"}, {"name": "Prod'uct"}, {"name": "LocalDateTable_1"}, {"name": "Calc", "calculationGroup": {}},
    ]}}
    queries = []

    class _Ext:
        def execute_dax_query_strict(self, ds, q):
            queries.append(q)
            if "Prod''uct" in q:
                raise SourceQueryError("DAX query failed (HTTP 400): table not found")
            return [{"[t]": "Sales", "[n]": 42}]

    with caplog.at_level(logging.WARNING):
        res = fabric_row_counts(_Ext(), DS, tmsl, batch_size=1)
    assert res.counts == {"Sales": 42}  # failed table absent, never reported as 0
    assert len(res.errors) == 1 and "table not found" in res.errors[0] and "row counts" in caplog.text
    assert len(queries) == 2  # auto-date and calculation-group tables are not counted
    assert "UNION(" in build_countrows_query(["A", "B"]) and "COUNTROWS('A')" in build_countrows_query(["A", "B"])

    # Extraction wiring: with the flag on, the classifier gets no row counts.
    src = Path(__file__).resolve().parents[1] / "src/semabridge/core/engine/extraction/fabric.py"
    text = src.read_text(encoding="utf-8")
    assert "context.source_row_counts = stats.counts" in text and "row_counts = {}" in text


# ── 5. Missing source type is a validation error at create/save ───────────────
def test_missing_source_type_rejected_at_create_and_save():
    from semabridge.api.controllers.projects_controller import _yaml_source_type
    from semabridge.api.services.pbix_source_validation import require_source_type
    from semabridge.domain.exceptions import ValidationError

    with pytest.raises(ValidationError, match=r"no source type: set source\.type"):
        require_source_type({}, where="new project")
    with pytest.raises(ValidationError, match="Unsupported source.type 'excel'"):
        require_source_type({"type": "excel"})
    assert require_source_type({"type": "Fabric"}) == "fabric"
    assert _yaml_source_type("source:\n  type: pbix\n") == "pbix"
    assert _yaml_source_type("source:\n  models: [a]\n") == ""  # -> caller raises, never defaults to fabric


# ── 6. Drift: a project with no matching domain checks nothing and says why ───
def test_drift_project_without_domain_returns_empty_with_warning(monkeypatch, caplog):
    from semabridge.governance import drift_service
    from semabridge.ontology.store import ontology_store

    monkeypatch.setattr(ontology_store, "domains_for", lambda pid: [])
    with caplog.at_level(logging.WARNING):
        out = drift_service.run_drift_check(project_id="proj-without-domains")
    assert out["events"] == [] and out["checked_metrics"] == 0
    assert "no matching ontology domain" in out["warnings"][0] and "no matching ontology domain" in caplog.text


# ── 7. Debug dump: opt-in only, connection details redacted ──────────────────
def test_debug_dump_is_opt_in_and_redacted(monkeypatch, tmp_path):
    import semabridge.connectors.fabric_extractor as fe
    from semabridge.sources.fabric.redact import redact_tmsl

    tmsl = {"model": {"tables": [{"name": "Sales", "partitions": [{"source": {"type": "m", "expression": [
        'let Source = Snowflake.Databases("acct.snowflakecomputing.com", "WH"),',
        '    T = Source{[Name="SALES_DB"]}[Data] in T']}}]}],
        "dataSources": [{"name": "ds", "connectionString": "Server=prod;Password=p@ss"}]}}
    red = json.dumps(redact_tmsl(tmsl))
    assert "acct.snowflakecomputing.com" not in red and "p@ss" not in red and '"WH"' not in red
    assert "SALES_DB" in red  # navigation/structure kept for debugging

    monkeypatch.chdir(tmp_path)
    resp = SimpleNamespace(status_code=200, json=lambda: {"definition": {"parts": []}}, headers={})
    ext = fe.FabricExtractor(fe.FabricConfig(workspace_id="ws"), access_token="t")
    ext._token_expires_at = time.time() + 3600
    monkeypatch.setattr(ext, "_request_with_auth_retry", lambda *a, **k: resp)
    monkeypatch.setattr(ext, "_parse_definition_response", lambda payload: tmsl)
    monkeypatch.setattr(ext, "resolve_model_id", lambda d: DS)
    monkeypatch.setattr(ext, "resolve_workspace_id", lambda w: "ws")
    monkeypatch.delenv("SEMABRIDGE_DEBUG_DUMPS", raising=False)
    ext.get_model_definition(DS)
    assert not list(tmp_path.rglob("raw_fabric_model.json"))
    monkeypatch.setenv("SEMABRIDGE_DEBUG_DUMPS", "1")
    ext.get_model_definition(DS)
    dumped = next(tmp_path.rglob("raw_fabric_model.json")).read_text(encoding="utf-8")
    assert "acct.snowflakecomputing.com" not in dumped and "<redacted>" in dumped


# ── 8. Run report names every Fabric model, per-model reports name their own ──
def test_run_report_lists_every_fabric_model():
    from semabridge.api.services.run_report_service import _describe_source, _describe_source_for_result

    cfg = "source:\n  type: fabric\n  workspace_id: ws-1\n  models: [Sales, Finance]\n"
    kind, labels = _describe_source(cfg, {"results": []})
    assert kind == "Microsoft Fabric" and labels == ["Fabric dataset Sales (workspace ws-1)", "Fabric dataset Finance (workspace ws-1)"]
    assert "(unknown dataset)" not in " ".join(labels)
    _, one = _describe_source_for_result({"model": "Finance", "dataset_id": DS}, cfg, {"source_type": "fabric"})
    assert one == [f"Fabric dataset {DS} (workspace ws-1)"]


# ── 9. UPSERT preview re-converts the extracted Fabric model ─────────────────
def test_fabric_upsert_preview_reconverts_instead_of_copying():
    from semabridge.core.engine.conversion.base import _upsert_delta_preview_model

    calls = {}
    converted = SimpleNamespace(name="reconverted")

    def _convert_fabric(shadow, ws, ds):
        calls["fabric"] = shadow.config_payload["mappings_overrides"]
        shadow.mapping_overrides_applied = True
        return converted

    engine = SimpleNamespace(
        _summary="S", _current_step=6,
        _convert_fabric_to_sml=_convert_fabric,
        _convert_pbix_to_sml=lambda *a: (_ for _ in ()).throw(AssertionError("pbix path")),
        _apply_mapping_overrides_from_config=lambda *a, **k: (_ for _ in ()).throw(AssertionError("overrides re-applied")),
        _normalize_relationships_for_target=lambda m: None,
    )
    ctx = SimpleNamespace(
        source_type="fabric", project_id="p", config_payload={}, config_path=None, mapping_overrides_applied=True,
        drop_ledger=None, source_format=SimpleNamespace(tmsl_definition={"model": {}}),
        sml_model=SimpleNamespace(model_copy=lambda deep: (_ for _ in ()).throw(AssertionError("copied, not re-converted"))),
    )
    out = _upsert_delta_preview_model(engine, ctx, {"mappings_overrides": [{"source_path": "metrics.A", "target_name": "B"}]})
    assert out is converted and calls["fabric"] == [{"source_path": "metrics.A", "target_name": "B"}]
    assert engine._summary == "S"  # restored


def _echo_resolver(refs, workspace_id, identity_id):
    """Test resolver (no network): every ref is its own model id."""
    from semabridge.sources.base import ModelIdentity
    from semabridge.sources.fabric.validation import FabricModelCheck

    by_ref, ids = {}, []
    for r in refs:
        ident = next((i for i in ids if i.model_id.lower() == r.lower()), None) or ModelIdentity("fabric", r, r, workspace_id)
        if ident not in ids:
            ids.append(ident)
        by_ref[r] = ident
    return FabricModelCheck(ids, [], by_ref)
